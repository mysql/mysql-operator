# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import copy
import importlib
import pathlib
import subprocess
import sys
import types
import unittest

import pytest
import yaml


TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _drop_modules(*module_names):
    for module_name in module_names:
        sys.modules.pop(module_name, None)


def _make_artifacts():
    return {
        "namespace_labels": {
            "purpose": "tests",
        },
        "deployment": {
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {
                "name": "mysql-operator",
                "namespace": "operator-ns",
            },
            "spec": {
                "template": {
                    "spec": {
                        "serviceAccountName": "mysql-operator-sa",
                        "imagePullSecrets": [
                            {"name": "operator-pull-secret"},
                        ],
                    }
                }
            },
        },
        "clusterroles": {
            "mysql-operator-role": {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRole",
                "metadata": {
                    "name": "mysql-operator-role",
                },
            },
        },
        "clusterrolebindings": {
            "mysql-operator-rolebinding": {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "ClusterRoleBinding",
                "metadata": {
                    "name": "mysql-operator-rolebinding",
                },
            },
        },
        "clusterkopfpeerings": {
            "mysql-operator": {
                "apiVersion": "zalando.org/v1",
                "kind": "ClusterKopfPeering",
                "metadata": {
                    "name": "mysql-operator",
                },
            },
        },
        "serviceaccounts": {
            "mysql-operator-sa": {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {
                    "name": "mysql-operator-sa",
                    "namespace": "operator-ns",
                },
            },
        },
        "secrets": {
            "operator-pull-secret": {
                "apiVersion": "v1",
                "kind": "Secret",
                "metadata": {
                    "name": "operator-pull-secret",
                    "namespace": "operator-ns",
                },
            },
        },
        "roles": {
            "mysql-operator-role": {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "Role",
                "metadata": {
                    "name": "mysql-operator-role",
                    "namespace": "operator-ns",
                },
            },
        },
        "rolebindings": {
            "mysql-operator-rolebinding": {
                "apiVersion": "rbac.authorization.k8s.io/v1",
                "kind": "RoleBinding",
                "metadata": {
                    "name": "mysql-operator-rolebinding",
                    "namespace": "operator-ns",
                },
            },
        },
        "kopfpeerings": {
            "mysql-operator": {
                "apiVersion": "zalando.org/v1",
                "kind": "KopfPeering",
                "metadata": {
                    "name": "mysql-operator",
                    "namespace": "operator-ns",
                },
            },
        },
    }


def _make_selector_test_artifacts():
    artifacts = _make_artifacts()
    artifacts["clusterrolebindings"]["mysql-operator-rolebinding"]["roleRef"] = {
        "name": "mysql-operator-role",
    }
    artifacts["clusterrolebindings"]["mysql-operator-rolebinding"]["subjects"] = [
        {
            "kind": "ServiceAccount",
            "name": "mysql-operator-sa",
            "namespace": "operator-ns",
        },
    ]
    artifacts["rolebindings"]["mysql-operator-rolebinding"]["subjects"] = [
        {
            "kind": "ServiceAccount",
            "name": "mysql-operator-sa",
            "namespace": "operator-ns",
        },
    ]
    deployment = artifacts["deployment"]
    deployment["metadata"]["labels"] = {
        "version": "2.2.8",
        "app.kubernetes.io/version": "9.7.0",
    }
    deployment["metadata"]["annotations"] = {
        "meta.helm.sh/release-name": "bootstrap",
        "meta.helm.sh/release-namespace": "operator-ns",
    }
    deployment["spec"]["selector"] = {
        "matchLabels": {
            "name": "mysql-operator",
        },
    }
    deployment["spec"]["template"] = {
        "metadata": {
            "labels": {
                "name": "mysql-operator",
                "app.kubernetes.io/name": "mysql-operator",
                "app.kubernetes.io/instance": "mysql-operator",
                "app.kubernetes.io/component": "controller",
                "version": "2.2.8",
                "app.kubernetes.io/version": "9.7.0",
            },
        },
        "spec": {
            "serviceAccountName": "mysql-operator-sa",
            "imagePullSecrets": [
                {"name": "operator-pull-secret"},
            ],
            "containers": [
                {
                    "name": "mysql-operator",
                    "env": [
                        {
                            "name": "POD_NAME",
                            "valueFrom": {
                                "fieldRef": {
                                    "fieldPath": "metadata.name",
                                },
                            },
                        },
                        {
                            "name": "POD_NAMESPACE",
                            "valueFrom": {
                                "fieldRef": {
                                    "fieldPath": "metadata.namespace",
                                },
                            },
                        },
                    ],
                },
            ],
        },
    }
    return artifacts


def _expected_manifest_objects():
    return [
        ("ClusterRole", "mysql-operator-role", ""),
        ("ClusterRoleBinding", "mysql-operator-rolebinding", ""),
        ("ClusterKopfPeering", "mysql-operator", ""),
        ("Secret", "operator-pull-secret", "operator-ns"),
        ("ServiceAccount", "mysql-operator-sa", "operator-ns"),
        ("Role", "mysql-operator-role", "operator-ns"),
        ("RoleBinding", "mysql-operator-rolebinding", "operator-ns"),
        ("KopfPeering", "mysql-operator", "operator-ns"),
        ("Deployment", "mysql-operator", "operator-ns"),
    ]


def _make_live_operator_deployment(namespace: str, name: str) -> dict:
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                        },
                    ],
                },
            },
        },
    }


def _make_non_operator_deployment(namespace: str, name: str) -> dict:
    return {
        "metadata": {
            "name": name,
            "namespace": namespace,
        },
        "spec": {
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": "nginx",
                        },
                    ],
                },
            },
        },
    }


def _write_raw_deploy_manifests(path: pathlib.Path) -> None:
    path.mkdir(parents=True)
    (path / "deploy-crds.yaml").write_text(
        """---
apiVersion: apiextensions.k8s.io/v1
kind: CustomResourceDefinition
metadata:
  name: innodbclusters.mysql.oracle.com
""",
        encoding="utf8",
    )
    (path / "deploy-operator.yaml").write_text(
        """---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: mysql-operator
rules:
- apiGroups:
  - apps
  resources:
  - deployments
  - statefulsets
  verbs:
  - get
  - create
  - patch
  - update
  - watch
  - delete
---
apiVersion: v1
kind: Namespace
metadata:
  name: mysql-operator
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql-operator
  namespace: mysql-operator
spec:
  selector:
    matchLabels:
      name: mysql-operator
  template:
    metadata:
      labels:
        name: mysql-operator
    spec:
      containers:
      - name: mysql-operator
        image: container-registry.oracle.com/mysql/community-operator:old
        imagePullPolicy: IfNotPresent
""",
        encoding="utf8",
    )


@pytest.fixture
def operator_t_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules(
        "e2e.mysqloperator.operator.operator_t",
        "e2e.mysqloperator.operator",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.stream",
        "mysql",
        "mysql.connector",
        "utils.helmutil",
        "utils.kutil",
        "utils.tutil",
        "utils.utils",
        "setup.config",
        "setup.defaults",
    )

    mysql_module = types.ModuleType("mysql")
    mysql_connector_module = types.ModuleType("mysql.connector")
    mysql_module.connector = mysql_connector_module
    sys.modules["mysql"] = mysql_module
    sys.modules["mysql.connector"] = mysql_connector_module

    kubernetes_module = types.ModuleType("kubernetes")
    kubernetes_client_module = types.ModuleType("kubernetes.client")
    kubernetes_client_rest_module = types.ModuleType("kubernetes.client.rest")
    kubernetes_stream_module = types.ModuleType("kubernetes.stream")

    class ApiException(Exception):
        pass

    kubernetes_client_rest_module.ApiException = ApiException
    kubernetes_stream_module.stream = lambda *args, **kwargs: None
    kubernetes_module.client = kubernetes_client_module
    kubernetes_module.stream = kubernetes_stream_module

    sys.modules["kubernetes"] = kubernetes_module
    sys.modules["kubernetes.client"] = kubernetes_client_module
    sys.modules["kubernetes.client.rest"] = kubernetes_client_rest_module
    sys.modules["kubernetes.stream"] = kubernetes_stream_module

    tutil_module = types.ModuleType("utils.tutil")

    class OperatorTest(unittest.TestCase):
        pass

    tutil_module.OperatorTest = OperatorTest
    tutil_module.g_full_log = False
    sys.modules["utils.tutil"] = tutil_module

    module = importlib.import_module("e2e.mysqloperator.operator.operator_t")
    module._KUBECTL_OPERATOR_INSTALLS.clear()
    yield module

    module._KUBECTL_OPERATOR_INSTALLS.clear()
    _drop_modules(
        "e2e.mysqloperator.operator.operator_t",
        "e2e.mysqloperator.operator",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.stream",
        "mysql",
        "mysql.connector",
        "utils.helmutil",
        "utils.kutil",
        "utils.tutil",
        "utils.utils",
        "setup.config",
        "setup.defaults",
    )


def test_get_initial_artifacts_registers_bootstrap_install(
    monkeypatch,
    operator_t_module,
):
    artifacts = _make_artifacts()
    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")
    monkeypatch.setattr(
        operator_t_module,
        "get_operator_artifacts",
        lambda **kwargs: copy.deepcopy(artifacts),
    )

    class DummyTest(operator_t_module.OperatorSingleAndMultipleBaseTest):
        pass

    DummyTest.operator_ns = "operator-ns"
    DummyTest.operator_deploy_name = "mysql-operator"
    DummyTest.crole_names_pattern = "unused"
    DummyTest.crole_binding_pattern = "unused"
    DummyTest.ckopf_peerings_pattern = "unused"
    DummyTest.sa_names_pattern = "unused"
    DummyTest.role_names_pattern = "unused"
    DummyTest.role_binding_pattern = "unused"
    DummyTest.kopf_peerings_pattern = "unused"

    DummyTest.get_initial_artifacts()

    install_key = operator_t_module.get_kubectl_install_target(
        "operator-ns",
        "mysql-operator",
    )
    tracked_install = operator_t_module._KUBECTL_OPERATOR_INSTALLS[install_key]

    assert DummyTest.artifacts == artifacts
    assert tracked_install.install_key == ("operator-ns", "mysql-operator", "kind-test")
    assert tracked_install.manifest_objects == _expected_manifest_objects()
    assert tracked_install.artifacts == artifacts


def test_install_operator_returns_and_tracks_manifest_objects(
    monkeypatch,
    operator_t_module,
):
    artifacts = _make_artifacts()
    apply_calls = []
    created_namespaces = []

    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")
    monkeypatch.setattr(operator_t_module.kutil, "ls_ns_ex", lambda: [])
    monkeypatch.setattr(
        operator_t_module.kutil,
        "apply",
        lambda ns, y, check=True: apply_calls.append((ns, yaml.safe_load(y))),
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "create_ns",
        lambda namespace, labels=None: created_namespaces.append((namespace, labels or {})),
    )

    install_result = operator_t_module.install_operator(
        copy.deepcopy(artifacts),
        "operator-ns",
    )

    assert install_result.install_key == ("operator-ns", "mysql-operator", "kind-test")
    assert install_result.manifest_objects == _expected_manifest_objects()
    assert install_result.artifacts == artifacts
    assert created_namespaces == [("operator-ns", {"purpose": "tests"})]
    assert [manifest["kind"] for _, manifest in apply_calls] == [
        "ClusterRole",
        "ClusterRoleBinding",
        "ClusterKopfPeering",
        "Secret",
        "ServiceAccount",
        "Role",
        "RoleBinding",
        "KopfPeering",
        "Deployment",
    ]
    assert (
        operator_t_module._KUBECTL_OPERATOR_INSTALLS[install_result.install_key].manifest_objects
        == _expected_manifest_objects()
    )


def test_setup_cleans_unexpected_operators_and_restores_default(
    monkeypatch,
    operator_t_module,
    capsys,
):
    artifact_calls = []
    removed = []
    restored = []
    wait_calls = []

    baseline_artifacts = copy.deepcopy(_make_artifacts())
    baseline_artifacts["deployment"]["metadata"]["namespace"] = "mysql-operator"
    for manifest_type in (
        "serviceaccounts",
        "secrets",
        "roles",
        "rolebindings",
        "kopfpeerings",
    ):
        for manifest in baseline_artifacts[manifest_type].values():
            manifest["metadata"]["namespace"] = "mysql-operator"

    monkeypatch.setattr(
        operator_t_module.kutil,
        "ls_ns_ex",
        lambda: ["app-ns", "mysql-operator", "op-extra"],
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "ls_deploy",
        lambda namespace: {
            "app-ns": [{"NAME": "web"}],
            "mysql-operator": [],
            "op-extra": [{"NAME": "myop"}],
        }.get(namespace, []),
    )

    def fake_get_deploy(namespace, deployment_name, check=False, cmd_output_log=None):
        if (namespace, deployment_name) == ("op-extra", "myop"):
            return _make_live_operator_deployment(namespace, deployment_name)
        if (namespace, deployment_name) == ("app-ns", "web"):
            return _make_non_operator_deployment(namespace, deployment_name)
        if (namespace, deployment_name) == ("mysql-operator", "mysql-operator"):
            return None
        return None

    monkeypatch.setattr(operator_t_module.kutil, "get_deploy", fake_get_deploy)

    leaked_artifacts = {"deployment": {"metadata": {"name": "myop"}}}

    def fake_get_operator_artifacts(**kwargs):
        artifact_calls.append((kwargs["operator_ns"], kwargs["operator_deploy_name"]))
        return leaked_artifacts

    monkeypatch.setattr(
        operator_t_module,
        "get_operator_artifacts",
        fake_get_operator_artifacts,
    )
    monkeypatch.setattr(
        operator_t_module,
        "remove_operator",
        lambda **kwargs: removed.append(kwargs),
    )
    monkeypatch.setattr(
        operator_t_module,
        "restore_operator",
        lambda artifacts, namespace: restored.append((artifacts, namespace)),
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "wait_deploy",
        lambda namespace, deployment_name, timeout=300: wait_calls.append(
            (namespace, deployment_name, timeout)
        ),
    )

    class DummyTest(operator_t_module.OperatorSingleAndMultipleBaseTest):
        default_allowed_op_errors = []

        def runTest(self):
            pass

    DummyTest.operator_ns = "mysql-operator"
    DummyTest.operator_deploy_name = "mysql-operator"
    DummyTest.artifacts = baseline_artifacts

    DummyTest().setUp()

    output = capsys.readouterr().out
    assert (
        "WARNING: unexpected operator found before test start: op-extra/myop; "
        "removing it"
    ) in output
    assert (
        "WARNING: default operator mysql-operator/mysql-operator is missing "
        "before test start; reinstalling it"
    ) in output
    assert artifact_calls == [("op-extra", "myop")]
    assert removed == [
        {
            "operator_ns": "op-extra",
            "operator_deploy_name": "myop",
            "artifacts": leaked_artifacts,
            "return_manifests": False,
            "delete_namespace": True,
            "check_namespace_empty": False,
        }
    ]
    assert restored == [(baseline_artifacts, "mysql-operator")]
    assert wait_calls == [("mysql-operator", "mysql-operator", 300)]


def test_remove_operator_deletes_tracked_manifest_objects_in_reverse_order(
    monkeypatch,
    operator_t_module,
):
    artifacts = _make_artifacts()
    deleted = []
    deleted_namespaces = []

    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")
    install_result = operator_t_module._track_kubectl_install_from_artifacts(
        operator_ns="operator-ns",
        operator_deploy_name="mysql-operator",
        artifacts=artifacts,
        kube_context="kind-test",
    )

    monkeypatch.setattr(
        operator_t_module,
        "print_operator_traceback_context",
        lambda namespace, deployment_name, lines_before=100: False,
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete",
        lambda namespace, resource, name, timeout=60: deleted.append(
            (resource, name, namespace, timeout)
        ),
    )
    monkeypatch.setattr(operator_t_module.kutil, "ls_ns_ex", lambda: ["operator-ns"])
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete_ns",
        lambda namespace: deleted_namespaces.append(namespace),
    )

    returned_artifacts = operator_t_module.remove_operator(
        operator_ns="operator-ns",
        operator_deploy_name="mysql-operator",
        return_manifests=True,
        delete_namespace=False,
        check_namespace_empty=False,
    )

    assert returned_artifacts == artifacts
    assert deleted == [
        ("deployment", "mysql-operator", "operator-ns", 60),
        ("kopfpeering", "mysql-operator", "operator-ns", 60),
        ("rolebinding", "mysql-operator-rolebinding", "operator-ns", 60),
        ("role", "mysql-operator-role", "operator-ns", 60),
        ("serviceaccount", "mysql-operator-sa", "operator-ns", 60),
        ("secret", "operator-pull-secret", "operator-ns", 60),
        ("clusterkopfpeering", "mysql-operator", None, 60),
        ("clusterrolebinding", "mysql-operator-rolebinding", None, 60),
        ("clusterrole", "mysql-operator-role", None, 60),
    ]
    assert deleted_namespaces == []
    assert install_result.install_key not in operator_t_module._KUBECTL_OPERATOR_INSTALLS


def test_remove_operator_check_namespace_empty_can_preserve_namespace(
    monkeypatch,
    operator_t_module,
):
    wait_calls = []
    delete_ns_calls = []

    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")
    operator_t_module._track_kubectl_install_from_artifacts(
        operator_ns="operator-ns",
        operator_deploy_name="mysql-operator",
        artifacts=_make_artifacts(),
        kube_context="kind-test",
    )

    monkeypatch.setattr(
        operator_t_module,
        "print_operator_traceback_context",
        lambda namespace, deployment_name, lines_before=100: False,
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete",
        lambda namespace, resource, name, timeout=60: None,
    )
    monkeypatch.setattr(operator_t_module.kutil, "ls_ns_ex", lambda: ["operator-ns"])
    monkeypatch.setattr(
        operator_t_module,
        "wait_for_namespace_empty",
        lambda namespace, timeout=60: wait_calls.append((namespace, timeout)),
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete_ns",
        lambda namespace: delete_ns_calls.append(namespace),
    )

    operator_t_module.remove_operator(
        operator_ns="operator-ns",
        operator_deploy_name="mysql-operator",
        return_manifests=False,
        delete_namespace=False,
        check_namespace_empty=True,
        check_namespace_empty_timeout=17,
    )

    assert wait_calls == [("operator-ns", 17)]
    assert delete_ns_calls == []


def test_remove_operator_delete_namespace_does_not_implicitly_wait(
    monkeypatch,
    operator_t_module,
):
    wait_calls = []
    delete_ns_calls = []

    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")
    operator_t_module._track_kubectl_install_from_artifacts(
        operator_ns="operator-ns",
        operator_deploy_name="mysql-operator",
        artifacts=_make_artifacts(),
        kube_context="kind-test",
    )

    monkeypatch.setattr(
        operator_t_module,
        "print_operator_traceback_context",
        lambda namespace, deployment_name, lines_before=100: False,
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete",
        lambda namespace, resource, name, timeout=60: None,
    )
    monkeypatch.setattr(operator_t_module.kutil, "ls_ns_ex", lambda: ["operator-ns"])
    monkeypatch.setattr(
        operator_t_module,
        "wait_for_namespace_empty",
        lambda namespace, timeout=60: wait_calls.append((namespace, timeout)),
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete_ns",
        lambda namespace: delete_ns_calls.append(namespace),
    )

    operator_t_module.remove_operator(
        operator_ns="operator-ns",
        operator_deploy_name="mysql-operator",
        return_manifests=False,
        delete_namespace=True,
        check_namespace_empty=False,
    )

    assert wait_calls == []
    assert delete_ns_calls == ["operator-ns"]


def test_install_operator_failure_tracks_partial_manifest_ownership(
    monkeypatch,
    operator_t_module,
):
    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")
    monkeypatch.setattr(operator_t_module.kutil, "ls_ns_ex", lambda: [])
    monkeypatch.setattr(
        operator_t_module.kutil,
        "create_ns",
        lambda namespace, labels=None: None,
    )

    def fake_apply(ns, y, check=True):
        manifest = yaml.safe_load(y)
        if manifest["kind"] == "ClusterRoleBinding":
            raise subprocess.CalledProcessError(
                1,
                ["kubectl", "apply"],
                output=b"",
                stderr=b"failed",
            )
        return None

    monkeypatch.setattr(operator_t_module.kutil, "apply", fake_apply)

    with pytest.raises(subprocess.CalledProcessError) as exc_info:
        operator_t_module.install_operator(
            copy.deepcopy(_make_artifacts()),
            "operator-ns",
        )

    partial_install_result = exc_info.value.kubectl_install_result
    assert partial_install_result.install_key == (
        "operator-ns",
        "mysql-operator",
        "kind-test",
    )
    assert partial_install_result.manifest_objects == [
        ("ClusterRole", "mysql-operator-role", ""),
    ]
    assert (
        operator_t_module._KUBECTL_OPERATOR_INSTALLS[partial_install_result.install_key].manifest_objects
        == partial_install_result.manifest_objects
    )


def test_get_kubectl_install_target_uses_kube_context(
    monkeypatch,
    operator_t_module,
):
    monkeypatch.setattr(operator_t_module.g_ts_cfg, "k8s_context", "kind-test")

    assert operator_t_module.get_kubectl_install_target(
        "operator-ns",
        "mysql-operator",
    ) == ("operator-ns", "mysql-operator", "kind-test")
    assert operator_t_module.get_kubectl_install_target(
        "operator-ns",
        "mysql-operator",
        kube_context="kind-alt",
    ) == ("operator-ns", "mysql-operator", "kind-alt")


def test_failed_helm_operator_upgrade_target_state_keeps_existing_target(
    monkeypatch,
    operator_t_module,
):
    deploy_calls = []

    def fake_get_deploy(namespace, deployment_name, check=False):
        deploy_calls.append((namespace, deployment_name, check))
        return {
            "metadata": {
                "namespace": namespace,
                "name": deployment_name,
            },
        }

    monkeypatch.setattr(operator_t_module.kutil, "get_deploy", fake_get_deploy)

    operator_t_module.assert_failed_helm_operator_upgrade_target_state(
        unittest.TestCase(),
        installed_namespace="operator-ns",
        installed_deployment_name="mysql-operator",
        attempted_namespace="operator-ns",
        attempted_deployment_name="mysql-operator",
    )

    assert deploy_calls == [("operator-ns", "mysql-operator", False)]


def test_failed_helm_operator_upgrade_target_state_allows_absent_new_target(
    monkeypatch,
    operator_t_module,
):
    deploy_calls = []

    def fake_get_deploy(namespace, deployment_name, check=False):
        deploy_calls.append((namespace, deployment_name, check))
        return None

    monkeypatch.setattr(operator_t_module.kutil, "get_deploy", fake_get_deploy)

    operator_t_module.assert_failed_helm_operator_upgrade_target_state(
        unittest.TestCase(),
        installed_namespace="operator-ns",
        installed_deployment_name="mysql-operator",
        attempted_namespace="operator-ns",
        attempted_deployment_name="scoped-operator",
    )

    assert deploy_calls == [("operator-ns", "scoped-operator", False)]


def test_failed_helm_operator_upgrade_target_state_rejects_created_new_target(
    monkeypatch,
    operator_t_module,
):
    monkeypatch.setattr(
        operator_t_module.kutil,
        "get_deploy",
        lambda namespace, deployment_name, check=False: {
            "metadata": {
                "namespace": namespace,
                "name": deployment_name,
            },
        },
    )

    with pytest.raises(AssertionError):
        operator_t_module.assert_failed_helm_operator_upgrade_target_state(
            unittest.TestCase(),
            installed_namespace="operator-ns",
            installed_deployment_name="mysql-operator",
            attempted_namespace="operator-ns",
            attempted_deployment_name="scoped-operator",
        )


def test_fatal_mysql_upgrade_logs_detect_server_upgrade_failure(operator_t_module):
    log_contents = "\n".join(
        [
            "2026-05-08T09:05:19.052926Z 6 [System] [MY-013381] "
            "[Server] Server upgrade from '90600' to '90700' started.",
            "2026-05-08T09:06:11.239889Z 6 [ERROR] [MY-013178] "
            "[Server] Execution of server-side SQL statement failed with "
            "error code = 1205, error message = 'Lock wait timeout exceeded; "
            "try restarting transaction'.",
            "2026-05-08T09:06:11.242191Z 0 [ERROR] [MY-013380] "
            "[Server] Failed to upgrade server.",
            "2026-05-08T09:06:11.242242Z 0 [ERROR] [MY-010119] "
            "[Server] Aborting",
        ]
    )

    fatal_lines = (
        operator_t_module._HelmLegacySwitchoverRbacUpgradeBase
        ._get_fatal_mysql_upgrade_log_lines(log_contents)
    )

    assert fatal_lines == [
        "2026-05-08T09:06:11.239889Z 6 [ERROR] [MY-013178] "
        "[Server] Execution of server-side SQL statement failed with "
        "error code = 1205, error message = 'Lock wait timeout exceeded; "
        "try restarting transaction'.",
        "2026-05-08T09:06:11.242242Z 0 [ERROR] [MY-010119] "
        "[Server] Aborting",
    ]


def test_raw_deploy_dir_for_current_release_uses_deploy_path(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    current_deploy_path = tmp_path / "deploy"
    historic_deploy_path = tmp_path / "deploy-historic"
    _write_raw_deploy_manifests(current_deploy_path)
    _write_raw_deploy_manifests(
        historic_deploy_path / operator_t_module.g_ts_cfg.operator_version_tag
    )

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_path",
        str(current_deploy_path),
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_historic_path",
        str(historic_deploy_path),
        raising=False,
    )

    assert (
        operator_t_module.get_raw_deploy_dir_for_release(
            operator_t_module.g_ts_cfg.operator_version_tag
        )
        == str(current_deploy_path)
    )


def test_default_operator_manifest_path_uses_configured_deploy_path(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    current_deploy_path = tmp_path / "deploy"
    _write_raw_deploy_manifests(current_deploy_path)

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_path",
        str(current_deploy_path),
        raising=False,
    )

    assert operator_t_module.get_default_operator_deploy_manifest_path() == (
        current_deploy_path / "deploy-operator.yaml"
    )


def test_get_helm_cluster_operator_release_uses_cluster_annotation(
    monkeypatch,
    operator_t_module,
):
    monkeypatch.setattr(
        operator_t_module.kutil,
        "get_ic",
        lambda namespace, name: {
            "metadata": {
                "annotations": {
                    "mysql.oracle.com/mysql-operator-version": "9.7.0-2.2.8",
                },
            },
        },
        raising=False,
    )

    assert operator_t_module.OperatorSingleAndMultipleBaseTest._get_helm_cluster_operator_release(
        namespace="cluster-ns",
        cluster_name="cluster",
        fallback_release="9.6.0-2.2.7",
    ) == "9.7.0-2.2.8"


def test_get_helm_cluster_operator_release_falls_back_without_annotation(
    monkeypatch,
    operator_t_module,
):
    monkeypatch.setattr(
        operator_t_module.kutil,
        "get_ic",
        lambda namespace, name: {"metadata": {"annotations": {}}},
        raising=False,
    )

    assert operator_t_module.OperatorSingleAndMultipleBaseTest._get_helm_cluster_operator_release(
        namespace="cluster-ns",
        cluster_name="cluster",
        fallback_release="9.6.0-2.2.7",
    ) == "9.6.0-2.2.7"


def test_current_operator_release_requires_current_switchover_rbac(
    operator_t_module,
):
    assert operator_t_module.OperatorSingleAndMultipleBaseTest._expected_namespace_switchover_rbac_state(
        switchover_rbac_release=operator_t_module.g_ts_cfg.operator_version_tag,
        cluster_releases=["9.6.0-2.2.7"],
    ) == (False, True)


def test_current_switchover_wait_allows_complete_legacy_rbac_when_opted_in(
    monkeypatch,
    operator_t_module,
):
    current_release = operator_t_module.g_ts_cfg.operator_version_tag
    test_case = operator_t_module.OperatorSingleAndMultipleBaseTest()
    state = {
        "legacy_sa": {"metadata": {"name": "mysql-switchover-sa"}},
        "legacy_rb": {"metadata": {"name": "mysql-switchover-rb"}},
        "clusters": {
            "cluster-a": {
                "sa": {"metadata": {"name": "cluster-a-switchover-sa"}},
                "rb": {"metadata": {"name": "cluster-a-switchover-rb"}},
            },
        },
    }

    monkeypatch.setattr(
        test_case,
        "_get_namespace_switchover_rbac_state",
        lambda *, namespace, cluster_names: state,
    )
    monkeypatch.setattr(
        test_case,
        "_assert_cluster_current_switchover_rbac_state",
        lambda **kwargs: None,
    )
    monkeypatch.setattr(
        test_case,
        "wait",
        lambda callback, **kwargs: callback(),
        raising=False,
    )

    assert test_case._wait_for_namespace_switchover_rbac_state(
        namespace="cluster-ns",
        cluster_names=["cluster-a"],
        switchover_rbac_release=current_release,
        cluster_releases=["9.6.0-2.2.7", current_release],
        allow_legacy_if_present=True,
    ) == state


def test_current_switchover_assertion_allows_complete_legacy_rbac_when_opted_in(
    monkeypatch,
    operator_t_module,
):
    current_release = operator_t_module.g_ts_cfg.operator_version_tag
    test_case = operator_t_module.OperatorSingleAndMultipleBaseTest()
    checked_clusters = []

    monkeypatch.setattr(
        test_case,
        "_get_namespace_switchover_rbac_state",
        lambda *, namespace, cluster_names: {
            "legacy_sa": {"metadata": {"name": "mysql-switchover-sa"}},
            "legacy_rb": {"metadata": {"name": "mysql-switchover-rb"}},
            "clusters": {
                "cluster-a": {
                    "sa": {"metadata": {"name": "cluster-a-switchover-sa"}},
                    "rb": {"metadata": {"name": "cluster-a-switchover-rb"}},
                },
            },
        },
    )
    monkeypatch.setattr(
        test_case,
        "_assert_cluster_current_switchover_rbac_state",
        lambda **kwargs: checked_clusters.append(kwargs["cluster_name"]),
    )

    test_case._assert_namespace_switchover_rbac_state(
        namespace="cluster-ns",
        cluster_names=["cluster-a"],
        switchover_rbac_release=current_release,
        cluster_releases=["9.6.0-2.2.7", current_release],
        allow_legacy_if_present=True,
    )

    assert checked_clusters == ["cluster-a"]


def test_current_switchover_assertion_rejects_partial_legacy_rbac(
    monkeypatch,
    operator_t_module,
):
    current_release = operator_t_module.g_ts_cfg.operator_version_tag
    test_case = operator_t_module.OperatorSingleAndMultipleBaseTest()

    monkeypatch.setattr(
        test_case,
        "_get_namespace_switchover_rbac_state",
        lambda *, namespace, cluster_names: {
            "legacy_sa": {"metadata": {"name": "mysql-switchover-sa"}},
            "legacy_rb": None,
            "clusters": {
                "cluster-a": {
                    "sa": {"metadata": {"name": "cluster-a-switchover-sa"}},
                    "rb": {"metadata": {"name": "cluster-a-switchover-rb"}},
                },
            },
        },
    )
    monkeypatch.setattr(
        test_case,
        "_assert_cluster_current_switchover_rbac_state",
        lambda **kwargs: None,
    )

    with pytest.raises(AssertionError, match="both exist or both be absent"):
        test_case._assert_namespace_switchover_rbac_state(
            namespace="cluster-ns",
            cluster_names=["cluster-a"],
            switchover_rbac_release=current_release,
            cluster_releases=["9.6.0-2.2.7", current_release],
            allow_legacy_if_present=True,
        )


def test_legacy_switchover_cluster_values_request_ephemeral_storage(
    operator_t_module,
):
    values = (
        operator_t_module._HelmLegacySwitchoverRbacUpgradeBase()
        ._build_legacy_switchover_cluster_values()
    )

    pod_spec = values["podSpec"]
    assert pod_spec["terminationGracePeriodSeconds"] == 5

    server_containers = {
        container["name"]: container
        for container in pod_spec["containers"]
    }
    init_containers = {
        container["name"]: container
        for container in pod_spec["initContainers"]
    }
    router_containers = {
        container["name"]: container
        for container in values["router"]["podSpec"]["containers"]
    }

    assert set(server_containers) == {"sidecar", "mysql"}
    assert set(init_containers) == {"fixdatadir", "initconf", "initmysql"}
    assert set(router_containers) == {"router"}

    assert server_containers["mysql"]["resources"]["requests"] == {
        "cpu": "100m",
        "memory": "128Mi",
        "ephemeral-storage": "128Mi",
    }
    assert init_containers["initmysql"]["resources"]["requests"] == {
        "cpu": "100m",
        "memory": "128Mi",
        "ephemeral-storage": "128Mi",
    }
    for container in (
        server_containers["sidecar"],
        init_containers["fixdatadir"],
        init_containers["initconf"],
        router_containers["router"],
    ):
        assert container["resources"]["requests"] == {
            "cpu": "50m",
            "memory": "64Mi",
            "ephemeral-storage": "64Mi",
        }

    dumped_values = yaml.safe_dump(values)
    assert "&id" not in dumped_values
    assert "*id" not in dumped_values


def test_apply_cluster_current_switchover_rbac_recreates_rolebinding_for_role_ref_drift(
    monkeypatch,
    operator_t_module,
):
    deleted = []
    applied = []

    monkeypatch.setattr(
        operator_t_module.kutil,
        "get",
        lambda ns, rsrc, name, check=False, cmd_output_log=None: {
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "mysql-switchover",
            },
        },
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "delete_rolebinding",
        lambda ns, name: deleted.append((ns, name)),
    )
    monkeypatch.setattr(
        operator_t_module.kutil,
        "apply",
        lambda ns, manifest, check=True: applied.append(
            (ns, list(yaml.safe_load_all(manifest)))
        ),
    )

    test_case = operator_t_module.OperatorSingleAndMultipleBaseTest()
    test_case._apply_cluster_current_switchover_rbac_manifests(
        namespace="cluster-ns",
        service_account_manifest={
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {"name": "cluster-a-switchover-sa"},
        },
        role_binding_manifest={
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "cluster-a-switchover-rb"},
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": "manual-drift-role",
            },
            "subjects": [
                {
                    "kind": "ServiceAccount",
                    "name": "manual-drift-sa",
                },
            ],
        },
    )

    assert deleted == [("cluster-ns", "cluster-a-switchover-rb")]
    assert applied[0][0] == "cluster-ns"
    assert [manifest["kind"] for manifest in applied[0][1]] == [
        "ServiceAccount",
        "RoleBinding",
    ]
    assert applied[0][1][1]["roleRef"]["name"] == "manual-drift-role"


def test_default_operator_clusterrole_manifest_can_include_helm_ownership(
    operator_t_module,
):
    manifest = operator_t_module._build_default_operator_clusterrole_manifest(
        "mysql-switchover",
        [{"resources": ["pods"], "verbs": ["get"]}],
        helm_release_name="mysql-operator",
        helm_release_namespace="mysql-operator",
    )

    assert manifest["metadata"] == {
        "name": "mysql-switchover",
        "labels": {"app.kubernetes.io/managed-by": "Helm"},
        "annotations": {
            "meta.helm.sh/release-name": "mysql-operator",
            "meta.helm.sh/release-namespace": "mysql-operator",
        },
    }


def test_sync_existing_operator_clusterrole_merges_missing_rules(
    monkeypatch,
    operator_t_module,
):
    applied = []
    existing_clusterrole = {
        "rules": [
            {
                "apiGroups": ["apps"],
                "resources": ["deployments", "statefulsets"],
                "verbs": ["get", "create", "patch", "update", "watch", "delete"],
            },
            {
                "apiGroups": [""],
                "resources": ["pods"],
                "verbs": ["get", "list"],
            },
        ],
    }

    monkeypatch.setattr(
        operator_t_module.kutil,
        "get",
        lambda ns, rsrc, name, check=False: existing_clusterrole,
    )

    def fake_apply(
        ns,
        manifest,
        *,
        check=True,
        field_manager=None,
        server_side=False,
        force_conflicts=False,
    ):
        applied.append(
            (
                ns,
                yaml.safe_load(manifest),
                check,
                field_manager,
                server_side,
                force_conflicts,
            )
        )

    monkeypatch.setattr(operator_t_module.kutil, "apply", fake_apply)

    operator_t_module._sync_existing_operator_clusterrole_missing_rules(
        "mysql-operator",
        [
            {
                "apiGroups": ["apps"],
                "resources": ["deployments", "statefulsets"],
                "verbs": [
                    "get",
                    "list",
                    "create",
                    "patch",
                    "update",
                    "watch",
                    "delete",
                ],
            },
            {
                "apiGroups": ["apiextensions.k8s.io"],
                "resources": ["customresourcedefinitions"],
                "verbs": ["list", "watch"],
            },
        ],
    )

    assert len(applied) == 1
    ns, manifest, check, field_manager, server_side, force_conflicts = applied[0]
    assert ns is None
    assert check is True
    assert field_manager == "helm"
    assert server_side is True
    assert force_conflicts is True
    assert manifest["apiVersion"] == "rbac.authorization.k8s.io/v1"
    assert manifest["kind"] == "ClusterRole"
    assert manifest["metadata"] == {"name": "mysql-operator"}
    patched_rules = manifest["rules"]
    assert patched_rules[0]["verbs"] == [
        "get",
        "create",
        "patch",
        "update",
        "watch",
        "delete",
        "list",
    ]
    assert patched_rules[1]["verbs"] == ["get", "list"]
    assert patched_rules[2] == {
        "apiGroups": ["apiextensions.k8s.io"],
        "resources": ["customresourcedefinitions"],
        "verbs": ["list", "watch"],
    }
    assert existing_clusterrole["rules"][0]["verbs"] == [
        "get",
        "create",
        "patch",
        "update",
        "watch",
        "delete",
    ]


def test_raw_deploy_dir_for_historic_release_accepts_nested_deploy_layout(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    historic_deploy_path = tmp_path / "deploy-historic"
    nested_release_path = historic_deploy_path / "9.6.0-2.2.7" / "deploy"
    _write_raw_deploy_manifests(nested_release_path)

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_historic_path",
        str(historic_deploy_path),
        raising=False,
    )

    assert (
        operator_t_module.get_raw_deploy_dir_for_release("9.6.0-2.2.7")
        == str(nested_release_path)
    )


def test_raw_deploy_dir_for_release_reports_missing_manifests(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    historic_deploy_path = tmp_path / "deploy-historic"
    (historic_deploy_path / "9.6.0-2.2.7").mkdir(parents=True)

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_historic_path",
        str(historic_deploy_path),
        raising=False,
    )

    with pytest.raises(FileNotFoundError, match="9.6.0-2.2.7"):
        operator_t_module.get_raw_deploy_dir_for_release("9.6.0-2.2.7")


def test_missing_raw_deploy_manifest_release_details_reports_unconfigured_historic_release(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    current_deploy_path = tmp_path / "deploy"
    _write_raw_deploy_manifests(current_deploy_path)

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_path",
        str(current_deploy_path),
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_historic_path",
        "",
        raising=False,
    )

    missing = operator_t_module.get_missing_raw_deploy_manifest_release_details(
        [
            "9.6.0-2.2.7",
            operator_t_module.g_ts_cfg.operator_version_tag,
        ]
    )

    assert len(missing) == 1
    assert missing[0].startswith("9.6.0-2.2.7:")
    assert "Raw deploy manifest paths are not configured" in missing[0]


def test_require_raw_deploy_manifest_releases_skips_when_artifacts_missing(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    current_deploy_path = tmp_path / "deploy"
    _write_raw_deploy_manifests(current_deploy_path)

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_path",
        str(current_deploy_path),
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_historic_path",
        "",
        raising=False,
    )

    class DummyTest(operator_t_module.OperatorSingleAndMultipleBaseTest):
        def runTest(self):
            pass

    with pytest.raises(unittest.SkipTest, match="Required raw deploy manifest"):
        DummyTest()._require_raw_deploy_manifest_releases_or_skip(
            ["9.6.0-2.2.7"]
        )


def test_raw_manifest_upgrade_release_chain_bridges_lts_to_current(
    monkeypatch,
    operator_t_module,
):
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_current_lts_version_tag",
        "8.4.7-2.1.9",
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_version_tag",
        "9.7.0-2.2.8",
        raising=False,
    )

    assert operator_t_module.get_raw_manifest_upgrade_release_chain() == [
        "8.4.7-2.1.9",
        "9.6.0-2.2.7",
        "9.7.0-2.2.8",
    ]


def test_render_raw_deploy_manifest_patches_operator_image_for_test_environment(
    monkeypatch,
    tmp_path,
    operator_t_module,
):
    current_deploy_path = tmp_path / "deploy"
    _write_raw_deploy_manifests(current_deploy_path)

    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "deploy_path",
        str(current_deploy_path),
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "image_registry",
        "registry.example.com:5000",
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "image_repository",
        "mysql",
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_image_name",
        "community-operator",
        raising=False,
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_pull_policy",
        "Never",
        raising=False,
    )

    rendered = operator_t_module.render_raw_deploy_manifest_for_test_environment(
        operator_t_module.g_ts_cfg.operator_version_tag,
        "deploy-operator.yaml",
    )
    deployment = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "Deployment"
    )
    cluster_role = next(
        doc
        for doc in yaml.safe_load_all(rendered)
        if doc and doc.get("kind") == "ClusterRole"
    )
    container = deployment["spec"]["template"]["spec"]["containers"][0]
    envs = {env["name"]: env["value"] for env in container["env"]}
    apps_rule = next(
        rule
        for rule in cluster_role["rules"]
        if "apps" in rule["apiGroups"]
        and "deployments" in rule["resources"]
    )

    assert (
        container["image"]
        == "registry.example.com:5000/mysql/community-operator:"
        f"{operator_t_module.g_ts_cfg.operator_version_tag}"
    )
    assert container["imagePullPolicy"] == "Never"
    assert envs["MYSQL_OPERATOR_DEFAULT_REPOSITORY"] == (
        "registry.example.com:5000/mysql"
    )
    assert envs["MYSQL_OPERATOR_IMAGE_PULL_POLICY"] == "Never"
    assert "list" in apps_rule["verbs"]


def test_selector_helper_outputs_match_chart_semantics(operator_t_module):
    service_selector = operator_t_module.get_service_selector_labels(
        "operator-ns",
        "custom-op",
    )

    assert service_selector == {
        "app.kubernetes.io/name": "mysql-operator",
        "app.kubernetes.io/instance": "operator-ns-custom-op-operator",
        "app.kubernetes.io/component": "controller",
    }
    assert operator_t_module.get_legacy_deployment_selector_labels() == {
        "name": "mysql-operator",
    }
    assert operator_t_module.get_deployment_selector_labels(
        "operator-ns",
        "custom-op",
    ) == {
        "name": "custom-op",
        **service_selector,
    }
    assert operator_t_module.get_selector_labels(
        "release-a",
        "operator-ns",
        "custom-op",
    ) == {
        "name": "custom-op",
        **service_selector,
    }


@pytest.mark.parametrize(
    "manifest_path",
    [
        TESTS_ROOT.parent / "deploy" / "deploy-operator.yaml",
        TESTS_ROOT.parent / "internal" / "deploy" / "deploy-operator.yaml",
    ],
)
def test_checked_in_raw_operator_manifest_selector_is_upgrade_compatible(
    manifest_path,
):
    deployment = next(
        doc
        for doc in yaml.safe_load_all(manifest_path.read_text(encoding="utf8"))
        if doc and doc.get("kind") == "Deployment"
    )

    selector_labels = deployment["spec"]["selector"]["matchLabels"]
    template_labels = deployment["spec"]["template"]["metadata"]["labels"]

    assert selector_labels == {
        "name": "mysql-operator",
    }
    for key, value in selector_labels.items():
        assert template_labels[key] == value
    assert template_labels["app.kubernetes.io/name"] == "mysql-operator"
    assert template_labels["app.kubernetes.io/instance"] == "mysql-operator"
    assert template_labels["app.kubernetes.io/component"] == "controller"


def test_get_patched_artifacts_uses_fresh_install_selector_helpers(
    operator_t_module,
):
    patched = operator_t_module.get_patched_artifacts(
        copy.deepcopy(_make_selector_test_artifacts()),
        "release-a",
        "operator-ns",
        "custom-op",
        watch_namespaces="operator-ns",
        standalone=True,
    )

    expected_deployment_selector = operator_t_module.get_deployment_selector_labels(
        "operator-ns",
        "custom-op",
    )
    expected_service_selector = operator_t_module.get_service_selector_labels(
        "operator-ns",
        "custom-op",
    )
    deployment = patched["deployment"]
    template_labels = deployment["spec"]["template"]["metadata"]["labels"]
    envs = operator_t_module.get_operator_envs_from_deployment(deployment)

    assert deployment["metadata"]["name"] == "custom-op"
    assert deployment["metadata"]["namespace"] == "operator-ns"
    assert deployment["spec"]["selector"]["matchLabels"] == (
        expected_deployment_selector
    )
    assert template_labels["name"] == "custom-op"
    for key, value in expected_deployment_selector.items():
        assert template_labels[key] == value
    for key, value in expected_service_selector.items():
        assert template_labels[key] == value
    assert envs["OPERATOR_DEPLOYMENT_NAME"] == "custom-op"
    assert envs["OPERATOR_NAMESPACES"] == "operator-ns"
    assert envs["OPERATOR_STANDALONE"] == "true"


def test_get_patched_artifacts_sets_recreate_strategy_for_standalone(
    operator_t_module,
):
    patched = operator_t_module.get_patched_artifacts(
        copy.deepcopy(_make_selector_test_artifacts()),
        "release-a",
        "operator-ns",
        "custom-op",
        watch_namespaces="operator-ns",
        standalone=True,
    )

    assert patched["deployment"]["spec"]["strategy"] == {
        "type": "Recreate",
    }


def test_get_patched_artifacts_canonicalizes_topology_env_namespace_sets(
    operator_t_module,
):
    patched = operator_t_module.get_patched_artifacts(
        copy.deepcopy(_make_selector_test_artifacts()),
        "release-a",
        "operator-ns",
        "custom-op",
        watch_namespaces="ns-b, ns-a, ns-b",
        standalone=False,
    )

    deployment = patched["deployment"]
    envs = operator_t_module.get_operator_envs_from_deployment(deployment)

    assert envs["OPERATOR_NAMESPACES"] == "ns-b,ns-a"
    assert envs["OPERATOR_STANDALONE"] == "false"
