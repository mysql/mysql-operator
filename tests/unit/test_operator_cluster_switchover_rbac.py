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

    class V1StatefulSet:
        pass

    kubernetes_rest_stub.ApiException = ApiException
    kubernetes_client_stub.V1DeleteOptions = V1DeleteOptions
    kubernetes_client_stub.V1StatefulSet = V1StatefulSet
    kubernetes_client_stub.rest = kubernetes_rest_stub
    kubernetes_stub.client = kubernetes_client_stub
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.client", kubernetes_client_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", kubernetes_rest_stub)

    registered_create_handlers = []

    def _decorator(*args, **kwargs):
        def _wrap(func):
            registered_create_handlers.append((args, kwargs, func))
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
    config_stub.DEFAULT_OPERATOR_VERSION_TAG = "26.7.0-2.3.0"

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
    module.registered_create_handlers = registered_create_handlers
    yield module
    sys.modules.pop("mysqloperator.controller.innodbcluster.operator_cluster", None)


def test_failover_create_handler_is_registered_for_community_operator(
    operator_cluster_module,
):
    assert callable(operator_cluster_module.on_failover_create)


def test_pod_create_handler_retries_without_a_cap(operator_cluster_module):
    pod_create_handler = next(
        handler
        for handler in operator_cluster_module.registered_create_handlers
        if handler[0] == ("", "v1", "pods")
    )

    assert "retries" not in pod_create_handler[1]


def test_pod_create_retry_delay_uses_capped_exponential_backoff(
    operator_cluster_module,
    monkeypatch,
):
    assert hasattr(operator_cluster_module, "_pod_create_retry_delay")

    monkeypatch.setattr(
        operator_cluster_module.random,
        "uniform",
        lambda minimum, maximum: maximum,
    )

    assert operator_cluster_module._pod_create_retry_delay(0) == 33
    assert operator_cluster_module._pod_create_retry_delay(1) == 66
    assert operator_cluster_module._pod_create_retry_delay(10) == 330


def test_stage_cluster_status_patch_preserves_kopf_status(operator_cluster_module):
    patch = {"status": {"kopf": {"progress": {"handler": {}}}}}
    cluster_status = {
        "status": "PENDING",
        "onlineInstances": 0,
        "lastProbeTime": "2026-01-01T00:00:00Z",
    }

    operator_cluster_module._stage_cluster_status_patch(patch, cluster_status)

    assert patch == {
        "status": {
            "kopf": {"progress": {"handler": {}}},
            "cluster": cluster_status,
        }
    }


def test_read_replica_field_handler_routes_removed_created_and_changed_replicas(
    operator_cluster_module,
    monkeypatch,
):
    calls = []

    class ClusterMutex:
        def __init__(self, cluster):
            calls.append(("lock", cluster.name))

        def __enter__(self):
            calls.append(("enter",))
            return self

        def __exit__(self, exc_type, exc, tb):
            calls.append(("exit",))
            return False

    class ParsedSpec:
        def get_read_replica(self, name):
            return f"spec:{name}"

    class InnoDBCluster:
        namespace = "test-ns"
        name = "cluster-a"
        parsed_spec = ParsedSpec()

        def __init__(self, body):
            calls.append(("cluster", body["metadata"]["name"]))

        def get_create_time(self):
            return "2026-01-01T00:00:00Z"

        def validate_spec(self, logger):
            calls.append(("validate",))

    monkeypatch.setattr(operator_cluster_module, "ClusterMutex", ClusterMutex)
    monkeypatch.setattr(operator_cluster_module, "InnoDBCluster", InnoDBCluster)
    monkeypatch.setattr(
        operator_cluster_module.cluster_objects,
        "remove_read_replica",
        lambda cluster, rr: calls.append(("remove", rr["name"])),
        raising=False,
    )
    monkeypatch.setattr(
        operator_cluster_module,
        "do_reconcile_read_replica",
        lambda cluster, rr, logger: calls.append(("reconcile", rr)),
    )
    monkeypatch.setattr(
        operator_cluster_module,
        "do_create_read_replica",
        lambda cluster, rr, set_zero, indent, logger:
            calls.append(("create", rr, set_zero, indent)),
    )

    logger = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    old = [
        {"name": "removed", "instances": 1, "baseServerId": 500},
        {"name": "changed", "instances": 1, "baseServerId": 510},
    ]
    new = [
        {"name": "changed", "instances": 2, "baseServerId": 510},
        {"name": "created", "instances": 1, "baseServerId": 520},
    ]
    body = {"metadata": {"name": "cluster-a"}}

    operator_cluster_module.on_innodbcluster_read_replicas_changed(
        old, new, body, logger)

    assert calls == [
        ("cluster", "cluster-a"),
        ("validate",),
        ("lock", "cluster-a"),
        ("enter",),
        ("remove", "removed"),
        ("reconcile", "spec:changed"),
        ("create", "spec:created", False, ""),
        ("exit",),
    ]


class _Logger:
    def __init__(self):
        self.infos = []
        self.errors = []
        self.warnings = []

    def info(self, message):
        self.infos.append(message)

    def error(self, message):
        self.errors.append(message)

    def warning(self, message):
        self.warnings.append(message)

    def debug(self, message):
        pass


def test_remove_member_finalizer_ignores_already_deleted_pod(
    operator_cluster_module,
):
    class Pod:
        namespace = "ns"
        name = "cluster-0"

        def remove_member_finalizer(self, body):
            raise operator_cluster_module.ApiException(status=404)

    logger = _Logger()

    operator_cluster_module._remove_member_finalizer_if_present(
        Pod(),
        {"metadata": {"finalizers": []}},
        logger,
    )

    assert logger.infos == ["on_pod_delete: Pod ns/cluster-0 is already gone"]


def test_on_pod_event_reconciles_configured_pod_without_membership(
    operator_cluster_module,
    monkeypatch,
):
    class Cluster:
        name = "cluster"

        def get_create_time(self):
            return "2026-01-01T00:00:00Z"

    cluster = Cluster()
    reconciled = []
    mutex_entries = []
    restart_counter_updates = []

    class Pod:
        name = "cluster-2"
        namespace = "ns"
        instance_type = "group-member"
        phase = "Running"
        deleting = False

        def get_membership_info(self):
            return self.membership_info

        def reload(self):
            pass

        def check_containers_ready(self):
            return True

        def get_container_restarts(self, container_name):
            return 0

        def get_member_readiness_gate(self, gate):
            return True if gate == "configured" else None

        def get_cluster(self):
            return cluster

    pod = Pod()
    pod.membership_info = None

    class MySQLPod:
        @staticmethod
        def from_json(pod_body):
            return pod

    class ClusterMutex:
        def __init__(self, locked_cluster, locked_pod):
            self.locked_cluster = locked_cluster
            self.locked_pod = locked_pod

        def __enter__(self):
            mutex_entries.append((self.locked_cluster, self.locked_pod))
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class ClusterController:
        def __init__(self, controlled_cluster):
            self.controlled_cluster = controlled_cluster

        def on_pod_created(self, created_pod, logger):
            reconciled.append((self.controlled_cluster, created_pod))
            created_pod.membership_info = {"status": "ONLINE"}

        def probe_status_if_needed(self, changed_pod, logger):
            raise AssertionError("pod should be reconciled and return early")

    def fail_if_restart_path_is_used(*args, **kwargs):
        raise AssertionError("restart path should not run")

    monkeypatch.setattr(operator_cluster_module, "MySQLPod", MySQLPod)
    monkeypatch.setattr(operator_cluster_module, "ClusterMutex", ClusterMutex)
    monkeypatch.setattr(operator_cluster_module, "ClusterController", ClusterController)
    monkeypatch.setattr(
        operator_cluster_module.utils,
        "ephemeral_value_changed",
        fail_if_restart_path_is_used,
    )
    monkeypatch.setattr(
        operator_cluster_module,
        "g_ephemeral_pod_state",
        types.SimpleNamespace(
            set=lambda *args, **kwargs: restart_counter_updates.append(
                (args, kwargs)
            )
        ),
    )

    operator_cluster_module.on_pod_event({}, _Logger())

    assert mutex_entries == [(cluster, pod)]
    assert reconciled == [(cluster, pod)]
    assert restart_counter_updates == [
        ((pod, "mysql-restarts", 0), {"context": "on_pod_event"})
    ]


def test_on_pod_event_ignores_uncreated_cluster_pod_without_membership(
    operator_cluster_module,
    monkeypatch,
):
    class Cluster:
        name = "cluster"

        def get_create_time(self):
            return None

    cluster = Cluster()
    calls = []

    class Pod:
        name = "cluster-0"
        namespace = "ns"
        instance_type = "group-member"
        phase = "Running"
        deleting = False

        def get_membership_info(self):
            return None

        def check_containers_ready(self):
            return True

        def get_container_restarts(self, container_name):
            return 0

        def get_member_readiness_gate(self, gate):
            return True if gate == "configured" else None

        def get_cluster(self):
            return cluster

    pod = Pod()

    class MySQLPod:
        @staticmethod
        def from_json(pod_body):
            return pod

    class ClusterController:
        def __init__(self, controlled_cluster):
            calls.append(("controller", controlled_cluster))

        def on_pod_created(self, created_pod, logger):
            calls.append(("created", created_pod))

    monkeypatch.setattr(operator_cluster_module, "MySQLPod", MySQLPod)
    monkeypatch.setattr(operator_cluster_module, "ClusterController", ClusterController)
    monkeypatch.setattr(
        operator_cluster_module.utils,
        "ephemeral_value_changed",
        lambda *args, **kwargs: calls.append(("restart-path",)),
    )
    monkeypatch.setattr(
        operator_cluster_module,
        "g_ephemeral_pod_state",
        types.SimpleNamespace(
            set=lambda *args, **kwargs: calls.append(("restart-counter",))
        ),
    )

    logger = _Logger()

    operator_cluster_module.on_pod_event({}, logger)

    assert calls == []
    assert logger.infos == ["ignored pod event"]


def test_group_member_reconcile_without_membership_retries(
    operator_cluster_module,
    monkeypatch,
):
    class TemporaryError(Exception):
        def __init__(self, message, delay=None):
            super().__init__(message)
            self.delay = delay

    class Pod:
        name = "cluster-2"
        namespace = "ns"
        instance_type = "group-member"

        def __init__(self):
            self.reloads = 0

        def get_membership_info(self):
            return None

        def reload(self):
            self.reloads += 1

    pod = Pod()

    monkeypatch.setattr(operator_cluster_module.kopf, "TemporaryError", TemporaryError)

    with pytest.raises(TemporaryError) as exc_info:
        operator_cluster_module._ensure_group_member_reconciled(pod)

    assert "no membership info after reconciliation" in str(exc_info.value)
    assert exc_info.value.delay == 10
    assert pod.reloads == 1


def test_on_pod_delete_removes_finalizer_for_deleting_non_running_pod(
    operator_cluster_module,
    monkeypatch,
):
    body = {"metadata": {"finalizers": ["mysql.oracle.com/membership"]}}
    removed = []
    cluster_finalizers_removed = []

    class Cluster:
        namespace = "ns"
        name = "cluster"
        deleting = True

        def remove_cluster_finalizer(self):
            cluster_finalizers_removed.append(self.name)

    class Pod:
        namespace = "ns"
        name = "cluster-0"
        deleting = True
        phase = "Pending"
        index = 0

        def get_cluster(self):
            return Cluster()

        def remove_member_finalizer(self, pod_body):
            removed.append(pod_body)
            pod_body["metadata"]["finalizers"].remove("mysql.oracle.com/membership")

    pod = Pod()

    class MySQLPod:
        @staticmethod
        def from_json(pod_body):
            return pod

    def fail_if_used(*args, **kwargs):
        raise AssertionError("cluster probe should not run")

    monkeypatch.setattr(operator_cluster_module, "MySQLPod", MySQLPod)
    monkeypatch.setattr(operator_cluster_module, "ClusterController", fail_if_used)
    monkeypatch.setattr(operator_cluster_module, "ClusterMutex", fail_if_used)
    monkeypatch.setattr(
        operator_cluster_module.cluster_objects,
        "on_last_cluster_pod_removed",
        lambda cluster, logger: cluster.remove_cluster_finalizer(),
        raising=False,
    )

    operator_cluster_module.on_pod_delete(body, _Logger())

    assert removed == [body]
    assert cluster_finalizers_removed == ["cluster"]
    assert body["metadata"]["finalizers"] == []


def test_on_router_pod_delete_retries_when_cluster_mutex_is_busy(
    operator_cluster_module,
    monkeypatch,
):
    class TemporaryError(Exception):
        pass

    class Cluster:
        name = "cluster"

    body = {
        "metadata": {
            "name": "cluster-router-abc",
            "labels": {"mysql.oracle.com/cluster": "cluster"},
        }
    }
    mutex_entries = []
    controller_calls = []
    sleeps = []

    class ClusterMutex:
        def __init__(self, cluster, context="n/a"):
            self.cluster = cluster
            self.context = context

        def __enter__(self):
            mutex_entries.append((self.cluster.name, self.context))
            if len(mutex_entries) == 1:
                raise TemporaryError("cluster busy")
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class ClusterController:
        def __init__(self, cluster):
            self.cluster = cluster

        def on_router_pod_delete(self, router_name, logger):
            controller_calls.append((self.cluster.name, router_name))

    monkeypatch.setattr(operator_cluster_module.kopf, "TemporaryError", TemporaryError)
    monkeypatch.setattr(operator_cluster_module, "ClusterMutex", ClusterMutex)
    monkeypatch.setattr(operator_cluster_module, "ClusterController", ClusterController)
    monkeypatch.setattr(operator_cluster_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        operator_cluster_module.cluster_api.InnoDBCluster,
        "read",
        staticmethod(lambda namespace, name: Cluster()),
        raising=False,
    )

    operator_cluster_module.on_router_pod_delete(body, _Logger(), namespace="ns")

    assert mutex_entries == [
        ("cluster", "on_router_pod_delete"),
        ("cluster", "on_router_pod_delete"),
    ]
    assert controller_calls == [("cluster", ["cluster-router-abc"])]
    assert sleeps == [operator_cluster_module._ROUTER_METADATA_REMOVAL_RETRY_DELAY_SECONDS]


def test_on_innodbcluster_delete_retries_router_metadata_removal(
    operator_cluster_module,
    monkeypatch,
):
    class TemporaryError(Exception):
        pass

    class Cluster:
        namespace = "ns"
        name = "cluster"

        def get_routers(self):
            return ["cluster-router-abc"]

        def get_stateful_set(self):
            return None

    cluster = Cluster()
    mutex_entries = []
    controller_calls = []
    sleeps = []
    router_scale_updates = []

    class ClusterMutex:
        def __init__(self, cluster, context="n/a"):
            self.cluster = cluster
            self.context = context

        def __enter__(self):
            mutex_entries.append((self.cluster.name, self.context))
            if len(mutex_entries) == 1:
                raise TemporaryError("cluster busy")
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class ClusterController:
        def __init__(self, cluster):
            self.cluster = cluster

        def on_router_pod_delete(self, router_name, logger):
            controller_calls.append((self.cluster.name, router_name))

    monkeypatch.setattr(operator_cluster_module.kopf, "TemporaryError", TemporaryError)
    monkeypatch.setattr(operator_cluster_module, "InnoDBCluster", lambda body: cluster)
    monkeypatch.setattr(operator_cluster_module, "ClusterMutex", ClusterMutex)
    monkeypatch.setattr(operator_cluster_module, "ClusterController", ClusterController)
    monkeypatch.setattr(operator_cluster_module.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        operator_cluster_module.g_group_monitor,
        "remove_cluster",
        lambda cluster: None,
    )
    monkeypatch.setattr(
        operator_cluster_module.router_objects,
        "update_size",
        lambda cluster, size, patch, logger: router_scale_updates.append(
            (cluster.name, size, patch)
        ),
        raising=False,
    )

    operator_cluster_module.on_innodbcluster_delete(
        "cluster", "ns", {"metadata": {"name": "cluster"}}, _Logger()
    )

    assert mutex_entries == [
        ("cluster", "on_innodbcluster_delete"),
        ("cluster", "on_innodbcluster_delete"),
    ]
    assert controller_calls == [("cluster", ["cluster-router-abc"])]
    assert sleeps == [operator_cluster_module._ROUTER_METADATA_REMOVAL_RETRY_DELAY_SECONDS]
    assert router_scale_updates == [("cluster", 0, False)]


def test_on_router_pod_delete_ignores_nonretryable_metadata_failure(
    operator_cluster_module,
    monkeypatch,
):
    class TemporaryError(Exception):
        pass

    class Cluster:
        name = "cluster"

    body = {
        "metadata": {
            "name": "cluster-router-abc",
            "labels": {"mysql.oracle.com/cluster": "cluster"},
        }
    }
    controller_calls = []
    logger = _Logger()

    class ClusterMutex:
        def __init__(self, cluster, context="n/a"):
            self.cluster = cluster

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class ClusterController:
        def __init__(self, cluster):
            self.cluster = cluster

        def on_router_pod_delete(self, router_name, logger):
            controller_calls.append((self.cluster.name, router_name))
            raise RuntimeError("router metadata already absent")

    def fail_sleep(seconds):
        raise AssertionError("non-retryable failures should not sleep")

    monkeypatch.setattr(operator_cluster_module.kopf, "TemporaryError", TemporaryError)
    monkeypatch.setattr(operator_cluster_module, "ClusterMutex", ClusterMutex)
    monkeypatch.setattr(operator_cluster_module, "ClusterController", ClusterController)
    monkeypatch.setattr(operator_cluster_module.time, "sleep", fail_sleep)
    monkeypatch.setattr(
        operator_cluster_module.cluster_api.InnoDBCluster,
        "read",
        staticmethod(lambda namespace, name: Cluster()),
        raising=False,
    )

    operator_cluster_module.on_router_pod_delete(body, logger, namespace="ns")

    assert controller_calls == [("cluster", ["cluster-router-abc"])]
    assert logger.warnings == [
        "on_router_pod_delete: Failed to remove metadata for "
        "cluster-router-abc: router metadata already absent",
        "on_router_pod_delete: Exception ignored, there might be stale metadata left",
    ]


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
