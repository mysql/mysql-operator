# Copyright (c) 2026, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is designed to work with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms,
# as designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have either included with
# the program or referenced in the documentation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301  USA
"""Exercise four-part product versions through chart and build interfaces."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("prefix", ["", "internal/"])
@pytest.mark.parametrize("server", ["26.10.0", "26.10.0.1", "9.7.4.1", "8.4.13.1"])
def test_cluster_chart_preserves_product_versions(prefix, server):
    result = subprocess.run([
        "helm", "template", "hotfix", str(ROOT / (prefix + "helm/mysql-innodbcluster")),
        "--set-string", "serverVersion=" + server,
        "--set-string", "router.version=26.10.0.1",
        "--set", "tls.useSelfSigned=true", "--set", "disableLookups=true",
        "--set-string", "credentials.root.password=render-only",
    ], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    cluster = next(d for d in yaml.safe_load_all(result.stdout)
                   if d and d.get("kind") == "InnoDBCluster")
    assert cluster["spec"]["version"] == server
    assert cluster["spec"]["router"]["version"] == "26.10.0.1"


@pytest.mark.parametrize("prefix", ["", "internal/"])
@pytest.mark.parametrize("version", ["8.0.27", "8.0.27.1", "8.0.29"])
def test_cluster_chart_enforces_version_policy(prefix, version):
    result = subprocess.run([
        "helm", "template", "hotfix", str(ROOT / (prefix + "helm/mysql-innodbcluster")),
        "--set-string", "serverVersion=" + version,
    ], capture_output=True, text=True)
    assert result.returncode != 0
    assert "It is not possible to use MySQL version" in result.stderr


@pytest.mark.parametrize("prefix", ["", "internal/"])
def test_operator_chart_preserves_four_part_image_tag(prefix, tmp_path):
    chart = tmp_path / "chart"
    shutil.copytree(ROOT / (prefix + "helm/mysql-operator"), chart)
    meta = yaml.safe_load((chart / "Chart.yaml").read_text())
    meta["appVersion"] = "26.10.0.1-2.3.2"
    (chart / "Chart.yaml").write_text(yaml.safe_dump(meta))
    result = subprocess.run(["helm", "template", "hotfix", str(chart), "--namespace", "mysql-operator"],
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    deployment = next(d for d in yaml.safe_load_all(result.stdout)
                      if d and d.get("kind") == "Deployment")
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"].endswith(
        ":26.10.0.1-2.3.2")


@pytest.mark.parametrize("version", ["26.10.0", "26.10.0.1"])
def test_build_keeps_release_family_alias(version, tmp_path):
    shutil.copy(ROOT / "build.sh", tmp_path)
    (tmp_path / "tag.sh").write_text("#!/bin/sh\necho " + version + "-2.3.2\n")
    (tmp_path / "tag.sh").chmod(0o755)
    (tmp_path / "docker").write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
    (tmp_path / "docker").chmod(0o755)
    result = subprocess.run(["bash", "build.sh", "-a", "arm64", "-t", "local/operator"],
                            cwd=tmp_path, env={**os.environ, "PATH": str(tmp_path) + ":" + os.environ["PATH"]},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "local/operator:26.10-arm64" in result.stdout.splitlines()


@pytest.mark.parametrize("shell_version", ["26.10.0.1", "9.7.4.1"])
def test_dockerfile_generator_preserves_independent_shell_version(shell_version, tmp_path):
    import sys

    shutil.copy(ROOT / "gen_dockerfile.sh", tmp_path)
    shutil.copytree(ROOT / "docker-build", tmp_path / "docker-build")
    env = os.environ.copy()
    if sys.platform == "darwin":
        # The generator uses GNU sed's in-place syntax; adapt only the test host.
        sed = tmp_path / "sed"
        sed.write_text('#!/bin/sh\nif [ "$1" = "-i" ]; then shift; exec /usr/bin/sed -i "" "$@"; fi\nexec /usr/bin/sed "$@"\n')
        sed.chmod(0o755)
        env["PATH"] = str(tmp_path) + ":" + env["PATH"]
    result = subprocess.run([
        "bash", "gen_dockerfile.sh", "http://repo.mysql.com", "python-deps",
        "3.13.12", shell_version, "mysql80-community-release", "mysql-tools-community", "arm64",
    ], cwd=tmp_path, env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    generated = (tmp_path / "Dockerfile").read_text()
    install = next(line for line in generated.splitlines() if "microdnf install" in line)
    assert "mysql-shell-" + shell_version in install.split()
