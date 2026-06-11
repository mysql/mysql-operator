# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import sys
import types

import pytest


@pytest.fixture
def utils_module(monkeypatch):
    for module_name in [
        "mysqloperator.controller.utils",
        "mysqloperator.controller.config",
        "mysqloperator.controller.kubeutils",
        "mysqloperator.controller",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
    ]:
        sys.modules.pop(module_name, None)

    kubernetes_stub = types.ModuleType("kubernetes")
    client_stub = types.ModuleType("kubernetes.client")
    rest_stub = types.ModuleType("kubernetes.client.rest")
    config_stub = types.ModuleType("kubernetes.config")

    class ApiException(Exception):
        pass

    class RESTClientObject:
        def __init__(self, configuration=None):
            self.configuration = configuration

    class _Configuration:
        def __init__(self):
            self.connection_pool_maxsize = 1
            self.verify_ssl = False
            self.ssl_ca_cert = None
            self.cert_file = None
            self.key_file = None

    class _ApiClient:
        def __init__(self, configuration=None):
            self.configuration = configuration
            self.rest_client = None

    class _ApiBase:
        def __init__(self, api_client=None):
            self.api_client = api_client

    class _VersionApi(_ApiBase):
        def get_code(self):
            return types.SimpleNamespace(major="1", minor="30")

    class ConfigException(Exception):
        pass

    def _load_config(*args, **kwargs):
        return None

    rest_stub.ApiException = ApiException
    rest_stub.RESTClientObject = RESTClientObject
    client_stub.Configuration = _Configuration
    client_stub.ApiClient = _ApiClient
    client_stub.CoreV1Api = _ApiBase
    client_stub.CustomObjectsApi = _ApiBase
    client_stub.AppsV1Api = _ApiBase
    client_stub.BatchV1Api = _ApiBase
    client_stub.PolicyV1Api = _ApiBase
    client_stub.RbacAuthorizationV1Api = _ApiBase
    client_stub.ApisApi = _ApiBase
    client_stub.VersionApi = _VersionApi
    client_stub.rest = rest_stub
    config_stub.load_kube_config = _load_config
    config_stub.load_incluster_config = _load_config
    config_stub.config_exception = types.SimpleNamespace(ConfigException=ConfigException)
    kubernetes_stub.client = client_stub
    kubernetes_stub.config = config_stub

    monkeypatch.setitem(sys.modules, "kubernetes", kubernetes_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.client", client_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.client.rest", rest_stub)
    monkeypatch.setitem(sys.modules, "kubernetes.config", config_stub)

    module = importlib.import_module("mysqloperator.controller.utils")
    yield module
    sys.modules.pop("mysqloperator.controller.utils", None)


def _fake_file(contents):
    class _File:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return contents

    return _File()


def test_get_mem_limits_in_gb_reads_cgroup_v2_first(monkeypatch, utils_module):
    calls = []

    def fake_open(path, mode="r", *args, **kwargs):
        calls.append(path)
        if path == "/sys/fs/cgroup/memory.max":
            return _fake_file(str(2 * 1024**3))
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(utils_module, "open", fake_open, raising=False)

    assert utils_module.get_mem_limits_in_gb() == "2.00"
    assert calls == ["/sys/fs/cgroup/memory.max"]


def test_get_mem_limits_in_gb_falls_back_to_cgroup_v1(monkeypatch, utils_module):
    def fake_open(path, mode="r", *args, **kwargs):
        if path == "/sys/fs/cgroup/memory.max":
            raise OSError("missing cgroup v2 file")
        if path == "/sys/fs/cgroup/memory/memory.limit_in_bytes":
            return _fake_file(str(3 * 1024**3))
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(utils_module, "open", fake_open, raising=False)

    assert utils_module.get_mem_limits_in_gb() == "3.00"


def test_get_mem_limits_in_gb_returns_unlimited_when_files_missing(monkeypatch, utils_module):
    def fake_open(path, mode="r", *args, **kwargs):
        raise OSError(f"cannot open {path}")

    monkeypatch.setattr(utils_module, "open", fake_open, raising=False)

    assert utils_module.get_mem_limits_in_gb() == "Unlimited"


def test_get_mem_limits_in_gb_returns_unlimited_on_malformed_content(monkeypatch, utils_module):
    calls = []

    def fake_open(path, mode="r", *args, **kwargs):
        calls.append(path)
        if path == "/sys/fs/cgroup/memory.max":
            return _fake_file("invalid")
        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(utils_module, "open", fake_open, raising=False)

    assert utils_module.get_mem_limits_in_gb() == "Unlimited"
    assert calls == ["/sys/fs/cgroup/memory.max"]


def test_get_cpu_limits_falls_back_to_cgroup_v1_on_value_error(monkeypatch, utils_module):
    def fake_open(path, mode="r", *args, **kwargs):
        if path == "/sys/fs/cgroup/cpu.max":
            return _fake_file("invalid")

        if path == "/sys/fs/cgroup/cpu/cpu.cfs_quota_us":
            return _fake_file("200000")

        if path == "/sys/fs/cgroup/cpu/cpu.cfs_period_us":
            return _fake_file("100000")

        raise AssertionError(f"unexpected path {path}")

    monkeypatch.setattr(utils_module, "open", fake_open, raising=False)

    assert utils_module.get_cpu_limits() == 2.0


def test_get_cpu_limits_returns_cpu_count_when_both_cgroup_reads_fail(monkeypatch, utils_module):
    def fake_open(path, mode="r", *args, **kwargs):
        raise OSError(f"cannot open {path}")

    monkeypatch.setattr(utils_module, "open", fake_open, raising=False)
    monkeypatch.setattr(utils_module.os, "cpu_count", lambda: 7)

    assert utils_module.get_cpu_limits() == 7


def test_generate_password_uses_cryptographic_choice(monkeypatch, utils_module):
    choices = []

    def fake_choice(characters):
        choices.append(characters)
        return characters[0]

    monkeypatch.setattr(utils_module.secrets, "choice", fake_choice)

    password = utils_module.generate_password()

    assert password == "aaaaa-aaaaa-aaaaa-aaaaa-aaaaa"
    assert len(choices) == 25


def test_generate_alphanum_string_uses_cryptographic_choice(monkeypatch, utils_module):
    choices = []

    def fake_choice(characters):
        choices.append(characters)
        return characters[-1]

    monkeypatch.setattr(utils_module.secrets, "choice", fake_choice)

    value = utils_module.generate_alphanum_string(10)

    assert value == "9999999999"
    assert len(choices) == 10


def test_ephemeral_value_changed_primes_first_observation(utils_module, monkeypatch):
    monkeypatch.setattr(utils_module, "g_ephemeral_pod_state", utils_module.EphemeralState())
    pod = types.SimpleNamespace(namespace="test-ns", name="test-pod")

    assert utils_module.ephemeral_value_changed(
        pod, "mysql-restarts", 0, context="on_pod_event"
    ) is False
    assert utils_module.ephemeral_value_changed(
        pod, "mysql-restarts", 0, context="on_pod_event"
    ) is False
    assert utils_module.ephemeral_value_changed(
        pod, "mysql-restarts", 1, context="on_pod_event"
    ) is True

    utils_module.g_ephemeral_pod_state.set(
        pod, "mysql-restarts", 1, context="on_pod_event"
    )

    assert utils_module.ephemeral_value_changed(
        pod, "mysql-restarts", 1, context="on_pod_event"
    ) is False
