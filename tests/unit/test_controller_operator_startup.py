import importlib
import pathlib
import sys
import types
import logging

import pytest


@pytest.fixture(autouse=True)
def cleanup_controller_operator_modules():
    yield

    for module_name in [
        "mysqloperator.controller.operator",
        "mysqloperator.controller.config",
        "mysqloperator.controller.utils",
        "mysqloperator.controller.consts",
        "mysqloperator.controller.group_monitor",
        "mysqloperator.controller.watched_namespaces",
        "mysqloperator.controller.backup",
        "mysqloperator.controller.backup.operator_backup",
        "mysqloperator.controller.innodbcluster.cluster_api",
        "mysqloperator.controller.innodbcluster.operator_cluster",
        "kopf",
    ]:
        sys.modules.pop(module_name, None)


def _load_controller_operator_module():
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    for module_name in [
        "mysqloperator.controller.operator",
        "mysqloperator.controller.config",
        "mysqloperator.controller.utils",
        "mysqloperator.controller.consts",
        "mysqloperator.controller.group_monitor",
        "mysqloperator.controller.watched_namespaces",
        "mysqloperator.controller.backup",
        "mysqloperator.controller.backup.operator_backup",
        "mysqloperator.controller.innodbcluster.cluster_api",
        "mysqloperator.controller.innodbcluster.operator_cluster",
        "kopf",
    ]:
        sys.modules.pop(module_name, None)

    def _decorator(*args, **kwargs):
        def _wrap(func):
            return func

        return _wrap

    kopf_stub = types.ModuleType("kopf")
    kopf_stub.OperatorSettings = type("OperatorSettings", (), {})
    kopf_stub.on = types.SimpleNamespace(
        startup=_decorator,
        cleanup=_decorator,
    )
    sys.modules["kopf"] = kopf_stub

    config_stub = types.ModuleType("mysqloperator.controller.config")
    config_stub.log_config_banner = lambda logger: None
    sys.modules["mysqloperator.controller.config"] = config_stub

    utils_stub = types.ModuleType("mysqloperator.controller.utils")
    utils_stub.log_banner = lambda *args, **kwargs: None
    sys.modules["mysqloperator.controller.utils"] = utils_stub

    consts_stub = types.ModuleType("mysqloperator.controller.consts")
    sys.modules["mysqloperator.controller.consts"] = consts_stub

    group_monitor_stub = types.ModuleType("mysqloperator.controller.group_monitor")
    group_monitor_stub.g_group_monitor = types.SimpleNamespace(
        start=lambda: None,
        stop=lambda: None,
        join=lambda timeout=None: None,
        is_alive=lambda: False,
        _thread_started=False,
    )
    sys.modules["mysqloperator.controller.group_monitor"] = group_monitor_stub

    watched_namespaces_stub = types.ModuleType(
        "mysqloperator.controller.watched_namespaces"
    )

    class WatchedNamespaces:
        def __init__(self, namespaces):
            self.namespaces = namespaces

        def validate(self):
            return None

        def resolve_operator_namespaces(self):
            return []

    watched_namespaces_stub.WatchedNamespaces = WatchedNamespaces
    sys.modules["mysqloperator.controller.watched_namespaces"] = (
        watched_namespaces_stub
    )

    backup_package_stub = types.ModuleType("mysqloperator.controller.backup")
    operator_backup_stub = types.ModuleType(
        "mysqloperator.controller.backup.operator_backup"
    )
    backup_package_stub.operator_backup = operator_backup_stub
    sys.modules["mysqloperator.controller.backup"] = backup_package_stub
    sys.modules["mysqloperator.controller.backup.operator_backup"] = (
        operator_backup_stub
    )

    cluster_api_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.cluster_api"
    )
    cluster_api_stub.InnoDBCluster = type("InnoDBCluster", (), {})
    cluster_api_stub.get_all_clusters = lambda namespaces: []
    sys.modules["mysqloperator.controller.innodbcluster.cluster_api"] = (
        cluster_api_stub
    )

    operator_cluster_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.operator_cluster"
    )
    operator_cluster_stub.ensure_backup_schedules_use_current_image = (
        lambda clusters, logger: None
    )
    operator_cluster_stub.ensure_switchover_rbac_uptodate = (
        lambda clusters, logger: None
    )
    operator_cluster_stub.monitor_existing_clusters = lambda clusters, logger: None
    operator_cluster_stub.refresh_existing_cluster_status = (
        lambda clusters, logger: None
    )
    operator_cluster_stub.ensure_router_accounts_are_uptodate = (
        lambda clusters, logger: None
    )
    sys.modules["mysqloperator.controller.innodbcluster.operator_cluster"] = (
        operator_cluster_stub
    )

    return importlib.import_module("mysqloperator.controller.operator")


class _FakeCluster:
    def __init__(self, namespace: str, name: str):
        self.namespace = namespace
        self.name = name
        self.info_calls = []

    def info(self, **kwargs):
        self.info_calls.append(kwargs)


class _FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


def test_on_startup_blocks_follow_up_steps_when_switchover_rbac_repair_fails(
    monkeypatch,
    tmp_path,
):
    operator_module = _load_controller_operator_module()
    clusters = [_FakeCluster("ns-a", "cluster-a")]
    call_order = []
    group_monitor_starts = []
    ready_file = tmp_path / "mysql-operator-ready"

    monkeypatch.setattr(operator_module, "READY_FILE", ready_file)
    monkeypatch.setattr(operator_module, "get_startup_clusters", lambda: clusters)
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "ensure_backup_schedules_use_current_image",
        lambda clusters, logger: call_order.append("backup"),
    )

    def fail_switchover(clusters, logger):
        call_order.append("switchover")
        raise RuntimeError("startup switchover failed")

    monkeypatch.setattr(
        operator_module.operator_cluster,
        "ensure_switchover_rbac_uptodate",
        fail_switchover,
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "monitor_existing_clusters",
        lambda clusters, logger: call_order.append("monitor"),
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "refresh_existing_cluster_status",
        lambda clusters, logger: call_order.append("refresh"),
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "ensure_router_accounts_are_uptodate",
        lambda clusters, logger: call_order.append("router"),
    )
    monkeypatch.setattr(
        operator_module.g_group_monitor,
        "start",
        lambda: group_monitor_starts.append("started"),
    )

    settings = types.SimpleNamespace(
        posting=types.SimpleNamespace(level=None, enabled=None)
    )
    logger = _FakeLogger()

    with pytest.raises(RuntimeError, match="startup switchover failed"):
        operator_module.on_startup(settings, logger)

    assert call_order == ["backup", "switchover"]
    assert group_monitor_starts == []
    assert ready_file.exists() is False
    assert clusters[0].info_calls == []
    assert settings.posting.level == logging.INFO
    assert settings.posting.enabled is False


def test_on_startup_emits_operator_restarted_only_after_success(
    monkeypatch,
    tmp_path,
):
    operator_module = _load_controller_operator_module()
    clusters = [
        _FakeCluster("ns-a", "cluster-a"),
        _FakeCluster("ns-b", "cluster-b"),
    ]
    call_order = []
    group_monitor_starts = []
    ready_file = tmp_path / "mysql-operator-ready"

    monkeypatch.setattr(operator_module, "READY_FILE", ready_file)
    monkeypatch.setattr(operator_module, "get_startup_clusters", lambda: clusters)
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "ensure_backup_schedules_use_current_image",
        lambda clusters, logger: call_order.append("backup"),
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "ensure_switchover_rbac_uptodate",
        lambda clusters, logger: call_order.append("switchover"),
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "monitor_existing_clusters",
        lambda clusters, logger: call_order.append("monitor"),
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "refresh_existing_cluster_status",
        lambda clusters, logger: call_order.append("refresh"),
    )
    monkeypatch.setattr(
        operator_module.operator_cluster,
        "ensure_router_accounts_are_uptodate",
        lambda clusters, logger: call_order.append("router"),
    )
    monkeypatch.setattr(
        operator_module.g_group_monitor,
        "start",
        lambda: group_monitor_starts.append("started"),
    )

    settings = types.SimpleNamespace(
        posting=types.SimpleNamespace(level=None, enabled=None)
    )
    logger = _FakeLogger()

    operator_module.on_startup(settings, logger)

    assert call_order == [
        "backup",
        "switchover",
        "monitor",
        "refresh",
        "router",
    ]
    assert group_monitor_starts == ["started"]
    assert ready_file.exists() is True
    assert settings.posting.level == logging.INFO
    assert settings.posting.enabled is False

    for cluster in clusters:
        assert cluster.info_calls == [
            {
                "action": "HandlingOperatorRestart",
                "reason": "OperatorRestarted",
                "message": (
                    f"Handling operator restarted for cluster "
                    f"{cluster.namespace}/{cluster.name}"
                ),
            }
        ]
