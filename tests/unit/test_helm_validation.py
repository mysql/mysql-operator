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
"""Validate chart rejection rules by rendering the real chart without a cluster."""

from pathlib import Path
import shutil
import subprocess

import pytest
import yaml


CHART = Path(__file__).resolve().parents[2] / "helm/mysql-operator"


@pytest.fixture
def render_chart(tmp_path):
    helm = shutil.which("helm")
    assert helm, "Helm is required for local chart validation tests"

    def render(values):
        values_file = tmp_path / "values.yaml"
        values_file.write_text(yaml.safe_dump(values), encoding="utf8")
        return subprocess.run(
            [helm, "template", "validation", str(CHART), "--namespace", "validation-ns",
             "--values", str(values_file)],
            capture_output=True, text=True, timeout=30,
        )

    return render


@pytest.mark.parametrize("value_key,label", [
    ("deploymentLabels", "app.kubernetes.io/name"),
    ("podLabels", "name"),
])
def test_chart_rejects_reserved_labels(render_chart, value_key, label):
    result = render_chart({"deployment": {value_key: {label: "override"}}})
    assert result.returncode != 0
    assert f"deployment.{value_key} contains chart-managed label keys: {label}" in result.stderr


@pytest.mark.parametrize("key,value", [
    ("affinity", {"nodeAffinity": {}}),
    ("nodeSelector", {"kubernetes.io/hostname": "worker-a"}),
])
def test_chart_rejects_conflicting_placement_values(render_chart, key, value):
    result = render_chart({key: value, "deployment": {key: value}})
    assert result.returncode != 0
    assert f"{key} and deployment.{key} cannot both be set" in result.stderr


def test_chart_rejects_multiple_standalone_replicas(render_chart):
    result = render_chart({"replicas": 2, "deployment": {"standalone": True}})
    assert result.returncode != 0
    assert "deployment.standalone=true requires replicas=1 (got 2)" in result.stderr


@pytest.mark.parametrize("namespace", ["team-*", "!kube-*", "qa-?"])
def test_chart_rejects_namespace_patterns(render_chart, namespace):
    result = render_chart({"deployment": {"standalone": True, "namespaces": [namespace]}})
    assert result.returncode != 0
    assert "supports explicit namespace names only" in result.stderr
    assert f"Remove pattern entries: {namespace}" in result.stderr


@pytest.mark.parametrize("extra_length", [0, 1])
def test_chart_enforces_deployment_name_length_boundary(render_chart, extra_length):
    namespace = "validation-ns"
    max_length = 53 - len(namespace)
    name = "a" * (max_length + extra_length)
    result = render_chart({"deployment": {"name": name}})
    if extra_length:
        assert result.returncode != 0
        assert f'but at most {max_length} are allowed in namespace "{namespace}"' in result.stderr
    else:
        assert result.returncode == 0, result.stderr
        documents = list(yaml.safe_load_all(result.stdout))
        deployment = next(doc for doc in documents if doc and doc.get("kind") == "Deployment")
        assert deployment["metadata"]["name"] == name
