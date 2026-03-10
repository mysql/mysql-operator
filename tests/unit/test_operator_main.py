import importlib
import sys
import types

import pytest
from kubernetes.client import (
    V1Container,
    V1Deployment,
    V1DeploymentSpec,
    V1LabelSelector,
    V1ObjectMeta,
    V1PodSpec,
    V1PodTemplateSpec,
)


@pytest.fixture(autouse=True)
def cleanup_operator_main_module_stubs():
    yield

    for module_name in [
        "mysqloperator.operator_main",
        "mysqloperator.controller.kubeutils",
        "mysqloperator.controller.k8sobject",
        "mysqloperator.controller.config",
        "mysqloperator.controller.operator",
        "mysqloperator.controller.operator_topology",
        "mysqloperator.controller.watched_namespaces",
        "mysqlsh",
        "kopf",
    ]:
        sys.modules.pop(module_name, None)


def _load_operator_main_module():
    for module_name in [
        "mysqloperator.operator_main",
        "mysqloperator.controller.kubeutils",
        "mysqloperator.controller.k8sobject",
        "mysqloperator.controller.config",
        "mysqloperator.controller.operator",
        "mysqloperator.controller.operator_topology",
        "mysqloperator.controller.watched_namespaces",
        "mysqlsh",
        "kopf",
    ]:
        sys.modules.pop(module_name, None)

    mysqlsh_stub = types.ModuleType("mysqlsh")
    mysqlsh_stub.globals = types.SimpleNamespace(
        shell=types.SimpleNamespace(
            options=types.SimpleNamespace(useWizards=False, logLevel=0, verbose=0)
        )
    )
    sys.modules["mysqlsh"] = mysqlsh_stub

    kopf_stub = types.ModuleType("kopf")
    kopf_stub.operator = None
    kopf_stub.configure = lambda **kwargs: None
    sys.modules["kopf"] = kopf_stub

    k8sobject_stub = types.ModuleType("mysqloperator.controller.k8sobject")
    k8sobject_stub.g_component = None
    k8sobject_stub.g_host = None
    sys.modules["mysqloperator.controller.k8sobject"] = k8sobject_stub

    config_stub = types.ModuleType("mysqloperator.controller.config")
    config_stub.config_from_env = lambda: None
    config_stub.debug = 0
    sys.modules["mysqloperator.controller.config"] = config_stub

    operator_stub = types.ModuleType("mysqloperator.controller.operator")
    sys.modules["mysqloperator.controller.operator"] = operator_stub

    kubeutils_stub = types.ModuleType("mysqloperator.controller.kubeutils")
    kubeutils_stub.ApiException = Exception
    kubeutils_stub.api_apps = types.SimpleNamespace(
        read_namespaced_deployment=lambda name, namespace: None,
        patch_namespaced_deployment=lambda name, namespace, body: None,
        list_deployment_for_all_namespaces=lambda: types.SimpleNamespace(items=[]),
    )
    kubeutils_stub.k8s_cluster_domain = lambda logger: "cluster.local"
    sys.modules["mysqloperator.controller.kubeutils"] = kubeutils_stub

    return importlib.import_module("mysqloperator.operator_main")


def test_get_kopf_namespaces_returns_empty_list_for_clusterwide():
    operator_main = _load_operator_main_module()

    assert operator_main.get_kopf_namespaces(None) == []


def test_get_kopf_namespaces_preserves_explicit_namespaces():
    operator_main = _load_operator_main_module()

    assert operator_main.get_kopf_namespaces(["ns1", "ns2"]) == ["ns1", "ns2"]


def _make_operator_deployment(
    *,
    namespace: str,
    name: str,
    annotations: dict | None = None,
    env: list[dict] | None = None,
    raw_manifest: bool = False,
    generation: int | None = None,
    strategy: dict | None = None,
):
    labels = {
        "app.kubernetes.io/name": "mysql-operator",
        "app.kubernetes.io/component": "controller",
    }
    if raw_manifest:
        labels.update(
            {
                "app.kubernetes.io/managed-by": "mysql-operator",
                "app.kubernetes.io/created-by": "mysql-operator",
            }
        )

    metadata = {
        "namespace": namespace,
        "name": name,
        "annotations": annotations or {},
        "labels": labels,
    }
    if generation is not None:
        metadata["generation"] = generation

    spec = {
        "replicas": 1,
        "selector": {
            "matchLabels": {
                "name": name,
                "app.kubernetes.io/name": "mysql-operator",
                "app.kubernetes.io/component": "controller",
            }
        },
        "template": {
            "metadata": {
                "labels": {
                    "name": name,
                    "app.kubernetes.io/name": "mysql-operator",
                    "app.kubernetes.io/component": "controller",
                }
            },
            "spec": {
                "containers": [
                    {
                        "name": "mysql-operator",
                        "env": env or [],
                    }
                ]
            },
        },
    }
    if strategy is not None:
        spec["strategy"] = strategy

    return {
        "metadata": metadata,
        "spec": spec,
    }


def _make_typed_legacy_global_operator_deployment(*, namespace: str) -> V1Deployment:
    return V1Deployment(
        metadata=V1ObjectMeta(
            namespace=namespace,
            name="mysql-operator",
            annotations={},
            labels={"name": "mysql-operator"},
        ),
        spec=V1DeploymentSpec(
            replicas=1,
            selector=V1LabelSelector(
                match_labels={"name": "mysql-operator"},
            ),
            template=V1PodTemplateSpec(
                metadata=V1ObjectMeta(
                    labels={"name": "mysql-operator"},
                ),
                spec=V1PodSpec(
                    containers=[V1Container(name="mysql-operator", env=[])],
                ),
            ),
        ),
    )


class _FakeLoop:
    def __init__(self):
        self.run_until_complete_called = False
        self.value = None

    def run_until_complete(self, value):
        self.run_until_complete_called = True
        self.value = value
        return None


def _set_operator_main_env(monkeypatch, **env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)


def test_main_bootstraps_missing_global_annotation_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()
    patch_calls = []

    deployment = _make_operator_deployment(
        namespace="operator-ns",
        name="mysql-operator",
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": ""},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        raw_manifest=True,
        generation=1,
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: patch_calls.append((name, namespace, body))
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[deployment])
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="false",
        OPERATOR_NAMESPACES="",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    assert operator_main.main([]) == 0
    assert patch_calls == [
        (
            "mysql-operator",
            "operator-ns",
            {
                "metadata": {
                    "annotations": {
                        "mysql.oracle.com/operator-topology": (
                            '{"version":1,"scope":"global","standalone":false,"namespaces":[]}'
                        )
                    }
                }
            },
        )
    ]
    assert fake_loop.run_until_complete_called is True
    assert fake_loop.value["clusterwide"] is True
    assert fake_loop.value["namespaces"] == []
    assert fake_loop.value["peering_name"] == "mysql-operator"
    assert fake_loop.value["standalone"] is False
    assert isinstance(fake_loop.value["priority"], int)


def test_main_bootstraps_typed_legacy_global_annotation_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()
    patch_calls = []

    deployment = _make_typed_legacy_global_operator_deployment(
        namespace="operator-ns",
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: patch_calls.append((name, namespace, body))
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[deployment])
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="false",
        OPERATOR_NAMESPACES="",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    assert operator_main.main([]) == 0
    assert patch_calls == [
        (
            "mysql-operator",
            "operator-ns",
            {
                "metadata": {
                    "annotations": {
                        "mysql.oracle.com/operator-topology": (
                            '{"version":1,"scope":"global","standalone":false,"namespaces":[]}'
                        )
                    }
                }
            },
        )
    ]
    assert fake_loop.run_until_complete_called is True
    assert fake_loop.value["clusterwide"] is True
    assert fake_loop.value["namespaces"] == []
    assert fake_loop.value["peering_name"] == "mysql-operator"
    assert fake_loop.value["standalone"] is False
    assert isinstance(fake_loop.value["priority"], int)


def test_main_bootstraps_missing_scoped_annotation_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()
    patch_calls = []

    deployment = _make_operator_deployment(
        namespace="operator-ns",
        name="mysql-operator",
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-b,team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        raw_manifest=True,
        generation=1,
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: patch_calls.append((name, namespace, body))
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[deployment])
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="false",
        OPERATOR_NAMESPACES="team-b,team-a",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    assert operator_main.main([]) == 0
    assert patch_calls == [
        (
            "mysql-operator",
            "operator-ns",
            {
                "metadata": {
                    "annotations": {
                        "mysql.oracle.com/operator-topology": (
                            '{"version":1,"scope":"scoped","standalone":false,"namespaces":["team-a","team-b"]}'
                        )
                    }
                }
            },
        )
    ]
    assert fake_loop.run_until_complete_called is True
    assert fake_loop.value["clusterwide"] is False
    assert fake_loop.value["namespaces"] == ["team-a", "team-b"]
    assert fake_loop.value["peering_name"] == "mysql-operator"
    assert fake_loop.value["standalone"] is False


def test_main_bootstraps_missing_standalone_annotation_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()
    patch_calls = []
    safety_calls = []

    deployment = _make_operator_deployment(
        namespace="operator-ns",
        name="mysql-operator",
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "true"},
        ],
        raw_manifest=True,
        generation=1,
        strategy={"type": "Recreate"},
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: patch_calls.append((name, namespace, body))
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[deployment])
    )
    def _fake_ensure_standalone_safe_deployment(standalone, namespace, deployment_name):
        assert len(patch_calls) == 1
        safety_calls.append((standalone, namespace, deployment_name))

    operator_main.ensure_standalone_safe_deployment = (
        _fake_ensure_standalone_safe_deployment
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="true",
        OPERATOR_NAMESPACES="team-a",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    assert operator_main.main([]) == 0
    assert patch_calls == [
        (
            "mysql-operator",
            "operator-ns",
            {
                "metadata": {
                    "annotations": {
                        "mysql.oracle.com/operator-topology": (
                            '{"version":1,"scope":"scoped","standalone":true,"namespaces":["team-a"]}'
                        )
                    }
                }
            },
        )
    ]
    assert safety_calls == [
        (True, "operator-ns", "mysql-operator")
    ]
    assert fake_loop.run_until_complete_called is True
    assert fake_loop.value["clusterwide"] is False
    assert fake_loop.value["namespaces"] == ["team-a"]
    assert fake_loop.value["peering_name"] is None
    assert fake_loop.value["standalone"] is True


def test_main_rejects_empty_persisted_annotation_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()

    deployment = _make_operator_deployment(
        namespace="operator-ns",
        name="mysql-operator",
        annotations={
            "mysql.oracle.com/operator-topology": "",
        },
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        raw_manifest=True,
        generation=1,
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: pytest.fail("topology annotation should not be patched")
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[deployment])
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="false",
        OPERATOR_NAMESPACES="team-a",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    with pytest.raises(
        RuntimeError,
        match=r"has invalid mysql\.oracle\.com/operator-topology",
    ):
        operator_main.main([])

    assert fake_loop.run_until_complete_called is False


def test_main_rejects_persisted_topology_change_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()

    deployment = _make_operator_deployment(
        namespace="operator-ns",
        name="mysql-operator",
        annotations={
            "mysql.oracle.com/operator-topology": (
                '{"version":1,"scope":"global","standalone":false,"namespaces":[]}'
            )
        },
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": ""},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: pytest.fail("topology annotation should not be patched")
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[deployment])
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="false",
        OPERATOR_NAMESPACES="team-a",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    with pytest.raises(
        RuntimeError,
        match=r"persisted mysql\.oracle\.com/operator-topology as global non-standalone",
    ):
        operator_main.main([])

    assert fake_loop.run_until_complete_called is False


def test_main_rejects_overlapping_operator_before_startup(monkeypatch):
    operator_main = _load_operator_main_module()
    fake_loop = _FakeLoop()

    current_deployment = _make_operator_deployment(
        namespace="operator-ns",
        name="mysql-operator",
        annotations={
            "mysql.oracle.com/operator-topology": (
                '{"version":1,"scope":"scoped","standalone":false,"namespaces":["team-a"]}'
            )
        },
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
    )
    other_deployment = _make_operator_deployment(
        namespace="other-operator-ns",
        name="other-operator",
        annotations={
            "mysql.oracle.com/operator-topology": (
                '{"version":1,"scope":"scoped","standalone":true,"namespaces":["team-a","team-b"]}'
            )
        },
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a,team-b"},
            {"name": "OPERATOR_STANDALONE", "value": "true"},
        ],
    )

    operator_main.api_apps.read_namespaced_deployment = (
        lambda name, namespace: current_deployment
    )
    operator_main.api_apps.patch_namespaced_deployment = (
        lambda name, namespace, body: pytest.fail("topology annotation should not be patched")
    )
    operator_main.api_apps.list_deployment_for_all_namespaces = (
        lambda: types.SimpleNamespace(items=[current_deployment, other_deployment])
    )
    operator_main.kopf.operator = lambda **kwargs: kwargs
    monkeypatch.setattr(operator_main.asyncio, "get_event_loop", lambda: fake_loop)

    _set_operator_main_env(
        monkeypatch,
        OPERATOR_STANDALONE="false",
        OPERATOR_NAMESPACES="team-a",
        OPERATOR_DEPLOYMENT_NAME="mysql-operator",
        POD_NAMESPACE="operator-ns",
    )

    with pytest.raises(
        RuntimeError,
        match=r"existing operator other-operator-ns/other-operator; overlapping namespaces: team-a",
    ):
        operator_main.main([])

    assert fake_loop.run_until_complete_called is False
