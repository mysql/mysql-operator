# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import logging
import pathlib
import subprocess
import sys
import types

import pytest
import yaml


TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
SAMPLE_HELM_DEBUG_OUTPUT = """level=DEBUG msg="getting history for release" release=myoperator
Release "myoperator" does not exist. Installing it now.
NAME:
myoperator
USER-SUPPLIED VALUES:
deployment:
  name: mysql-operator
HOOKS:
MANIFEST:
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mysql-operator-sa
  namespace: operator-ns
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: mysql-operator
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql-operator
  namespace: operator-ns
NOTES:
post-install notes
"""

SAMPLE_HELM_GET_MANIFEST_OUTPUT = """---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mysql-operator-sa
  namespace: operator-ns
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: mysql-operator-binding
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql-operator
  namespace: operator-ns
"""


def _drop_modules(*module_names):
    for module_name in module_names:
        sys.modules.pop(module_name, None)


def _load_generated_values_from_argv(argv):
    values_index = argv.index("--values")
    values_path = pathlib.Path(argv[values_index + 1])
    return yaml.safe_load(values_path.read_text(encoding="utf8"))


def _fake_helm_run_command(
    recorded: dict,
    *,
    apply_output: str = "",
    manifest_output: str = SAMPLE_HELM_GET_MANIFEST_OUTPUT,
):
    def fake_run_command(argv, check=True, cwd=None):
        argv = list(argv)
        recorded.setdefault("argvs", []).append(argv)
        if argv[:3] == ["helm", "get", "manifest"]:
            recorded["get_manifest_argv"] = argv
            return types.SimpleNamespace(stdout=manifest_output)

        recorded["argv"] = argv
        if "--values" in argv:
            recorded["values"] = _load_generated_values_from_argv(argv)
        return types.SimpleNamespace(stdout=apply_output)

    return fake_run_command


def _write_chart(
    root: pathlib.Path,
    dirname: str,
    chart_name: str,
    *,
    app_version: str | None = None,
    version: str = "0.1.0",
) -> pathlib.Path:
    chart_dir = root / dirname
    chart_dir.mkdir(parents=True)
    chart_yaml = {
        "apiVersion": "v2",
        "name": chart_name,
        "version": version,
    }
    if app_version is not None:
        chart_yaml["appVersion"] = app_version
    (chart_dir / "Chart.yaml").write_text(
        yaml.safe_dump(chart_yaml, sort_keys=False),
        encoding="utf8",
    )
    return chart_dir


def _link_chart(
    root: pathlib.Path,
    link_name: str,
    target: str,
) -> pathlib.Path:
    chart_link = root / link_name
    chart_link.symlink_to(target)
    return chart_link


def _add_broken_symlink(
    root: pathlib.Path,
    link_name: str,
    target: str,
) -> pathlib.Path:
    broken_link = root / link_name
    broken_link.symlink_to(target)
    return broken_link


def _make_install_options(
    operator_helm_module,
    *,
    operator_values=None,
    use_chart_defaults=False,
):
    return operator_helm_module.HelmOperatorInstallOptions(
        namespace="operator-ns",
        release_name="myoperator",
        kube_context="kind-test",
        helm_package="mysql-operator",
        operator_values=operator_values,
        use_chart_defaults=use_chart_defaults,
    )


def _make_cluster_install_options(
    operator_helm_module,
    *,
    app_version="26.7.0-2.3.0",
    cluster_values=None,
):
    return operator_helm_module.HelmClusterInstallOptions(
        namespace="cluster-ns",
        release_name="mycluster",
        kube_context="kind-test",
        app_version=app_version,
        helm_package="mysql-innodbcluster",
        cluster_values=cluster_values,
    )


@pytest.fixture
def operator_helm_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules(
        "utils.helmutil",
        "utils.kutil",
        "utils.utils",
        "setup.config",
        "setup.defaults",
    )

    module = importlib.import_module("utils.helmutil")
    yield module

    _drop_modules(
        "utils.helmutil",
        "utils.kutil",
        "utils.utils",
        "setup.config",
        "setup.defaults",
    )


@pytest.fixture
def helmutil_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules("utils.helmutil", "setup.config", "setup.defaults")

    module = importlib.import_module("utils.helmutil")
    yield module

    _drop_modules("utils.helmutil", "setup.config", "setup.defaults")


def test_helm_repo_defaults_are_removed(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules("setup.defaults")

    defaults = importlib.import_module("setup.defaults")

    assert not hasattr(defaults, "HELM_REPO_HOST")
    assert not hasattr(defaults, "HELM_REPO_PORT")
    assert not hasattr(defaults, "HELM_REPO_ALIAS")


def test_resolve_install_with_helm_options_preserves_source_image_and_pull_policies(
    monkeypatch,
    operator_helm_module,
):
    source_operator_deployment = {
        "metadata": {
            "name": "mysql-operator",
            "annotations": {"meta.helm.sh/release-name": "saved-release"},
        },
        "spec": {
            "replicas": 2,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                            "image": (
                                "registry.example.com/team/custom-operator:"
                                "26.7.0-2.3.0@sha256:deadbeef"
                            ),
                            "imagePullPolicy": "Never",
                            "env": [
                                {
                                    "name": "MYSQL_OPERATOR_DEFAULT_REPOSITORY",
                                    "value": "registry.example.com/team",
                                },
                                {
                                    "name": "MYSQL_OPERATOR_IMAGE_PULL_POLICY",
                                    "value": "Always",
                                },
                                {
                                    "name": "MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN",
                                    "value": "cluster.internal",
                                },
                                {
                                    "name": "OPERATOR_NAMESPACES",
                                    "value": " ns1, ns2 , ns1 ",
                                },
                                {"name": "OPERATOR_STANDALONE", "value": "true"},
                                {"name": "MYSQL_OPERATOR_DEBUG", "value": "2"},
                            ],
                        }
                    ]
                }
            },
        },
    }

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_ns_labels",
        lambda namespace: {
            "purpose": "tests",
            "kubernetes.io/metadata.name": namespace,
        },
    )
    monkeypatch.setattr(operator_helm_module.g_ts_cfg, "k8s_context", "kind-test")

    options = operator_helm_module.resolve_install_with_helm_options(
        namespace="operator-ns",
        source_operator_namespace="operator-ns",
        source_operator_deployment=source_operator_deployment,
    )

    assert options.release_name == "saved-release"
    assert options.operator_registry == "registry.example.com"
    assert options.operator_repository == "team"
    assert options.operator_image_name == "custom-operator"
    assert options.operator_image_tag == "26.7.0-2.3.0"
    assert options.operator_image_digest == "sha256:deadbeef"
    assert options.operator_image_pull_policy == "Never"
    assert options.managed_images_pull_policy == "Always"
    assert options.default_registry == "registry.example.com"
    assert options.default_repository == "team"
    assert options.operator_namespaces == "ns1,ns2"
    assert options.k8s_domain == "cluster.internal"
    assert options.deployment_replicas == 2
    assert options.standalone is True
    assert options.debug_operator == 2
    assert options.namespace_labels == {"purpose": "tests"}
    assert options.app_version == "26.7.0-2.3.0"


def test_resolve_install_with_helm_options_uses_resident_image_source_without_source_env(
    monkeypatch,
    operator_helm_module,
):
    source_operator_deployment = {
        "metadata": {
            "name": "mysql-operator",
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                            "image": "registry.example.com/team/custom-operator:26.7.0-2.3.0",
                            "imagePullPolicy": "IfNotPresent",
                            "env": [],
                        }
                    ]
                }
            }
        },
    }

    monkeypatch.setattr(operator_helm_module.kutil, "get_ns_labels", lambda namespace: {})

    options = operator_helm_module.resolve_install_with_helm_options(
        namespace="operator-ns",
        source_operator_namespace="operator-ns",
        source_operator_deployment=source_operator_deployment,
    )

    assert options.operator_registry == "registry.example.com"
    assert options.operator_repository == "team"
    assert options.default_registry == "registry.example.com"
    assert options.default_repository == "team"
    assert options.app_version == "26.7.0-2.3.0"


def test_resolve_install_with_helm_options_prefers_resident_image_source_over_source_env(
    monkeypatch,
    operator_helm_module,
):
    source_operator_deployment = {
        "metadata": {
            "name": "mysql-operator",
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                            "image": "registry.example.com/team/custom-operator:26.7.0-2.3.0",
                            "imagePullPolicy": "IfNotPresent",
                            "env": [
                                {
                                    "name": "MYSQL_OPERATOR_DEFAULT_REPOSITORY",
                                    "value": "other-registry.example.com/other-team",
                                },
                            ],
                        }
                    ]
                }
            }
        },
    }

    monkeypatch.setattr(operator_helm_module.kutil, "get_ns_labels", lambda namespace: {})

    options = operator_helm_module.resolve_install_with_helm_options(
        namespace="operator-ns",
        source_operator_namespace="operator-ns",
        source_operator_deployment=source_operator_deployment,
    )

    assert options.operator_registry == "registry.example.com"
    assert options.operator_repository == "team"
    assert options.default_registry == "registry.example.com"
    assert options.default_repository == "team"
    assert options.app_version == "26.7.0-2.3.0"


def test_helm_operator_install_options_factory_returns_mutable_defaults(
    monkeypatch,
    operator_helm_module,
):
    source_operator_deployment = {
        "metadata": {
            "name": "mysql-operator",
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                            "image": "registry.example.com/team/custom-operator:26.7.0-2.3.0",
                            "imagePullPolicy": "IfNotPresent",
                            "env": [],
                        }
                    ]
                }
            }
        },
    }

    monkeypatch.setattr(operator_helm_module.kutil, "get_ns_labels", lambda namespace: {})
    monkeypatch.setattr(operator_helm_module.g_ts_cfg, "k8s_cluster_domain_alias", "cluster.alias")

    options = operator_helm_module.HelmOperatorInstallOptions.from_source(
        "operator-ns",
        source_operator_namespace="operator-ns",
        source_operator_deployment=source_operator_deployment,
    )

    assert options.k8s_domain == "cluster.alias"
    assert options.operator_values["edition"] == "community"
    assert options.operator_values["namespaceLabels"] == {}
    assert options.operator_values["image"]["registry"] == "registry.example.com"
    assert options.operator_values["image"]["repository"] == "team"
    assert options.operator_values["image"]["name"] == "custom-operator"
    assert options.operator_values["image"]["pullPolicy"] == "IfNotPresent"
    assert "pullSecrets" not in options.operator_values["image"]
    assert options.operator_values["image"]["tag"] == "26.7.0-2.3.0"
    assert options.operator_values["envs"]["imagesPullPolicy"] == "IfNotPresent"
    assert options.operator_values["envs"]["imagesDefaultRegistry"] == "registry.example.com"
    assert options.operator_values["envs"]["imagesDefaultRepository"] == "team"
    assert options.operator_values["envs"]["k8sClusterDomain"] == "cluster.alias"
    assert options.operator_values["replicas"] == 1
    assert options.operator_values["deployment"]["name"] == "mysql-operator"
    assert options.operator_values["deployment"]["standalone"] is False
    assert options.operator_values["deployment"]["namespaces"] == []
    assert options.operator_values["operatorDebug"] == 0
    assert options.helm_package == "mysql-operator"
    assert options.app_version == "26.7.0-2.3.0"

    options.operator_values["debugger"] = {"enabled": True}

    assert options.operator_values["debugger"] == {"enabled": True}


def test_get_helm_chart_path_accepts_direct_chart_root(tmp_path, helmutil_module):
    chart_dir = _write_chart(
        tmp_path,
        "mounted-chart",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-operator",
        source_path=str(chart_dir),
        app_version="26.7.0-2.3.0",
    ) == str(chart_dir)


def test_get_helm_chart_path_uses_root_chart_path_without_app_version(
    tmp_path,
    helmutil_module,
):
    _write_chart(tmp_path, "mysql-operator", "mysql-operator")

    assert helmutil_module.get_helm_chart_path(
        "mysql-operator",
        source_path=str(tmp_path),
    ) == str(tmp_path / "mysql-operator")


def test_get_helm_chart_path_rejects_missing_chart(tmp_path, helmutil_module):
    _write_chart(tmp_path, "mysql-innodbcluster", "mysql-innodbcluster")

    with pytest.raises(FileNotFoundError, match="mysql-operator"):
        helmutil_module.get_helm_chart_path(
            "mysql-operator",
            source_path=str(tmp_path),
        )


def test_get_helm_chart_path_rejects_direct_dir_with_wrong_chart_name(
    tmp_path,
    helmutil_module,
):
    _write_chart(tmp_path, "mysql-operator", "not-mysql-operator")

    with pytest.raises(FileNotFoundError, match="not-mysql-operator"):
        helmutil_module.get_helm_chart_path(
            "mysql-operator",
            source_path=str(tmp_path),
        )


def test_get_helm_chart_path_prefers_direct_chart_when_app_version_matches(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    _write_chart(
        tmp_path,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )
    _write_chart(
        tmp_path / "26.7.0-2.3.0",
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-operator",
        source_path=str(tmp_path),
        app_version="26.7.0-2.3.0",
    ) == str(tmp_path / "mysql-operator")


def test_get_helm_chart_path_uses_versioned_chart_when_current_shortcut_is_missing(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    version_root = tmp_path / "26.7.0-2.3.0"
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-operator",
        source_path=str(tmp_path),
        app_version="26.7.0-2.3.0",
    ) == str(version_root / "mysql-operator")


def test_get_helm_chart_path_uses_versioned_chart_when_current_shortcut_chart_has_broken_symlink(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    current_chart = _write_chart(
        tmp_path,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )
    _add_broken_symlink(
        current_chart,
        "mysql-operator",
        "26.7.0-2.3.0/mysql-operator",
    )

    version_root = tmp_path / "26.7.0-2.3.0"
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-operator",
        source_path=str(tmp_path),
        app_version="26.7.0-2.3.0",
    ) == str(version_root / "mysql-operator")


def test_get_helm_chart_path_rejects_current_chart_when_versioned_chart_has_broken_symlink(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    version_root = tmp_path / "26.7.0-2.3.0"
    version_root.mkdir()
    versioned_chart = _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )
    _add_broken_symlink(
        versioned_chart,
        "mysql-operator",
        "26.7.0-2.3.0/mysql-operator",
    )
    _link_chart(tmp_path, "mysql-operator", "26.7.0-2.3.0/mysql-operator")

    with pytest.raises(FileNotFoundError, match="broken symlink"):
        helmutil_module.get_helm_chart_path(
            "mysql-operator",
            source_path=str(tmp_path),
            app_version="26.7.0-2.3.0",
        )


def test_get_helm_chart_path_raises_when_direct_and_versioned_app_versions_mismatch(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    _write_chart(
        tmp_path,
        "mysql-operator",
        "mysql-operator",
        app_version="9.8.0-2.2.9",
    )
    version_root = tmp_path / "26.7.0-2.3.0"
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version="9.8.0-2.2.9",
    )

    with pytest.raises(FileNotFoundError, match="appVersion"):
        helmutil_module.get_helm_chart_path(
            "mysql-operator",
            source_path=str(tmp_path),
            app_version="26.7.0-2.3.0",
        )


def test_get_helm_chart_path_accepts_split_app_and_chart_versions(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    _write_chart(
        tmp_path,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-innodbcluster",
        source_path=str(tmp_path),
        app_version="26.7.0-2.3.0",
    ) == str(tmp_path / "mysql-innodbcluster")


def test_get_helm_chart_path_uses_only_requested_version_for_non_current_release(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    _write_chart(
        tmp_path,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )
    version_root = tmp_path / "8.4.7-2.1.9"
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version="8.4.7-2.1.9",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-operator",
        source_path=str(tmp_path),
        app_version="8.4.7-2.1.9",
    ) == str(version_root / "mysql-operator")


def test_get_helm_chart_path_uses_split_metadata_for_non_current_versioned_chart(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    version_root = tmp_path / "8.4.7-2.1.9"
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="8.4.7",
        version="2.1.9",
    )

    assert helmutil_module.get_helm_chart_path(
        "mysql-innodbcluster",
        source_path=str(tmp_path),
        app_version="8.4.7-2.1.9",
    ) == str(version_root / "mysql-innodbcluster")


def test_get_helm_chart_path_rejects_missing_requested_versioned_chart(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    _write_chart(
        tmp_path,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )

    with pytest.raises(FileNotFoundError, match="8.4.7-2.1.9"):
        helmutil_module.get_helm_chart_path(
            "mysql-operator",
            source_path=str(tmp_path),
            app_version="8.4.7-2.1.9",
        )


def test_get_missing_helm_chart_release_paths_reports_missing_historical_charts(
    tmp_path,
    helmutil_module,
):
    current_version = "26.7.0-2.3.0"
    historical_version = "8.4.7-2.1.9"
    helmutil_module.g_ts_cfg.operator_version_tag = current_version
    _write_chart(
        tmp_path,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        tmp_path,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )

    assert helmutil_module.get_missing_helm_chart_release_paths(
        ("mysql-operator", "mysql-innodbcluster"),
        (current_version, historical_version),
        source_path=str(tmp_path),
    ) == [
        str(tmp_path / historical_version / "mysql-operator"),
        str(tmp_path / historical_version / "mysql-innodbcluster"),
    ]


def test_get_missing_helm_chart_release_paths_rejects_invalid_historical_chart(
    tmp_path,
    helmutil_module,
):
    historical_version = "8.4.7-2.1.9"
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    historical_root = tmp_path / historical_version
    historical_root.mkdir()
    _write_chart(
        historical_root,
        "mysql-operator",
        "mysql-operator",
        app_version="8.4.8-2.1.9",
    )

    with pytest.raises(FileNotFoundError, match="appVersion 8.4.8-2.1.9"):
        helmutil_module.get_missing_helm_chart_release_paths(
            ("mysql-operator",),
            (historical_version,),
            source_path=str(tmp_path),
        )


def test_get_helm_chart_path_rejects_split_metadata_release_mismatch(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"
    _write_chart(
        tmp_path,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.2.7",
    )

    with pytest.raises(FileNotFoundError, match="2.2.7"):
        helmutil_module.get_helm_chart_path(
            "mysql-innodbcluster",
            source_path=str(tmp_path),
            app_version="26.7.0-2.3.0",
        )


def test_get_previous_operator_chart_release_picks_prior_chart_patch(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"

    for release_name in ("26.7.0-2.3.0", "9.6.0-2.2.7", "8.4.9-2.1.11"):
        version_root = tmp_path / release_name
        version_root.mkdir()
        _write_chart(
            version_root,
            "mysql-operator",
            "mysql-operator",
            app_version=release_name,
        )

    assert helmutil_module.get_previous_operator_chart_release(
        source_path=str(tmp_path),
    ) == "9.6.0-2.2.7"


def test_get_previous_operator_chart_release_handles_same_mysql_version_patch_fix(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "8.0.33-2.0.10"

    for release_name in ("8.0.33-2.0.10", "8.0.33-2.0.9", "8.0.32-2.0.8"):
        version_root = tmp_path / release_name
        version_root.mkdir()
        _write_chart(
            version_root,
            "mysql-operator",
            "mysql-operator",
            app_version=release_name,
        )

    assert helmutil_module.get_previous_operator_chart_release(
        source_path=str(tmp_path),
    ) == "8.0.33-2.0.9"


def test_get_previous_operator_chart_release_falls_back_to_highest_previous_minor(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "9.0.0-2.2.0"

    for release_name in ("9.0.0-2.2.0", "8.4.10-2.1.12", "8.4.9-2.1.11"):
        version_root = tmp_path / release_name
        version_root.mkdir()
        _write_chart(
            version_root,
            "mysql-operator",
            "mysql-operator",
            app_version=release_name,
        )

    assert helmutil_module.get_previous_operator_chart_release(
        source_path=str(tmp_path),
    ) == "8.4.10-2.1.12"


def test_get_previous_operator_chart_release_raises_when_no_prior_release_exists(
    tmp_path,
    helmutil_module,
):
    helmutil_module.g_ts_cfg.operator_version_tag = "26.7.0-2.3.0"

    version_root = tmp_path / "26.7.0-2.3.0"
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version="26.7.0-2.3.0",
    )

    with pytest.raises(FileNotFoundError, match="26.7.0-2.3.0"):
        helmutil_module.get_previous_operator_chart_release(
            source_path=str(tmp_path),
        )


def test_validate_helm_test_environment_accepts_current_shortcut_paths(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    version_root = tmp_path / current_version
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        version_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )
    _link_chart(tmp_path, "mysql-operator", f"{current_version}/mysql-operator")
    _link_chart(
        tmp_path,
        "mysql-innodbcluster",
        f"{current_version}/mysql-innodbcluster",
    )

    helmutil_module.validate_helm_test_environment(str(tmp_path))


def test_validate_helm_test_environment_accepts_current_version_directory_only(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    version_root = tmp_path / current_version
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        version_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )

    helmutil_module.validate_helm_test_environment(str(tmp_path))


def test_validate_helm_test_environment_rejects_missing_required_chart(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    version_root = tmp_path / current_version
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )

    with pytest.raises(FileNotFoundError, match="mysql-innodbcluster"):
        helmutil_module.validate_helm_test_environment(str(tmp_path))


def test_validate_helm_test_environment_formats_current_layout_errors_per_line(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        helmutil_module.validate_helm_test_environment(str(tmp_path))

    assert str(exc_info.value) == "\n".join(
        [
            "Mounted Helm charts are incomplete under "
            f"{tmp_path}. Expected either top-level current charts "
            "mysql-operator, mysql-innodbcluster or versioned charts under "
            f"{current_version}.",
            "Shortcut layout errors:",
            f"  Helm chart directory does not exist: {tmp_path / 'mysql-operator'}",
            f"  Helm chart directory does not exist: {tmp_path / 'mysql-innodbcluster'}",
            "Versioned layout errors:",
            (
                f"  Helm chart directory does not exist: "
                f"{tmp_path / current_version / 'mysql-operator'}"
            ),
            (
                f"  Helm chart directory does not exist: "
                f"{tmp_path / current_version / 'mysql-innodbcluster'}"
            ),
        ]
    )


def test_validate_helm_test_environment_rejects_split_metadata_release_mismatch(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    version_root = tmp_path / current_version
    version_root.mkdir()
    _write_chart(
        version_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        version_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.2.7",
    )

    with pytest.raises(FileNotFoundError, match="Accepted releases from metadata: 26.7.0, 26.7.0-2.2.7"):
        helmutil_module.validate_helm_test_environment(str(tmp_path))


def test_validate_helm_test_environment_formats_historical_layout_errors_per_line(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    current_root = tmp_path / current_version
    current_root.mkdir()
    _write_chart(
        current_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        current_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )

    historical_root = tmp_path / "9.6.0-2.2.7"
    historical_root.mkdir()
    _write_chart(
        historical_root,
        "mysql-operator",
        "mysql-operator",
        app_version="9.6.1-2.2.7",
        version="2.2.7",
    )

    with pytest.raises(FileNotFoundError) as exc_info:
        helmutil_module.validate_helm_test_environment(str(tmp_path))

    assert str(exc_info.value) == "\n".join(
        [
            f"Mounted Helm release directories under {tmp_path} contain invalid chart layouts:",
            (
                f"[9.6.0-2.2.7] Configured Helm chart path "
                f"{historical_root / 'mysql-operator'} has appVersion 9.6.1-2.2.7 "
                f"and chart version 2.2.7; expected release 9.6.0-2.2.7. "
                "Accepted releases from metadata: 9.6.1-2.2.7"
            ),
            (
                f"[9.6.0-2.2.7] Helm chart directory does not exist: "
                f"{historical_root / 'mysql-innodbcluster'}"
            ),
        ]
    )


def test_validate_helm_test_environment_rejects_broken_symlink_in_historical_operator_chart(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    current_root = tmp_path / current_version
    current_root.mkdir()
    _write_chart(
        current_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        current_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )

    historical_root = tmp_path / "9.6.0-2.2.7"
    historical_root.mkdir()
    broken_operator_chart = _write_chart(
        historical_root,
        "mysql-operator",
        "mysql-operator",
        app_version="9.6.0-2.2.7",
    )
    _write_chart(
        historical_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="9.6.0",
        version="2.2.7",
    )
    _add_broken_symlink(
        broken_operator_chart,
        "mysql-operator",
        "9.6.0-2.2.7/mysql-operator",
    )

    with pytest.raises(FileNotFoundError, match=r"broken symlink.*9\.6\.0-2\.2\.7/mysql-operator"):
        helmutil_module.validate_helm_test_environment(str(tmp_path))


def test_validate_helm_test_environment_rejects_broken_symlink_in_historical_innodbcluster_chart(
    tmp_path,
    helmutil_module,
    monkeypatch,
):
    current_version = "26.7.0-2.3.0"
    monkeypatch.setattr(helmutil_module, "helm_binary_available", lambda: True)
    monkeypatch.setattr(
        helmutil_module.g_ts_cfg,
        "operator_version_tag",
        current_version,
    )

    current_root = tmp_path / current_version
    current_root.mkdir()
    _write_chart(
        current_root,
        "mysql-operator",
        "mysql-operator",
        app_version=current_version,
    )
    _write_chart(
        current_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="26.7.0",
        version="2.3.0",
    )

    historical_root = tmp_path / "9.6.0-2.2.7"
    historical_root.mkdir()
    _write_chart(
        historical_root,
        "mysql-operator",
        "mysql-operator",
        app_version="9.6.0-2.2.7",
    )
    broken_cluster_chart = _write_chart(
        historical_root,
        "mysql-innodbcluster",
        "mysql-innodbcluster",
        app_version="9.6.0",
        version="2.2.7",
    )
    _add_broken_symlink(
        broken_cluster_chart,
        "mysql-innodbcluster",
        "9.6.0-2.2.7/mysql-innodbcluster",
    )

    with pytest.raises(FileNotFoundError, match=r"broken symlink.*9\.6\.0-2\.2\.7/mysql-innodbcluster"):
        helmutil_module.validate_helm_test_environment(str(tmp_path))


def test_parse_image_reference_requires_registry_host(operator_helm_module):
    with pytest.raises(ValueError, match="explicit registry host"):
        operator_helm_module._parse_image_reference(
            "mysql/community-operator:26.7.0-2.3.0"
        )


def test_split_registry_and_repository_requires_registry_host(operator_helm_module):
    with pytest.raises(ValueError, match="explicit registry host"):
        operator_helm_module._split_registry_and_repository("mysql/community")


def test_split_helm_output_sections_preserves_preamble_and_named_sections(
    operator_helm_module,
):
    preamble, sections = operator_helm_module._split_helm_output_sections(
        SAMPLE_HELM_DEBUG_OUTPUT
    )

    assert "getting history for release" in preamble
    assert sections["NAME"] == "myoperator"
    assert sections["USER-SUPPLIED VALUES"] == "deployment:\n  name: mysql-operator"
    assert sections["HOOKS"] == ""
    assert sections["NOTES"] == "post-install notes"


def test_extract_manifest_objects_reads_manifest_tuples(operator_helm_module):
    manifest_objects = operator_helm_module._extract_manifest_objects(
        operator_helm_module._split_helm_output_sections(
            SAMPLE_HELM_DEBUG_OUTPUT
        )[1]["MANIFEST"]
    )

    assert manifest_objects == [
        ("ServiceAccount", "mysql-operator-sa", "operator-ns"),
        ("ClusterRole", "mysql-operator", ""),
        ("Deployment", "mysql-operator", "operator-ns"),
    ]


def test_install_with_helm_passes_exact_operator_image_and_pull_policies(
    monkeypatch,
    operator_helm_module,
    capsys,
):
    recorded = {}
    patched = {}

    monkeypatch.setattr(operator_helm_module.kutil, "create_ns", lambda namespace, labels=None: None)
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "mysql-operator",
                                "image": "registry.example.com/team/custom-operator:26.7.0-2.3.0",
                                "imagePullPolicy": "Never",
                            }
                        ]
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": patched.setdefault(
            "payload", patch
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: [],
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: (
            recorded.update(
                {
                    "chart_query": {
                        "chart_name": chart_name,
                        "app_version": app_version,
                    }
                }
            )
            or f"/charts/{chart_name}"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
        ),
    )

    options = _make_install_options(
        operator_helm_module,
        operator_values={
            "image": {
                "registry": "registry.example.com",
                "repository": "team",
                "name": "custom-operator",
                "tag": "26.7.0-2.3.0",
                "digest": "sha256:deadbeef",
                "pullPolicy": "Never",
                "pullSecrets": {"secretName": "repo-secret"},
            },
            "envs": {
                "imagesPullPolicy": "Always",
                "imagesDefaultRegistry": "registry.example.com",
                "imagesDefaultRepository": "team",
            },
            "deployment": {
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {
                        "maxSurge": 0,
                        "maxUnavailable": 1,
                    },
                },
            },
        },
    )
    options.helm_wait_timeout_seconds = 42

    install_result = operator_helm_module.install_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    argv = recorded["argv"]
    values = recorded["values"]
    assert "--debug" in argv
    assert "--values" in argv
    assert "--set" not in argv
    assert "--set-string" not in argv
    assert "--skip-crds" in argv
    assert "--wait" in argv
    assert "--timeout" in argv
    assert "42s" in argv
    assert recorded["chart_query"] == {
        "chart_name": "mysql-operator",
        "app_version": options.app_version,
    }
    assert argv[-1] == "/charts/mysql-operator"
    assert values["image"]["name"] == "custom-operator"
    assert values["image"]["tag"] == "26.7.0-2.3.0"
    assert values["image"]["pullPolicy"] == "Never"
    assert values["envs"]["imagesPullPolicy"] == "Always"
    assert values["deployment"]["strategy"] == {
        "type": "RollingUpdate",
        "rollingUpdate": {
            "maxSurge": 0,
            "maxUnavailable": 1,
        },
    }
    assert "pullSecrets" not in values["image"]
    assert "digest" not in values["image"]
    assert install_result.options is options
    assert patched["payload"]["spec"]["template"]["spec"]["containers"][0]["image"] == (
        "registry.example.com/team/custom-operator:26.7.0-2.3.0@sha256:deadbeef"
    )
    assert "Generated Helm values file" in capsys.readouterr().out


def test_install_with_helm_uses_chart_default_overrides_without_reconciling_images(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: pytest.fail(
            "chart-default helm install should not inspect operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": pytest.fail(
            "chart-default helm install should not patch operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: pytest.fail(
            "chart-default helm install should not wait for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: pytest.fail(
            "chart-default helm install should not list pods for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: pytest.fail(
            "chart-default helm install should skip image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(recorded),
    )

    options = _make_install_options(
        operator_helm_module,
        operator_values={
            "image": {
                "registry": "registry.example.com",
                "repository": "team",
            },
            "envs": {
                "imagesDefaultRegistry": "registry.example.com",
                "imagesDefaultRepository": "team",
            },
        },
        use_chart_defaults=True,
    )

    operator_helm_module.install_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    assert "--values" in recorded["argv"]
    assert recorded["values"] == {
        "image": {
            "registry": "registry.example.com",
            "repository": "team",
        },
        "envs": {
            "imagesDefaultRegistry": "registry.example.com",
            "imagesDefaultRepository": "team",
        },
    }


def test_install_with_helm_uses_empty_chart_defaults_without_reconciling_images(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: pytest.fail(
            "chart-default helm install should not inspect operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": pytest.fail(
            "chart-default helm install should not patch operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: pytest.fail(
            "chart-default helm install should not wait for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: pytest.fail(
            "chart-default helm install should not list pods for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: pytest.fail(
            "chart-default helm install should skip image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(recorded),
    )

    options = _make_install_options(
        operator_helm_module,
        use_chart_defaults=True,
    )

    operator_helm_module.install_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    assert "--values" not in recorded["argv"]
    assert "values" not in recorded


def test_install_cluster_with_helm_uses_cluster_chart_without_helm_wait(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: pytest.fail(
            "cluster helm install should not inspect operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": pytest.fail(
            "cluster helm install should not patch deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: pytest.fail(
            "cluster helm install should not wait for deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: pytest.fail(
            "cluster helm install should not list deployment pods"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: pytest.fail(
            "cluster helm install should not reconcile operator images"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: (
            recorded.update(
                {
                    "chart_query": {
                        "chart_name": chart_name,
                        "app_version": app_version,
                    }
                }
            )
            or f"/charts/{chart_name}"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )

    options = _make_cluster_install_options(
        operator_helm_module,
        cluster_values={
            "serverInstances": 3,
            "router": {
                "instances": 1,
            },
            "credentials": {
                "root": {
                    "secretName": "root-secret",
                },
            },
            "tls": {
                "useSelfSigned": True,
            },
            "podSpec": {
                "terminationGracePeriodSeconds": 5,
            },
        },
    )

    install_result = operator_helm_module.install_cluster_with_helm(
        "cluster-ns",
        resolved_options=options,
    )

    argv = recorded["argv"]
    values = recorded["values"]
    assert "--wait" not in argv
    assert "--timeout" not in argv
    assert recorded["chart_query"] == {
        "chart_name": "mysql-innodbcluster",
        "app_version": "26.7.0-2.3.0",
    }
    assert values["serverInstances"] == 3
    assert values["router"]["instances"] == 1
    assert "routerInstances" not in values
    assert values["credentials"]["root"]["secretName"] == "root-secret"
    assert values["tls"]["useSelfSigned"] is True
    assert values["podSpec"]["terminationGracePeriodSeconds"] == 5
    assert "serverVersion" not in values
    assert install_result.options is options


def test_upgrade_cluster_with_helm_uses_strict_upgrade_without_helm_wait(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: pytest.fail(
            "cluster helm upgrade should not create namespaces"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: pytest.fail(
            "cluster helm upgrade should not inspect operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": pytest.fail(
            "cluster helm upgrade should not patch deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: pytest.fail(
            "cluster helm upgrade should not wait for deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: pytest.fail(
            "cluster helm upgrade should not list deployment pods"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: pytest.fail(
            "cluster helm upgrade should not reconcile operator images"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: (
            recorded.update(
                {
                    "chart_query": {
                        "chart_name": chart_name,
                        "app_version": app_version,
                    }
                }
            )
            or f"/charts/{chart_name}"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )

    options = _make_cluster_install_options(
        operator_helm_module,
        app_version="9.6.0-2.2.7",
        cluster_values={
            "serverInstances": 3,
            "router": {
                "instances": 1,
            },
            "credentials": {
                "root": {
                    "secretName": "root-secret",
                },
            },
            "tls": {
                "useSelfSigned": True,
            },
            "podSpec": {
                "terminationGracePeriodSeconds": 5,
            },
        },
    )

    upgrade_result = operator_helm_module.upgrade_cluster_with_helm(
        "cluster-ns",
        resolved_options=options,
    )

    argv = recorded["argv"]
    values = recorded["values"]
    assert argv[:2] == ["helm", "upgrade"]
    assert "--install" not in argv
    assert "--wait" not in argv
    assert "--timeout" not in argv
    assert recorded["chart_query"] == {
        "chart_name": "mysql-innodbcluster",
        "app_version": "9.6.0-2.2.7",
    }
    assert recorded["get_manifest_argv"] == [
        "helm",
        "get",
        "manifest",
        "mycluster",
        "--namespace",
        "cluster-ns",
        "--kube-context",
        "kind-test",
    ]
    assert values["serverInstances"] == 3
    assert values["router"]["instances"] == 1
    assert values["credentials"]["root"]["secretName"] == "root-secret"
    assert values["tls"]["useSelfSigned"] is True
    assert values["podSpec"]["terminationGracePeriodSeconds"] == 5
    assert upgrade_result.options is options


def test_install_cluster_with_helm_merges_supported_test_harness_overrides(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_sts_labels",
        lambda: {"custom-label": "true"},
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_sts_podspec",
        lambda: "terminationGracePeriodSeconds: 42\nnodeSelector:\n  dedicated: mysql\n",
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_ic_server_version_override",
        lambda: "",
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_ic_server_version",
        lambda: "8.4.7",
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_ic_router_version_override",
        lambda: "",
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_ic_router_version",
        lambda: "",
    )

    options = _make_cluster_install_options(
        operator_helm_module,
        cluster_values={
            "serverInstances": 3,
            "router": {
                "instances": 1,
            },
            "credentials": {
                "root": {
                    "secretName": "root-secret",
                },
            },
            "tls": {
                "useSelfSigned": True,
            },
            "podLabels": {
                "existing-label": "true",
            },
            "podSpec": {
                "terminationGracePeriodSeconds": 5,
            },
        },
    )

    operator_helm_module.install_cluster_with_helm(
        "cluster-ns",
        resolved_options=options,
    )

    values = recorded["values"]
    assert values["serverVersion"] == "8.4.7"
    assert values["podLabels"] == {
        "existing-label": "true",
        "custom-label": "true",
    }
    assert values["podSpec"] == {
        "terminationGracePeriodSeconds": 42,
        "nodeSelector": {
            "dedicated": "mysql",
        },
    }


def test_install_cluster_with_helm_rejects_custom_router_version_override(
    monkeypatch,
    operator_helm_module,
):
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        lambda argv, check=True, cwd=None: pytest.fail(
            "helm command should not run when router version overrides are unsupported"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_ic_router_version_override",
        lambda: "8.4.7",
    )
    monkeypatch.setattr(
        operator_helm_module.g_ts_cfg,
        "get_custom_ic_router_version",
        lambda: "",
    )

    options = _make_cluster_install_options(
        operator_helm_module,
        cluster_values={
            "serverInstances": 3,
            "router": {
                "instances": 1,
            },
            "credentials": {
                "root": {
                    "secretName": "root-secret",
                },
            },
        },
    )

    with pytest.raises(RuntimeError, match="router version overrides"):
        operator_helm_module.install_cluster_with_helm(
            "cluster-ns",
            resolved_options=options,
        )


def test_install_with_helm_captures_debug_sections_and_manifest_inventory(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: False,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )

    options = _make_install_options(operator_helm_module)

    install_result = operator_helm_module.install_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    assert "--debug" in recorded["argv"]
    assert options.helm_output_preamble.startswith(
        'level=DEBUG msg="getting history for release"'
    )
    assert options.helm_output_sections["MANIFEST"].startswith("---")
    assert recorded["get_manifest_argv"] == [
        "helm",
        "get",
        "manifest",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--kube-context",
        "kind-test",
    ]
    assert options.helm_manifest_objects == [
        ("ServiceAccount", "mysql-operator-sa", "operator-ns"),
        ("ClusterRoleBinding", "mysql-operator-binding", ""),
        ("Deployment", "mysql-operator", "operator-ns"),
    ]
    assert install_result.release_key == ("operator-ns", "myoperator", "kind-test")
    assert install_result.output_sections == options.helm_output_sections
    assert install_result.manifest_objects == options.helm_manifest_objects


def test_install_with_helm_clears_manifest_inventory_when_failed_install_has_no_release(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: False,
    )

    def fake_run_command(argv, check=True, cwd=None):
        argv = list(argv)
        recorded.setdefault("argvs", []).append(argv)
        raise subprocess.CalledProcessError(
            1,
            argv,
            output=SAMPLE_HELM_DEBUG_OUTPUT,
        )

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        fake_run_command,
    )

    options = _make_install_options(operator_helm_module)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        operator_helm_module.install_with_helm(
            "operator-ns",
            resolved_options=options,
        )

    release_key = ("operator-ns", "myoperator", "kind-test")
    assert options.helm_output_sections["MANIFEST"].startswith("---")
    assert options.helm_manifest_objects == []
    assert release_key not in operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS
    assert exc_info.value.helm_manifest_objects == []
    assert len(recorded["argvs"]) == 1
    assert recorded["argvs"][0][:3] == ["helm", "upgrade", "--install"]


def test_upgrade_with_helm_uses_strict_upgrade_and_tracks_release_manifest(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: pytest.fail(
            "upgrade_with_helm should not create the namespace"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: False,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )

    release_key = ("operator-ns", "myoperator", "kind-test")
    operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] = [
        ("Deployment", "mysql-operator-old", "operator-ns"),
    ]

    options = _make_install_options(operator_helm_module)

    upgrade_result = operator_helm_module.upgrade_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    assert recorded["argv"][:2] == ["helm", "upgrade"]
    assert "--values" in recorded["argv"]
    assert "--install" not in recorded["argv"]
    assert "--skip-crds" not in recorded["argv"]
    assert recorded["get_manifest_argv"] == [
        "helm",
        "get",
        "manifest",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--kube-context",
        "kind-test",
    ]
    assert upgrade_result.manifest_objects == [
        ("ServiceAccount", "mysql-operator-sa", "operator-ns"),
        ("ClusterRoleBinding", "mysql-operator-binding", ""),
        ("Deployment", "mysql-operator", "operator-ns"),
    ]
    assert operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] == (
        upgrade_result.manifest_objects
    )


def test_upgrade_with_helm_uses_chart_default_overrides_without_reconciling_images(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: pytest.fail(
            "chart-default helm upgrade should not create namespaces"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: pytest.fail(
            "chart-default helm upgrade should not inspect operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": pytest.fail(
            "chart-default helm upgrade should not patch operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: pytest.fail(
            "chart-default helm upgrade should not wait for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: pytest.fail(
            "chart-default helm upgrade should not list pods for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: pytest.fail(
            "chart-default helm upgrade should skip image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )

    release_key = ("operator-ns", "myoperator", "kind-test")
    operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] = [
        ("Deployment", "mysql-operator-old", "operator-ns"),
    ]

    options = _make_install_options(
        operator_helm_module,
        operator_values={
            "image": {
                "registry": "registry.example.com",
                "repository": "team",
            },
            "envs": {
                "imagesDefaultRegistry": "registry.example.com",
                "imagesDefaultRepository": "team",
            },
            "deployment": {
                "name": "my-upgraded-operator",
            },
        },
        use_chart_defaults=True,
    )

    operator_helm_module.upgrade_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    assert "--values" in recorded["argv"]
    assert recorded["values"] == {
        "image": {
            "registry": "registry.example.com",
            "repository": "team",
        },
        "envs": {
            "imagesDefaultRegistry": "registry.example.com",
            "imagesDefaultRepository": "team",
        },
        "deployment": {
            "name": "my-upgraded-operator",
        },
    }


def test_upgrade_with_helm_uses_empty_chart_defaults_without_reconciling_images(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: pytest.fail(
            "chart-default helm upgrade should not create namespaces"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: pytest.fail(
            "chart-default helm upgrade should not inspect operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": pytest.fail(
            "chart-default helm upgrade should not patch operator deployments"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: pytest.fail(
            "chart-default helm upgrade should not wait for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: pytest.fail(
            "chart-default helm upgrade should not list pods for image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: pytest.fail(
            "chart-default helm upgrade should skip image reconciliation"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
            apply_output=SAMPLE_HELM_DEBUG_OUTPUT,
        ),
    )

    release_key = ("operator-ns", "myoperator", "kind-test")
    operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] = [
        ("Deployment", "mysql-operator-old", "operator-ns"),
    ]

    options = _make_install_options(
        operator_helm_module,
        use_chart_defaults=True,
    )

    operator_helm_module.upgrade_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    assert "--values" not in recorded["argv"]
    assert "values" not in recorded


def test_upgrade_with_helm_failed_upgrade_refreshes_manifest_inventory_from_release(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: pytest.fail(
            "upgrade_with_helm should not create the namespace"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: False,
    )

    def fake_run_command(argv, check=True, cwd=None):
        argv = list(argv)
        recorded.setdefault("argvs", []).append(argv)
        if argv[:3] == ["helm", "get", "manifest"]:
            return types.SimpleNamespace(stdout=SAMPLE_HELM_GET_MANIFEST_OUTPUT)
        raise subprocess.CalledProcessError(
            1,
            argv,
            output=SAMPLE_HELM_DEBUG_OUTPUT,
        )

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        fake_run_command,
    )

    release_key = ("operator-ns", "myoperator", "kind-test")
    previous_manifest_objects = [
        ("Deployment", "mysql-operator-old", "operator-ns"),
    ]
    operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] = list(
        previous_manifest_objects
    )

    options = _make_install_options(operator_helm_module)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        operator_helm_module.upgrade_with_helm(
            "operator-ns",
            resolved_options=options,
        )

    assert recorded["argvs"][0][:2] == ["helm", "upgrade"]
    assert recorded["argvs"][1] == [
        "helm",
        "get",
        "manifest",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--kube-context",
        "kind-test",
    ]
    assert options.helm_manifest_objects == [
        ("ServiceAccount", "mysql-operator-sa", "operator-ns"),
        ("ClusterRoleBinding", "mysql-operator-binding", ""),
        ("Deployment", "mysql-operator", "operator-ns"),
    ]
    assert operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] == (
        options.helm_manifest_objects
    )
    assert exc_info.value.helm_manifest_objects == options.helm_manifest_objects


def test_upgrade_with_helm_failed_upgrade_preserves_previous_manifest_inventory_when_refresh_fails(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: pytest.fail(
            "upgrade_with_helm should not create the namespace"
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: False,
    )

    def fake_run_command(argv, check=True, cwd=None):
        argv = list(argv)
        recorded.setdefault("argvs", []).append(argv)
        if argv[:3] == ["helm", "get", "manifest"]:
            raise subprocess.CalledProcessError(
                1,
                argv,
                output="Error: release not found\n",
            )
        raise subprocess.CalledProcessError(
            1,
            argv,
            output=SAMPLE_HELM_DEBUG_OUTPUT,
        )

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        fake_run_command,
    )

    release_key = ("operator-ns", "myoperator", "kind-test")
    previous_manifest_objects = [
        ("Deployment", "mysql-operator-old", "operator-ns"),
        ("ServiceAccount", "mysql-operator-sa-old", "operator-ns"),
    ]
    operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] = list(
        previous_manifest_objects
    )

    options = _make_install_options(operator_helm_module)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        operator_helm_module.upgrade_with_helm(
            "operator-ns",
            resolved_options=options,
        )

    assert recorded["argvs"][0][:2] == ["helm", "upgrade"]
    assert recorded["argvs"][1] == [
        "helm",
        "get",
        "manifest",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--kube-context",
        "kind-test",
    ]
    assert options.helm_manifest_objects == previous_manifest_objects
    assert operator_helm_module._HELM_RELEASE_MANIFEST_OBJECTS[release_key] == (
        previous_manifest_objects
    )
    assert exc_info.value.helm_manifest_objects == previous_manifest_objects


def test_install_with_helm_omits_managed_image_source_flags_when_defaults_are_unset(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}

    monkeypatch.setattr(operator_helm_module.kutil, "create_ns", lambda namespace, labels=None: None)
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "mysql-operator",
                                "image": "registry.example.com/team/custom-operator:26.7.0-2.3.0",
                                "imagePullPolicy": "IfNotPresent",
                            }
                        ]
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: [],
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
        ),
    )

    options = _make_install_options(
        operator_helm_module,
        operator_values={
            "image": {
                "registry": "registry.example.com",
                "repository": "team",
                "name": "custom-operator",
                "tag": "26.7.0-2.3.0",
                "pullPolicy": "IfNotPresent",
            },
            "envs": {
                "imagesPullPolicy": "IfNotPresent",
            },
        },
    )

    operator_helm_module.install_with_helm("operator-ns", resolved_options=options)

    values = recorded["values"]
    assert "imagesDefaultRegistry" not in values["envs"]
    assert "imagesDefaultRepository" not in values["envs"]


def test_install_with_helm_merges_generated_values_into_existing_values_dict(
    monkeypatch,
    operator_helm_module,
):
    recorded = {}
    operator_values = {
        "debugger": {"enabled": True},
        "image": {"pullSecrets": {"enabled": False}},
        "deployment": {
            "deploymentLabels": {"kept-deploy-label": "true"},
            "deploymentAnnotations": {"kept-deploy-annotation": "true"},
            "podLabels": {"kept-pod-label": "true"},
            "podAnnotations": {"kept-pod-annotation": "true"},
            "affinity": {
                "podAntiAffinity": {
                    "requiredDuringSchedulingIgnoredDuringExecution": [
                        {
                            "labelSelector": {
                                "matchLabels": {
                                    "e2e.mysql.oracle.com/affinity-blocker": "example",
                                },
                            },
                            "topologyKey": "kubernetes.io/hostname",
                        },
                    ],
                },
            },
            "nodeSelector": {
                "kubernetes.io/hostname": "worker-node-name",
            },
            "resources": {
                "limits": {
                    "cpu": "500m",
                    "memory": "1Gi",
                },
                "requests": {
                    "cpu": "100m",
                    "memory": "512Mi",
                },
            },
        },
    }

    monkeypatch.setattr(operator_helm_module.kutil, "create_ns", lambda namespace, labels=None: None)
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name: {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "mysql-operator",
                                "image": "registry.example.com/team/custom-operator:26.7.0-2.3.0",
                                "imagePullPolicy": "IfNotPresent",
                            }
                        ]
                    }
                }
            }
        },
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "patch_dp",
        lambda namespace, deployment_name, patch, type=None, data_as_type="yaml": None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace, pattern=None: [],
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        _fake_helm_run_command(
            recorded,
        ),
    )

    options = _make_install_options(
        operator_helm_module,
        operator_values=operator_helm_module._merge_operator_values(
            operator_values,
            {
                "image": {
                    "registry": "registry.example.com",
                    "repository": "team",
                    "name": "custom-operator",
                    "tag": "26.7.0-2.3.0",
                    "pullPolicy": "Always",
                    "pullSecrets": {"secretName": "repo-secret"},
                },
                "envs": {
                    "imagesPullPolicy": "Never",
                    "imagesDefaultRepository": "mirror-team",
                },
                "deployment": {
                    "namespaces": [" ns1 ", " ns2 ", " ns1 "],
                },
            },
        ),
    )

    operator_helm_module.install_with_helm("operator-ns", resolved_options=options)

    values = recorded["values"]
    assert values["image"]["repository"] == "team"
    assert values["image"]["pullPolicy"] == "Always"
    assert values["envs"]["imagesPullPolicy"] == "Never"
    assert values["envs"]["imagesDefaultRepository"] == "mirror-team"
    assert values["deployment"]["namespaces"] == ["ns1", "ns2"]
    assert values["deployment"]["deploymentLabels"] == {"kept-deploy-label": "true"}
    assert values["deployment"]["deploymentAnnotations"] == {"kept-deploy-annotation": "true"}
    assert values["deployment"]["podLabels"] == {"kept-pod-label": "true"}
    assert values["deployment"]["podAnnotations"] == {"kept-pod-annotation": "true"}
    assert values["deployment"]["affinity"] == {
        "podAntiAffinity": {
            "requiredDuringSchedulingIgnoredDuringExecution": [
                {
                    "labelSelector": {
                        "matchLabels": {
                            "e2e.mysql.oracle.com/affinity-blocker": "example",
                        },
                    },
                    "topologyKey": "kubernetes.io/hostname",
                },
            ],
        },
    }
    assert values["deployment"]["nodeSelector"] == {
        "kubernetes.io/hostname": "worker-node-name",
    }
    assert values["deployment"]["resources"] == {
        "limits": {
            "cpu": "500m",
            "memory": "1Gi",
        },
        "requests": {
            "cpu": "100m",
            "memory": "512Mi",
        },
    }
    assert values["debugger"]["enabled"] is True
    assert "deploymentLabels" not in values
    assert "deploymentAnnotations" not in values
    assert "podLabels" not in values
    assert "podAnnotations" not in values
    assert "affinity" not in values
    assert "nodeSelector" not in values
    assert "resources" not in values
    assert "pullSecrets" not in values["image"]


def test_run_command_does_not_override_environment(monkeypatch, helmutil_module):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return types.SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(helmutil_module.subprocess, "run", fake_run)

    helmutil_module.run_command(["helm", "version"])
    helmutil_module.run_command(["ss", "-ltnp"], check=False)

    helm_call, non_helm_call = calls

    assert helm_call[1]["env"] is None
    assert non_helm_call[1]["env"] is None


def test_uninstall_with_helm_deletes_empty_namespace_by_default(
    monkeypatch,
    operator_helm_module,
):
    recorded = {"argv": None, "deleted_namespaces": []}

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        lambda argv, check=False, cwd=None: recorded.update({"argv": list(argv)}),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_ns_ex",
        lambda: ["operator-ns"],
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_all_raw",
        lambda namespace: "",
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "delete_ns",
        lambda namespace: recorded["deleted_namespaces"].append(namespace),
    )

    operator_helm_module.uninstall_with_helm(
        release_key=("operator-ns", "myoperator", "kind-test"),
        check_namespace_empty=True,
        check_namespace_empty_timeout=0,
    )

    assert recorded["argv"] == [
        "helm",
        "uninstall",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--wait",
        "--kube-context",
        "kind-test",
    ]
    assert recorded["deleted_namespaces"] == ["operator-ns"]


def test_uninstall_with_helm_checks_manifest_objects_from_install_inventory(
    monkeypatch,
    operator_helm_module,
):
    recorded = {"argvs": [], "lookups": []}

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module,
        "get_helm_chart_path",
        lambda chart_name, app_version=None: f"/charts/{chart_name}",
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_reconcile_operator_deployment_image",
        lambda options: False,
    )

    def fake_run_command(argv, check=True, cwd=None):
        recorded["argvs"].append(list(argv))
        if argv[:3] == ["helm", "upgrade", "--install"]:
            return types.SimpleNamespace(stdout=SAMPLE_HELM_DEBUG_OUTPUT)
        if argv[:3] == ["helm", "get", "manifest"]:
            return types.SimpleNamespace(stdout=SAMPLE_HELM_GET_MANIFEST_OUTPUT)
        return types.SimpleNamespace(stdout="")

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        fake_run_command,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "get",
        lambda namespace, resource, name, check=False, cmd_output_log=None, **kwargs: recorded[
            "lookups"
        ].append((resource, name, namespace)) or None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_ns_ex",
        lambda: ["operator-ns"],
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_all_raw",
        lambda namespace: pytest.fail("namespace-wide emptiness check should be skipped when manifest inventory is available"),
    )

    options = _make_install_options(operator_helm_module)
    install_result = operator_helm_module.install_with_helm(
        "operator-ns",
        resolved_options=options,
    )

    operator_helm_module.uninstall_with_helm(
        install_result.release_key,
        delete_namespace=False,
        check_namespace_empty=True,
    )

    assert recorded["argvs"][0][:3] == ["helm", "upgrade", "--install"]
    assert recorded["argvs"][1] == [
        "helm",
        "get",
        "manifest",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--kube-context",
        "kind-test",
    ]
    assert recorded["argvs"][2] == [
        "helm",
        "uninstall",
        "myoperator",
        "--namespace",
        "operator-ns",
        "--wait",
        "--kube-context",
        "kind-test",
    ]
    assert recorded["lookups"] == [
        ("serviceaccount", "mysql-operator-sa", "operator-ns"),
        ("clusterrolebinding", "mysql-operator-binding", None),
        ("deployment", "mysql-operator", "operator-ns"),
    ]


def test_uninstall_with_helm_warns_and_manually_cleans_remaining_manifest_objects(
    monkeypatch,
    operator_helm_module,
    caplog,
):
    deleted = []
    wait_calls = []

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        lambda argv, check=False, cwd=None: types.SimpleNamespace(stdout=""),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_wait_for_manifest_objects_gone",
        lambda manifest_objects, timeout=10: (
            wait_calls.append(list(manifest_objects))
            or (
                list(manifest_objects)
                if len(wait_calls) == 1
                else []
            )
        ),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_delete_manifest_objects",
        lambda manifest_objects: deleted.extend(manifest_objects),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_ns_ex",
        lambda: [],
    )

    with caplog.at_level(logging.WARNING):
        operator_helm_module.uninstall_with_helm(
            release_key=("operator-ns", "myoperator", ""),
            manifest_objects=[
                ("Deployment", "mysql-operator", "operator-ns"),
            ],
            delete_namespace=False,
        )

    assert wait_calls == [
        [("Deployment", "mysql-operator", "operator-ns")],
        [("Deployment", "mysql-operator", "operator-ns")],
    ]
    assert deleted == [("Deployment", "mysql-operator", "operator-ns")]
    assert "attempting manual cleanup" in caplog.text


def test_uninstall_with_helm_raises_when_manual_manifest_cleanup_still_leaves_objects(
    monkeypatch,
    operator_helm_module,
):
    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        lambda argv, check=False, cwd=None: types.SimpleNamespace(stdout=""),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_wait_for_manifest_objects_gone",
        lambda manifest_objects, timeout=10: list(manifest_objects),
    )
    monkeypatch.setattr(
        operator_helm_module,
        "_delete_manifest_objects",
        lambda manifest_objects: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_ns_ex",
        lambda: [],
    )

    with pytest.raises(
        RuntimeError,
        match="manual cleanup still left manifest objects behind",
    ):
        operator_helm_module.uninstall_with_helm(
            release_key=("operator-ns", "myoperator", ""),
            manifest_objects=[
                ("Deployment", "mysql-operator", "operator-ns"),
            ],
            delete_namespace=False,
        )


def test_uninstall_with_helm_raises_when_namespace_still_has_objects(
    monkeypatch,
    operator_helm_module,
):
    deleted_namespaces = []

    monkeypatch.setattr(
        operator_helm_module,
        "run_command",
        lambda argv, check=False, cwd=None: None,
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_ns_ex",
        lambda: ["operator-ns"],
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_all_raw",
        lambda namespace: "### deployment\nmysql-operator 1/1 1 1",
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "delete_ns",
        lambda namespace: deleted_namespaces.append(namespace),
    )

    with pytest.raises(RuntimeError, match="still contains objects after helm uninstall"):
        operator_helm_module.uninstall_with_helm(
            release_key=("operator-ns", "myoperator", ""),
            delete_namespace=True,
            check_namespace_empty=True,
            check_namespace_empty_timeout=0,
        )

    assert deleted_namespaces == []


def test_wait_for_namespace_empty_waits_for_transient_pods(
    monkeypatch,
    operator_helm_module,
):
    state = {
        "namespace_exists": True,
        "objects": [
            "### po\nmyop 1/1 Terminating 0 5s",
            "### po\nmyop 0/1 Completed 0 5s",
            "",
        ],
        "pods": [
            [{"NAME": "myop", "STATUS": "Terminating"}],
            [{"NAME": "myop", "STATUS": "Completed"}],
            [],
        ],
        "waited_pods": [],
    }

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_ns_ex",
        lambda: ["operator-ns"] if state["namespace_exists"] else [],
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_all_raw",
        lambda namespace: state["objects"].pop(0),
    )
    monkeypatch.setattr(
        operator_helm_module.kutil,
        "ls_po",
        lambda namespace: state["pods"].pop(0),
    )

    def fake_wait_pod_gone(namespace, pod_name, timeout=300):
        state["waited_pods"].append((namespace, pod_name, timeout))

    monkeypatch.setattr(
        operator_helm_module.kutil,
        "wait_pod_gone",
        fake_wait_pod_gone,
    )
    monkeypatch.setattr(operator_helm_module.time, "time", lambda: 0)

    operator_helm_module.wait_for_namespace_empty(
        "operator-ns",
        timeout=5,
    )

    assert state["waited_pods"] == [
        ("operator-ns", "myop", 5),
        ("operator-ns", "myop", 5),
    ]
