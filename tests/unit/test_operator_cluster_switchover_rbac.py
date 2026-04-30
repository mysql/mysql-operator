# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import pathlib
import sys
import types

import pytest


@pytest.fixture
def operator_cluster_module(monkeypatch):
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    for module_name in [
        "mysqloperator.controller.innodbcluster.operator_cluster",
        "mysqloperator.controller.api_utils",
        "mysqloperator.controller.consts",
        "mysqloperator.controller.kubeutils",
        "mysqloperator.controller.config",
        "mysqloperator.controller.utils",
        "mysqloperator.controller.errors",
        "mysqloperator.controller.diagnose",
        "mysqloperator.controller.shellutils",
        "mysqloperator.controller.group_monitor",
        "mysqloperator.controller.backup",
        "mysqloperator.controller.backup.backup_objects",
        "mysqloperator.controller.innodbcluster.cluster_controller",
        "mysqloperator.controller.innodbcluster.cluster_objects",
        "mysqloperator.controller.innodbcluster.router_objects",
        "mysqloperator.controller.innodbcluster.cluster_api",
        "mysqlsh",
        "kopf",
        "kopf._cogs",
        "kopf._cogs.structs",
        "kopf._cogs.structs.bodies",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
    ]:
        sys.modules.pop(module_name, None)

    kubernetes_stub = types.ModuleType("kubernetes")
    kubernetes_client_stub = types.ModuleType("kubernetes.client")
    kubernetes_rest_stub = types.ModuleType("kubernetes.client.rest")

    class ApiException(Exception):
        def __init__(self, status=None):
            super().__init__(f"status={status}")
            self.status = status

    class V1DeleteOptions:
        def __init__(self, grace_period_seconds=None):
            self.grace_period_seconds = grace_period_seconds

    kubernetes_rest_stub.ApiException = ApiException
    kubernetes_client_stub.V1DeleteOptions = V1DeleteOptions
    kubernetes_client_stub.rest = kubernetes_rest_stub
    kubernetes_stub.client = kubernetes_client_stub
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.client", kubernetes_client_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", kubernetes_rest_stub)

    def _decorator(*args, **kwargs):
        def _wrap(func):
            return func

        return _wrap

    kopf_stub = types.ModuleType("kopf")
    kopf_stub.on = types.SimpleNamespace(
        create=_decorator,
        delete=_decorator,
        field=_decorator,
    )
    kopf_stub.adopt = lambda *args, **kwargs: None
    kopf_stub.Patch = dict
    kopf_stub.TemporaryError = Exception
    kopf_stub.PermanentError = Exception
    monkeypatch.setitem(sys.modules, "kopf", kopf_stub)

    kopf_cogs_stub = types.ModuleType("kopf._cogs")
    kopf_structs_stub = types.ModuleType("kopf._cogs.structs")
    kopf_bodies_stub = types.ModuleType("kopf._cogs.structs.bodies")
    kopf_bodies_stub.Body = dict
    monkeypatch.setitem(sys.modules, "kopf._cogs", kopf_cogs_stub)
    monkeypatch.setitem(sys.modules, "kopf._cogs.structs", kopf_structs_stub)
    monkeypatch.setitem(sys.modules, "kopf._cogs.structs.bodies", kopf_bodies_stub)

    mysqlsh_stub = types.ModuleType("mysqlsh")
    mysqlsh_stub.Error = Exception
    monkeypatch.setitem(sys.modules, "mysqlsh", mysqlsh_stub)

    api_utils_stub = types.ModuleType("mysqloperator.controller.api_utils")
    api_utils_stub.ApiSpecError = type("ApiSpecError", (Exception,), {})
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.api_utils",
        api_utils_stub,
    )

    consts_stub = types.ModuleType("mysqloperator.controller.consts")
    consts_stub.GROUP = "mysql.oracle.com"
    consts_stub.VERSION = "v2"
    consts_stub.INNODBCLUSTER_PLURAL = "innodbclusters"
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.consts", consts_stub)

    config_stub = types.ModuleType("mysqloperator.controller.config")
    config_stub.DEFAULT_OPERATOR_VERSION_TAG = "9.7.0-2.2.8"

    class Edition:
        enterprise = "enterprise"

    config_stub.Edition = Edition
    config_stub.OPERATOR_EDITION = "community"
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.config", config_stub)

    utils_stub = types.ModuleType("mysqloperator.controller.utils")
    utils_stub.g_ephemeral_pod_state = types.SimpleNamespace(
        get=lambda *args, **kwargs: None,
        set=lambda *args, **kwargs: None,
    )
    utils_stub.ephemeral_value_changed = lambda *args, **kwargs: False
    utils_stub.isotime = lambda: "2026-01-01T00:00:00Z"
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.utils", utils_stub)

    errors_stub = types.ModuleType("mysqloperator.controller.errors")
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.errors", errors_stub)

    diagnose_stub = types.ModuleType("mysqloperator.controller.diagnose")
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.diagnose",
        diagnose_stub,
    )

    shellutils_stub = types.ModuleType("mysqloperator.controller.shellutils")
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.shellutils",
        shellutils_stub,
    )

    group_monitor_stub = types.ModuleType("mysqloperator.controller.group_monitor")
    group_monitor_stub.g_group_monitor = types.SimpleNamespace(
        monitor_cluster=lambda *args, **kwargs: None,
        remove_cluster=lambda *args, **kwargs: None,
    )
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.group_monitor",
        group_monitor_stub,
    )

    backup_objects_stub = types.ModuleType(
        "mysqloperator.controller.backup.backup_objects"
    )
    backup_objects_stub.ensure_schedules_use_current_image = (
        lambda *args, **kwargs: None
    )
    backup_package_stub = types.ModuleType("mysqloperator.controller.backup")
    backup_package_stub.backup_objects = backup_objects_stub
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.backup", backup_package_stub)
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.backup.backup_objects",
        backup_objects_stub,
    )

    kubeutils_stub = types.ModuleType("mysqloperator.controller.kubeutils")
    kubeutils_stub.api_core = types.SimpleNamespace()
    kubeutils_stub.api_apps = types.SimpleNamespace()
    kubeutils_stub.api_policy = types.SimpleNamespace()
    kubeutils_stub.api_rbac = types.SimpleNamespace()
    kubeutils_stub.api_customobj = types.SimpleNamespace()
    kubeutils_stub.api_batch = types.SimpleNamespace()
    kubeutils_stub.k8s_version = lambda: "1.30"
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.kubeutils",
        kubeutils_stub,
    )

    cluster_controller_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.cluster_controller"
    )

    class ClusterController:
        pass

    class ClusterMutex:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    cluster_controller_stub.ClusterController = ClusterController
    cluster_controller_stub.ClusterMutex = ClusterMutex
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.innodbcluster.cluster_controller",
        cluster_controller_stub,
    )

    cluster_objects_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.cluster_objects"
    )
    cluster_objects_stub.ReadReplicaSpec = type("ReadReplicaSpec", (), {})
    cluster_objects_stub.InnoDBClusterObjectModifier = type(
        "InnoDBClusterObjectModifier",
        (),
        {},
    )
    router_objects_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.router_objects"
    )
    cluster_api_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.cluster_api"
    )

    class InnoDBCluster:
        deleting = False

    class InnoDBClusterSpec:
        pass

    class MySQLPod:
        pass

    cluster_api_stub.InnoDBCluster = InnoDBCluster
    cluster_api_stub.InnoDBClusterSpec = InnoDBClusterSpec
    cluster_api_stub.MySQLPod = MySQLPod
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.innodbcluster.cluster_objects",
        cluster_objects_stub,
    )
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.innodbcluster.router_objects",
        router_objects_stub,
    )
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.innodbcluster.cluster_api",
        cluster_api_stub,
    )

    module = importlib.import_module(
        "mysqloperator.controller.innodbcluster.operator_cluster"
    )
    yield module
    sys.modules.pop("mysqloperator.controller.innodbcluster.operator_cluster", None)


def test_failover_create_handler_is_registered_for_community_operator(
    operator_cluster_module,
):
    assert callable(operator_cluster_module.on_failover_create)


def _owner_reference(
    *,
    api_version: str,
    kind: str,
    name: str,
    uid: str,
    controller: bool,
    block_owner_deletion: bool,
):
    return types.SimpleNamespace(
        api_version=api_version,
        kind=kind,
        name=name,
        uid=uid,
        controller=controller,
        block_owner_deletion=block_owner_deletion,
    )


def test_prepare_switchover_service_account_patch_repairs_owner_refs_and_clears_pull_secrets(
    operator_cluster_module,
):
    desired_owner_references = [
        {
            "apiVersion": "mysql.oracle.com/v2",
            "kind": "InnoDBCluster",
            "name": "cluster-a",
            "uid": "cluster-a-uid",
            "controller": True,
            "blockOwnerDeletion": True,
        }
    ]
    current_service_account = types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            owner_references=[
                _owner_reference(
                    api_version="v1",
                    kind="Secret",
                    name="foreign-owner",
                    uid="foreign-owner-uid",
                    controller=False,
                    block_owner_deletion=False,
                )
            ]
        ),
        image_pull_secrets=[types.SimpleNamespace(name="stale-pull-secret")],
    )

    patch = operator_cluster_module._prepare_switchover_service_account_patch(
        current_service_account,
        {
            "metadata": {
                "ownerReferences": desired_owner_references,
            }
        },
    )

    assert patch == {
        "metadata": {
            "ownerReferences": desired_owner_references,
        },
        "imagePullSecrets": [],
    }


def test_prepare_switchover_service_account_patch_returns_none_for_matching_state(
    operator_cluster_module,
):
    desired_owner_references = [
        {
            "apiVersion": "mysql.oracle.com/v2",
            "kind": "InnoDBCluster",
            "name": "cluster-a",
            "uid": "cluster-a-uid",
            "controller": True,
            "blockOwnerDeletion": True,
        }
    ]
    current_service_account = types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            owner_references=[
                _owner_reference(
                    api_version="mysql.oracle.com/v2",
                    kind="InnoDBCluster",
                    name="cluster-a",
                    uid="cluster-a-uid",
                    controller=True,
                    block_owner_deletion=True,
                )
            ]
        ),
        image_pull_secrets=[],
    )

    patch = operator_cluster_module._prepare_switchover_service_account_patch(
        current_service_account,
        {
            "metadata": {
                "ownerReferences": desired_owner_references,
            }
        },
    )

    assert patch is None


def test_classify_switchover_role_binding_repair_returns_patch_for_owner_and_subject_drift(
    operator_cluster_module,
):
    desired_owner_references = [
        {
            "apiVersion": "mysql.oracle.com/v2",
            "kind": "InnoDBCluster",
            "name": "cluster-a",
            "uid": "cluster-a-uid",
            "controller": True,
            "blockOwnerDeletion": True,
        }
    ]
    desired_role_binding = {
        "metadata": {
            "ownerReferences": desired_owner_references,
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": "cluster-a-switchover-sa",
            }
        ],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "mysql-switchover",
        },
    }
    current_role_binding = types.SimpleNamespace(
        metadata=types.SimpleNamespace(
            owner_references=[
                _owner_reference(
                    api_version="v1",
                    kind="ConfigMap",
                    name="foreign-owner",
                    uid="foreign-owner-uid",
                    controller=False,
                    block_owner_deletion=False,
                )
            ]
        ),
        subjects=[types.SimpleNamespace(kind="ServiceAccount", name="drifted-sa")],
        role_ref=types.SimpleNamespace(
            api_group="rbac.authorization.k8s.io",
            kind="ClusterRole",
            name="mysql-switchover",
        ),
    )

    action, patch = operator_cluster_module._classify_switchover_role_binding_repair(
        current_role_binding,
        desired_role_binding,
        "test-ns",
    )

    assert action == "patch"
    assert patch == {
        "metadata": {
            "ownerReferences": desired_owner_references,
        },
        "subjects": desired_role_binding["subjects"],
    }


def test_classify_switchover_role_binding_repair_returns_recreate_for_role_ref_drift(
    operator_cluster_module,
):
    desired_role_binding = {
        "metadata": {
            "ownerReferences": [],
        },
        "subjects": [
            {
                "kind": "ServiceAccount",
                "name": "cluster-a-switchover-sa",
            }
        ],
        "roleRef": {
            "apiGroup": "rbac.authorization.k8s.io",
            "kind": "ClusterRole",
            "name": "mysql-switchover",
        },
    }
    current_role_binding = types.SimpleNamespace(
        metadata=types.SimpleNamespace(owner_references=[]),
        subjects=[
            types.SimpleNamespace(
                kind="ServiceAccount",
                name="cluster-a-switchover-sa",
            )
        ],
        role_ref=types.SimpleNamespace(
            api_group="rbac.authorization.k8s.io",
            kind="ClusterRole",
            name="different-clusterrole",
        ),
    )

    action, patch = operator_cluster_module._classify_switchover_role_binding_repair(
        current_role_binding,
        desired_role_binding,
        "test-ns",
    )

    assert action == "recreate"
    assert patch is None


def _make_startup_cluster(
    *,
    namespace: str,
    name: str,
    deleting: bool = False,
):
    return types.SimpleNamespace(
        namespace=namespace,
        name=name,
        deleting=deleting,
    )


def test_ensure_switchover_rbac_uptodate_retries_all_non_deleting_clusters_before_raising(
    operator_cluster_module,
    monkeypatch,
):
    attempted_clusters = []
    logger = types.SimpleNamespace()

    def fake_reconcile(cluster, logger):
        attempted_clusters.append(f"{cluster.namespace}/{cluster.name}")
        if cluster.name == "cluster-fail":
            raise RuntimeError("forbidden")

    monkeypatch.setattr(
        operator_cluster_module,
        "reconcile_switchover_rbac",
        fake_reconcile,
    )

    clusters = [
        _make_startup_cluster(namespace="ns-a", name="cluster-fail"),
        _make_startup_cluster(namespace="ns-b", name="cluster-ok"),
        _make_startup_cluster(namespace="ns-c", name="cluster-deleting", deleting=True),
    ]

    with pytest.raises(
        RuntimeError,
        match=(
            r"Failed to reconcile switchover RBAC during startup for clusters: "
            r"ns-a/cluster-fail: forbidden"
        ),
    ):
        operator_cluster_module.ensure_switchover_rbac_uptodate(clusters, logger)

    assert attempted_clusters == [
        "ns-a/cluster-fail",
        "ns-b/cluster-ok",
    ]


def test_ensure_switchover_rbac_uptodate_reports_all_failed_clusters(
    operator_cluster_module,
    monkeypatch,
):
    attempted_clusters = []
    logger = types.SimpleNamespace()

    def fake_reconcile(cluster, logger):
        attempted_clusters.append(f"{cluster.namespace}/{cluster.name}")
        if cluster.name == "cluster-fail-a":
            raise RuntimeError("serviceaccounts is forbidden")
        if cluster.name == "cluster-fail-b":
            raise RuntimeError("rolebindings is forbidden")

    monkeypatch.setattr(
        operator_cluster_module,
        "reconcile_switchover_rbac",
        fake_reconcile,
    )

    clusters = [
        _make_startup_cluster(namespace="ns-a", name="cluster-fail-a"),
        _make_startup_cluster(namespace="ns-b", name="cluster-fail-b"),
    ]

    with pytest.raises(RuntimeError) as exc_info:
        operator_cluster_module.ensure_switchover_rbac_uptodate(clusters, logger)

    message = str(exc_info.value)
    assert "ns-a/cluster-fail-a: serviceaccounts is forbidden" in message
    assert "ns-b/cluster-fail-b: rolebindings is forbidden" in message
    assert attempted_clusters == [
        "ns-a/cluster-fail-a",
        "ns-b/cluster-fail-b",
    ]
