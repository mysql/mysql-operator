# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import pathlib
import sys
import types


TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _drop_modules(*module_names):
    for module_name in module_names:
        sys.modules.pop(module_name, None)


def _stub_tutil_imports(monkeypatch):
    mysql_module = types.ModuleType("mysql")
    mysql_connector_module = types.ModuleType("mysql.connector")
    mysql_module.connector = mysql_connector_module
    monkeypatch.setitem(sys.modules, "mysql", mysql_module)
    monkeypatch.setitem(sys.modules, "mysql.connector", mysql_connector_module)

    kubernetes_module = types.ModuleType("kubernetes")
    kubernetes_client_module = types.ModuleType("kubernetes.client")
    kubernetes_client_module.__path__ = []
    kubernetes_client_rest_module = types.ModuleType("kubernetes.client.rest")
    kubernetes_stream_module = types.ModuleType("kubernetes.stream")
    kubernetes_stream_module.__path__ = []
    kubernetes_stream_module.stream = lambda *args, **kwargs: None
    kubernetes_ws_client_module = types.ModuleType("kubernetes.stream.ws_client")
    kubernetes_ws_client_module.ERROR_CHANNEL = 3
    kubernetes_watch_module = types.ModuleType("kubernetes.watch")

    class ApiException(Exception):
        pass

    kubernetes_client_rest_module.ApiException = ApiException

    kubernetes_module.client = kubernetes_client_module
    kubernetes_module.stream = kubernetes_stream_module
    kubernetes_module.watch = kubernetes_watch_module
    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_module)
    monkeypatch.setitem(sys.modules, "kubernetes.client", kubernetes_client_module)
    monkeypatch.setitem(
        sys.modules,
        "kubernetes.client.rest",
        kubernetes_client_rest_module,
    )
    monkeypatch.setitem(sys.modules, "kubernetes.stream", kubernetes_stream_module)
    monkeypatch.setitem(
        sys.modules,
        "kubernetes.stream.ws_client",
        kubernetes_ws_client_module,
    )
    monkeypatch.setitem(sys.modules, "kubernetes.watch", kubernetes_watch_module)


def _import_tutil(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules(
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.stream",
        "kubernetes.stream.ws_client",
        "kubernetes.watch",
        "mysql",
        "mysql.connector",
        "utils.tutil",
    )
    _stub_tutil_imports(monkeypatch)
    return importlib.import_module("utils.tutil")


def _drop_tutil_imports():
    _drop_modules(
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.stream",
        "kubernetes.stream.ws_client",
        "kubernetes.watch",
        "mysql",
        "mysql.connector",
        "utils.tutil",
    )


def test_deploy_rollover_waiter_ignores_inactive_failed_pods(monkeypatch):
    tutil = _import_tutil(monkeypatch)
    state = {"phase": "old"}
    rows = {
        "old": [{"NAME": "cluster-router-old", "STATUS": "Running"}],
        "new": [
            {"NAME": "cluster-router-new", "STATUS": "Running"},
            {"NAME": "cluster-router-failed", "STATUS": "Error"},
        ],
    }
    pod_specs = {
        "cluster-router-old": {
            "metadata": {"name": "cluster-router-old", "uid": "old-uid"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"image": "router:old", "state": {"running": {}}},
                ],
            },
        },
        "cluster-router-new": {
            "metadata": {"name": "cluster-router-new", "uid": "new-uid"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"image": "router:new", "state": {"running": {}}},
                ],
            },
        },
        "cluster-router-failed": {
            "metadata": {"name": "cluster-router-failed", "uid": "failed-uid"},
            "status": {
                "phase": "Failed",
                "containerStatuses": [
                    {"image": "router:new", "state": {"terminated": {}}},
                ],
            },
        },
    }

    monkeypatch.setattr(
        tutil.kutil,
        "get_deploy",
        lambda ns, name: {"spec": {"replicas": 1}},
    )
    monkeypatch.setattr(
        tutil.kutil,
        "ls_po",
        lambda ns, pattern=".*": rows[state["phase"]],
    )
    monkeypatch.setattr(
        tutil.kutil,
        "get_po",
        lambda ns, name: pod_specs[name],
    )

    class DummyTest:
        ns = "test-ns"

        def __init__(self):
            self.wait_calls = 0

        def wait(self, fn, timeout=60, delay=2, **kwargs):
            self.wait_calls += 1
            assert fn() is True

    test_obj = DummyTest()
    waiter = tutil.get_deploy_rollover_update_waiter(
        test_obj,
        "test-ns",
        "cluster-router",
        timeout=30,
        delay=1,
    )

    state["phase"] = "new"
    waiter()

    assert test_obj.wait_calls == 2

    _drop_tutil_imports()


def test_sts_rollover_waiter_checks_gates_for_active_pods(monkeypatch):
    tutil = _import_tutil(monkeypatch)

    state = {"phase": "old"}
    rows = {
        "old": [{"NAME": "cluster-0", "STATUS": "Running"}],
        "new": [
            {"NAME": "cluster-0", "STATUS": "Running"},
            {"NAME": "cluster-failed", "STATUS": "Failed"},
        ],
    }
    pod_specs = {
        "old": {
            "metadata": {"name": "cluster-0", "uid": "old-uid"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"image": "server:old", "state": {"running": {}}},
                ],
                "conditions": [
                    {"type": "mysql.oracle.com/configured"},
                    {"type": "mysql.oracle.com/ready"},
                ],
            },
        },
        "new": {
            "metadata": {"name": "cluster-0", "uid": "new-uid"},
            "status": {
                "phase": "Running",
                "containerStatuses": [
                    {"image": "server:new", "state": {"running": {}}},
                ],
                "conditions": [
                    {"type": "mysql.oracle.com/configured"},
                    {"type": "mysql.oracle.com/ready"},
                ],
            },
        },
        "cluster-failed": {
            "metadata": {"name": "cluster-failed", "uid": "failed-uid"},
            "status": {
                "phase": "Failed",
                "containerStatuses": [
                    {"image": "server:new", "state": {"terminated": {}}},
                ],
                "conditions": [],
            },
        },
    }

    monkeypatch.setattr(
        tutil.kutil,
        "get_sts",
        lambda ns, name: {"spec": {"replicas": 1}},
    )
    monkeypatch.setattr(
        tutil.kutil,
        "ls_po",
        lambda ns, pattern=".*": rows[state["phase"]],
    )
    monkeypatch.setattr(
        tutil.kutil,
        "get_po",
        lambda ns, name: pod_specs["new" if name == "cluster-0" else name]
        if state["phase"] == "new"
        else pod_specs["old"],
    )

    class DummyTest:
        ns = "test-ns"

        def __init__(self):
            self.wait_calls = 0

        def wait(self, fn, timeout=60, delay=2, **kwargs):
            self.wait_calls += 1
            assert fn() is True

    test_obj = DummyTest()
    waiter = tutil.get_sts_rollover_update_waiter(
        test_obj,
        "cluster",
        timeout=30,
        delay=1,
    )

    state["phase"] = "new"
    waiter()

    assert test_obj.wait_calls == 3

    _drop_tutil_imports()


def test_sts_rollover_waiter_waits_for_container_statuses(monkeypatch):
    tutil = _import_tutil(monkeypatch)

    state = {"phase": "old", "status_ready": False}
    rows = {
        "old": [{"NAME": "cluster-0", "STATUS": "Running"}],
        "new": [{"NAME": "cluster-0", "STATUS": "Running"}],
    }
    old_pod = {
        "metadata": {"name": "cluster-0", "uid": "old-uid"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"image": "server:old", "state": {"running": {}}},
            ],
            "conditions": [
                {"type": "mysql.oracle.com/configured"},
                {"type": "mysql.oracle.com/ready"},
            ],
        },
    }
    new_pod_without_statuses = {
        "metadata": {"name": "cluster-0", "uid": "new-uid"},
        "status": {"phase": "Running"},
    }
    new_pod_ready = {
        "metadata": {"name": "cluster-0", "uid": "new-uid"},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {"image": "server:new", "state": {"running": {}}},
            ],
            "conditions": [
                {"type": "mysql.oracle.com/configured"},
                {"type": "mysql.oracle.com/ready"},
            ],
        },
    }

    monkeypatch.setattr(
        tutil.kutil,
        "get_sts",
        lambda ns, name: {"spec": {"replicas": 1}},
    )
    monkeypatch.setattr(
        tutil.kutil,
        "ls_po",
        lambda ns, pattern=".*": rows[state["phase"]],
    )
    monkeypatch.setattr(
        tutil.kutil,
        "get_po",
        lambda ns, name: old_pod
        if state["phase"] == "old"
        else (new_pod_ready if state["status_ready"] else new_pod_without_statuses),
    )

    class DummyTest:
        ns = "test-ns"

        def __init__(self):
            self.wait_calls = 0

        def wait(self, fn, timeout=60, delay=2, **kwargs):
            self.wait_calls += 1
            if self.wait_calls == 2:
                assert fn() is False
                state["status_ready"] = True
            assert fn() is True

    test_obj = DummyTest()
    waiter = tutil.get_sts_rollover_update_waiter(
        test_obj,
        "cluster",
        timeout=30,
        delay=1,
    )

    state["phase"] = "new"
    waiter()

    assert test_obj.wait_calls == 3

    _drop_tutil_imports()


def test_wait_routers_ignores_inactive_rows(monkeypatch):
    tutil = _import_tutil(monkeypatch)

    monkeypatch.setattr(
        tutil.kutil,
        "ls_po",
        lambda ns, pattern=".*": [
            {"NAME": "cluster-router-new", "STATUS": "Running", "READY": "1/1"},
            {"NAME": "cluster-router-failed", "STATUS": "Error", "READY": "0/1"},
            {
                "NAME": "cluster-router-terminating",
                "STATUS": "Terminating",
                "READY": "1/1",
            },
        ],
    )

    class DummyTest:
        ns = "test-ns"

        def wait(self, fn, timeout=60, delay=2, **kwargs):
            assert fn() is True

    router_names = tutil.OperatorTest.wait_routers(
        DummyTest(),
        "cluster-router-*",
        num_online=1,
        awaited_ready="1/1",
        timeout=30,
        wait=1,
    )

    assert router_names == ["cluster-router-new"]

    _drop_tutil_imports()
