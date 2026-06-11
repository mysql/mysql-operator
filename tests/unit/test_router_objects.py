# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import logging
import pathlib
import sys
import types

import pytest


@pytest.fixture
def router_objects_module(monkeypatch):
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))

    for module_name in [
        "mysqloperator.controller.innodbcluster.router_objects",
        "mysqloperator.controller.innodbcluster.cluster_api",
        "mysqloperator.controller.kubeutils",
        "mysqloperator.controller.config",
        "mysqloperator.controller.fqdn",
        "mysqloperator.controller.utils",
        "mysqloperator.controller.shellutils",
        "mysqlsh",
        "kopf",
    ]:
        sys.modules.pop(module_name, None)

    class FakeMysqlshError(Exception):
        def __init__(self, code=0, msg=""):
            super().__init__(msg)
            self.code = code
            self.msg = msg

    mysqlsh_stub = types.ModuleType("mysqlsh")
    mysqlsh_stub.Error = FakeMysqlshError
    mysqlsh_stub.mysql = types.SimpleNamespace(
        ErrorCode=types.SimpleNamespace(ER_NONEXISTING_GRANT=1141)
    )
    monkeypatch.setitem(sys.modules, "mysqlsh", mysqlsh_stub)

    kopf_stub = types.ModuleType("kopf")
    kopf_stub.adopt = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "kopf", kopf_stub)

    cluster_api_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.cluster_api"
    )
    cluster_api_stub.InnoDBCluster = type("InnoDBCluster", (), {})
    cluster_api_stub.InnoDBClusterSpec = type("InnoDBClusterSpec", (), {})
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.innodbcluster.cluster_api",
        cluster_api_stub,
    )

    class ApiException(Exception):
        def __init__(self, status=None):
            super().__init__(f"status={status}")
            self.status = status

    kubeutils_stub = types.ModuleType("mysqloperator.controller.kubeutils")
    kubeutils_stub.client = types.SimpleNamespace(
        V1Deployment=type("V1Deployment", (), {})
    )
    kubeutils_stub.ApiException = ApiException
    kubeutils_stub.api_apps = types.SimpleNamespace()
    kubeutils_stub.api_core = types.SimpleNamespace()
    kubeutils_stub.k8s_cluster_domain = lambda logger: "cluster.local"
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.kubeutils",
        kubeutils_stub,
    )

    config_stub = types.ModuleType("mysqloperator.controller.config")
    config_stub.ROUTER_METADATA_USER_NAME = "mysqlrouter"
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.config", config_stub)

    fqdn_stub = types.ModuleType("mysqloperator.controller.fqdn")
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.fqdn", fqdn_stub)

    utils_stub = types.ModuleType("mysqloperator.controller.utils")
    utils_stub.b64encode = lambda value: value
    utils_stub.generate_password = lambda: "generated-password"
    utils_stub.generate_alphanum_string = lambda length: "generated"
    utils_stub.sha256 = lambda value: f"sha256:{value}"
    utils_stub.isotime = lambda: "2026-01-01T00:00:00Z"
    utils_stub.merge_patch_object = lambda target, patch: target.update(patch)
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.utils", utils_stub)

    shellutils_stub = types.ModuleType("mysqloperator.controller.shellutils")
    monkeypatch.setitem(
        sys.modules, "mysqloperator.controller.shellutils", shellutils_stub
    )

    module = importlib.import_module(
        "mysqloperator.controller.innodbcluster.router_objects"
    )
    yield module
    sys.modules.pop("mysqloperator.controller.innodbcluster.router_objects", None)


class _FakePod:
    def __init__(self, name="cluster-0", *, deleting=False, container_error=False):
        self.name = name
        self.deleting = deleting
        self._container_error = container_error
        self.endpoint = f"{name}:3306"
        self.endpoint_co = {"host": name, "port": 3306}

    def check_container_status_any_reason(self, container_names, reasons):
        return self._container_error

    def get_container_status_reason(self, container_name):
        return "CrashLoopBackOff"


class _FakeSession:
    def __init__(self, *, grant_exists, mysqlsh_error, missing_grant_code):
        self._grant_exists = grant_exists
        self._mysqlsh_error = mysqlsh_error
        self._missing_grant_code = missing_grant_code
        self.calls = []

    def run_sql(self, statement, params):
        self.calls.append((statement, params))
        if not self._grant_exists:
            raise self._mysqlsh_error(self._missing_grant_code, "grant missing")


class _FakeDbaCluster:
    def __init__(self):
        self.setup_calls = []

    def setup_router_account(self, user, options):
        self.setup_calls.append((user, options))


class _FakeDba:
    def __init__(self, session, dba_cluster):
        self.session = session
        self._dba_cluster = dba_cluster

    def get_cluster(self):
        return self._dba_cluster


class _FakeDbaWrap:
    def __init__(self, dba):
        self._dba = dba

    def __enter__(self):
        return self._dba

    def __exit__(self, exc_type, exc, tb):
        return False


def _make_cluster(pod):
    return types.SimpleNamespace(
        ready=True,
        namespace="test-ns",
        name="test-cluster",
        get_router_account=lambda: ("router-user", "router-password"),
        get_pods=lambda: [pod],
    )


def test_update_router_account_creates_missing_account(router_objects_module, monkeypatch):
    session = _FakeSession(
        grant_exists=False,
        mysqlsh_error=router_objects_module.mysqlsh.Error,
        missing_grant_code=router_objects_module.mysqlsh.mysql.ErrorCode.ER_NONEXISTING_GRANT,
    )
    dba_cluster = _FakeDbaCluster()
    dba = _FakeDba(session, dba_cluster)
    pod = _FakePod()
    cluster = _make_cluster(pod)
    on_nonupdated_called = False

    monkeypatch.setattr(
        router_objects_module.shellutils,
        "connect_dba",
        lambda endpoint, logger, max_tries=3: dba,
        raising=False,
    )
    monkeypatch.setattr(
        router_objects_module.shellutils,
        "DbaWrap",
        _FakeDbaWrap,
        raising=False,
    )

    def on_nonupdated():
        nonlocal on_nonupdated_called
        on_nonupdated_called = True

    router_objects_module.update_router_account(
        cluster, on_nonupdated, logging.getLogger(__name__)
    )

    assert session.calls == [("show grants for ?@'%'", ["router-user"])]
    assert dba_cluster.setup_calls == [
        ("router-user", {"password": "router-password", "update": False})
    ]
    assert on_nonupdated_called is False


def test_update_router_account_updates_existing_account(router_objects_module, monkeypatch):
    session = _FakeSession(
        grant_exists=True,
        mysqlsh_error=router_objects_module.mysqlsh.Error,
        missing_grant_code=router_objects_module.mysqlsh.mysql.ErrorCode.ER_NONEXISTING_GRANT,
    )
    dba_cluster = _FakeDbaCluster()
    dba = _FakeDba(session, dba_cluster)
    pod = _FakePod()
    cluster = _make_cluster(pod)

    monkeypatch.setattr(
        router_objects_module.shellutils,
        "connect_dba",
        lambda endpoint, logger, max_tries=3: dba,
        raising=False,
    )
    monkeypatch.setattr(
        router_objects_module.shellutils,
        "DbaWrap",
        _FakeDbaWrap,
        raising=False,
    )

    router_objects_module.update_router_account(
        cluster, None, logging.getLogger(__name__)
    )

    assert session.calls == [("show grants for ?@'%'", ["router-user"])]
    assert dba_cluster.setup_calls == [
        ("router-user", {"password": "router-password", "update": True})
    ]
