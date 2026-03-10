import copy
import importlib
import json
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
    topology_annotation = deployment["metadata"]["annotations"][
        operator_t_module.TOPOLOGY_ANNOTATION_KEY
    ]

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
    assert json.loads(topology_annotation) == {
        "version": 1,
        "scope": "scoped",
        "standalone": True,
        "namespaces": ["operator-ns"],
    }


def test_get_patched_artifacts_canonicalizes_topology_annotation_namespace_sets(
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
    topology_annotation = json.loads(
        deployment["metadata"]["annotations"][
            operator_t_module.TOPOLOGY_ANNOTATION_KEY
        ]
    )

    assert envs["OPERATOR_NAMESPACES"] == "ns-b,ns-a"
    assert topology_annotation == {
        "version": 1,
        "scope": "scoped",
        "standalone": False,
        "namespaces": ["ns-a", "ns-b"],
    }
