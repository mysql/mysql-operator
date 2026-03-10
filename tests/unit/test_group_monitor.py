import importlib
import logging
import os
import socket
import sys
import threading
import time
import types

import pytest


def _load_group_monitor_module(monkeypatch):
    import mysqloperator  # ensure package is loaded before stubbing submodules

    for module_name in [
        "mysqloperator.controller.group_monitor",
        "mysqloperator.controller.shellutils",
        "mysqloperator.controller.innodbcluster.cluster_api",
        "mysqloperator.controller.innodbcluster",
        "mysqloperator.controller",
        "mysqlsh",
    ]:
        sys.modules.pop(module_name, None)

    mysqlsh_stub = types.ModuleType("mysqlsh")

    class FakeMysqlshError(Exception):
        def __init__(self, code=0, msg=""):
            super().__init__(msg)
            self.code = code
            self.msg = msg

    mysqlsh_stub.Error = FakeMysqlshError
    mysqlsh_stub.mysql = types.SimpleNamespace(
        ErrorCode=types.SimpleNamespace(CR_MAX_ERROR=2999, CR_MIN_ERROR=2000)
    )
    mysqlsh_stub.mysqlx = types.SimpleNamespace()
    monkeypatch.setitem(sys.modules, "mysqlsh", mysqlsh_stub)

    shellutils_stub = types.ModuleType("mysqloperator.controller.shellutils")

    class RetryLoop:
        def __init__(self, logger, timeout=60, max_tries=None, is_retriable=None, backoff=None):
            self.logger = logger

        def call(self, f, *args, **kwargs):
            return f(*args, **kwargs)

    shellutils_stub.RetryLoop = RetryLoop
    shellutils_stub.parse_uri = lambda uri: {"host": "127.0.0.1", "port": 33060}
    shellutils_stub.query_members = lambda session: []
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.shellutils", shellutils_stub)

    innodbcluster_stub = types.ModuleType("mysqloperator.controller.innodbcluster")
    innodbcluster_stub.__path__ = []
    monkeypatch.setitem(sys.modules, "mysqloperator.controller.innodbcluster", innodbcluster_stub)

    cluster_api_stub = types.ModuleType("mysqloperator.controller.innodbcluster.cluster_api")

    class InnoDBCluster:
        pass

    cluster_api_stub.InnoDBCluster = InnoDBCluster
    monkeypatch.setitem(
        sys.modules,
        "mysqloperator.controller.innodbcluster.cluster_api",
        cluster_api_stub,
    )

    return importlib.import_module("mysqloperator.controller.group_monitor")


class _PipeSocket:
    def __init__(self, fd: int):
        self._fd = fd
        self.closed = False

    def fileno(self):
        return self._fd

    def setblocking(self, flag):
        os.set_blocking(self._fd, flag)

    def recv(self, bufsize):
        return os.read(self._fd, bufsize)

    def send(self, data):
        return os.write(self._fd, data)

    def close(self):
        if self.closed:
            return
        self.closed = True
        os.close(self._fd)


def _pipe_socketpair():
    reader_fd, writer_fd = os.pipe()
    return _PipeSocket(reader_fd), _PipeSocket(writer_fd)


def _wait_until(predicate, timeout=1.0, interval=0.01):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return predicate()


class _FakeCluster:
    def __init__(self, name, namespace="ns"):
        self.name = name
        self.namespace = namespace

    def get_admin_account(self):
        return ("admin", "password")


class _FakeSession:
    def __init__(self):
        self._reader, self._writer = socket.socketpair()
        self.closed = False
        self.close_count = 0

    def _get_socket_fd(self):
        return self._reader.fileno()

    def make_readable(self):
        if self.closed:
            return
        self._writer.send(b"\0")

    def close(self):
        self.close_count += 1
        if self.closed:
            return

        self.closed = True
        self._reader.close()
        self._writer.close()


class _FakeMonitoredCluster:
    instances = {}

    def __init__(self, cluster, account, handler):
        self.cluster = cluster
        self.account = account
        self.handler = handler
        self.session = _FakeSession()
        self._active = True
        self.ensure_connected_count = 0
        self.handle_notice_count = 0
        self.logger = logging.getLogger(f"fake-{cluster.namespace}-{cluster.name}")
        self.instances[(cluster.namespace, cluster.name)] = self

    @property
    def name(self):
        return self.cluster.name

    @property
    def namespace(self):
        return self.cluster.namespace

    @property
    def active(self):
        return self._active

    def deactivate(self):
        self._active = False

    def ensure_connected(self):
        if not self.active:
            return None
        self.ensure_connected_count += 1
        return self.session

    def handle_notice(self):
        if not self.active:
            return
        self.handle_notice_count += 1

    @classmethod
    def reset(cls):
        cls.instances = {}

    @classmethod
    def close_all(cls):
        for instance in list(cls.instances.values()):
            if instance.session:
                instance.session.close()
        cls.instances = {}


class _FakeSessionWithCloseError(_FakeSession):
    def close(self):
        self.close_count += 1
        if self.closed:
            return
        self.closed = True
        self._reader.close()
        self._writer.close()
        raise RuntimeError("close failed")


class _FakeMonitoredClusterWithCloseError(_FakeMonitoredCluster):
    def __init__(self, cluster, account, handler):
        self.cluster = cluster
        self.account = account
        self.handler = handler
        self.session = _FakeSessionWithCloseError()
        self.ensure_connected_count = 0
        self.handle_notice_count = 0
        self.logger = logging.getLogger(f"fake-{cluster.namespace}-{cluster.name}")
        self.instances[(cluster.namespace, cluster.name)] = self



@pytest.fixture
def group_monitor_module(monkeypatch):
    module = _load_group_monitor_module(monkeypatch)
    monkeypatch.setattr(module.socket, "socketpair", _pipe_socketpair)
    yield module
    sys.modules.pop("mysqloperator.controller.group_monitor", None)


@pytest.fixture(autouse=True)
def reset_fake_monitored_clusters():
    _FakeMonitoredCluster.reset()
    yield
    _FakeMonitoredCluster.close_all()


def test_get_group_monitor_poll_timeout_seconds(monkeypatch, group_monitor_module):
    logger = logging.getLogger("test")

    monkeypatch.delenv("OPERATOR_GROUP_MONITOR_POLL_TIMEOUT", raising=False)
    assert group_monitor_module._get_group_monitor_poll_timeout_seconds(logger) == (
        group_monitor_module.consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS
    )

    monkeypatch.setenv("OPERATOR_GROUP_MONITOR_POLL_TIMEOUT", "7")
    assert group_monitor_module._get_group_monitor_poll_timeout_seconds(logger) == 7

    monkeypatch.setenv("OPERATOR_GROUP_MONITOR_POLL_TIMEOUT", "0")
    assert group_monitor_module._get_group_monitor_poll_timeout_seconds(logger) == (
        group_monitor_module.consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS
    )

    monkeypatch.setenv("OPERATOR_GROUP_MONITOR_POLL_TIMEOUT", "abc")
    assert group_monitor_module._get_group_monitor_poll_timeout_seconds(logger) == (
        group_monitor_module.consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS
    )


def test_monitor_cluster_wakes_select_for_new_cluster(monkeypatch, group_monitor_module):
    select_entered = threading.Event()
    original_select = group_monitor_module.select.select

    def instrumented_select(*args, **kwargs):
        select_entered.set()
        return original_select(*args, **kwargs)

    monkeypatch.setattr(group_monitor_module.select, "select", instrumented_select)
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster1 = _FakeCluster("cluster-1")
    cluster2 = _FakeCluster("cluster-2")

    try:
        monitor.monitor_cluster(cluster1, lambda *args: None, logger)
        monitor._drain_wake_socket()
        monitor.start()

        assert _wait_until(select_entered.is_set), "GroupMonitor never entered select()"

        monitor.monitor_cluster(cluster2, lambda *args: None, logger)

        def cluster2_connected():
            instance = _FakeMonitoredCluster.instances.get((cluster2.namespace, cluster2.name))
            return bool(instance and instance.ensure_connected_count > 0)

        assert _wait_until(cluster2_connected), (
            "Adding a cluster did not wake the select() loop promptly"
        )
    finally:
        monitor.stop()
        monitor.join(timeout=1)


def test_remove_cluster_wakes_select_for_rebuild(monkeypatch, group_monitor_module):
    select_entered = threading.Event()
    original_select = group_monitor_module.select.select

    def instrumented_select(*args, **kwargs):
        select_entered.set()
        return original_select(*args, **kwargs)

    monkeypatch.setattr(group_monitor_module.select, "select", instrumented_select)
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster1 = _FakeCluster("cluster-1")
    cluster2 = _FakeCluster("cluster-2")

    try:
        monitor.monitor_cluster(cluster1, lambda *args: None, logger)
        monitor.monitor_cluster(cluster2, lambda *args: None, logger)
        monitor._drain_wake_socket()
        monitor.start()

        assert _wait_until(select_entered.is_set), "GroupMonitor never entered select()"

        cluster2_instance = _FakeMonitoredCluster.instances[(cluster2.namespace, cluster2.name)]
        ensure_count_before_remove = cluster2_instance.ensure_connected_count

        monitor.remove_cluster(cluster1)

        assert _wait_until(
            lambda: cluster2_instance.ensure_connected_count > ensure_count_before_remove
        ), "Removing a cluster did not wake the select() loop promptly"
    finally:
        monitor.stop()
        monitor.join(timeout=1)


def test_stop_wakes_select_with_monitored_fd(monkeypatch, group_monitor_module):
    select_entered = threading.Event()
    original_select = group_monitor_module.select.select

    def instrumented_select(*args, **kwargs):
        select_entered.set()
        return original_select(*args, **kwargs)

    monkeypatch.setattr(group_monitor_module.select, "select", instrumented_select)
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster = _FakeCluster("cluster-1")

    try:
        monitor.monitor_cluster(cluster, lambda *args: None, logger)
        monitor._drain_wake_socket()
        monitor.start()

        assert _wait_until(select_entered.is_set), "GroupMonitor never entered select()"

        monitor.stop()
        monitor.join(timeout=1)

        assert not monitor.is_alive(), "stop() did not wake the blocked select() call"
    finally:
        if monitor.is_alive():
            monitor.stop()
            monitor.join(timeout=1)


def test_stop_discards_stale_ready_cluster_fds(monkeypatch, group_monitor_module):
    select_entered = threading.Event()
    allow_select_return = threading.Event()
    original_select = group_monitor_module.select.select

    def instrumented_select(*args, **kwargs):
        select_entered.set()
        assert allow_select_return.wait(timeout=1), "Timed out waiting to release select()"
        return original_select(*args, **kwargs)

    monkeypatch.setattr(group_monitor_module.select, "select", instrumented_select)
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster = _FakeCluster("cluster-1")

    try:
        monitor.monitor_cluster(cluster, lambda *args: None, logger)
        monitor._drain_wake_socket()
        monitor.start()

        assert _wait_until(select_entered.is_set), "GroupMonitor never entered select()"

        cluster_instance = _FakeMonitoredCluster.instances[(cluster.namespace, cluster.name)]
        cluster_instance.session.make_readable()
        monitor.stop()
        allow_select_return.set()
        monitor.join(timeout=1)

        assert not monitor.is_alive(), "stop() did not wake the blocked select() call"
        assert cluster_instance.handle_notice_count == 0, (
            "GroupMonitor handled a stale session fd after wakeup"
        )
    finally:
        allow_select_return.set()
        if monitor.is_alive():
            monitor.stop()
            monitor.join(timeout=1)


def test_monitor_cluster_does_not_add_after_stop(monkeypatch, group_monitor_module):
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)

    account_lookup_started = threading.Event()
    allow_account_lookup = threading.Event()

    class _BlockingCluster(_FakeCluster):
        def get_admin_account(self):
            account_lookup_started.set()
            assert allow_account_lookup.wait(timeout=1), (
                "Timed out waiting to release get_admin_account()"
            )
            return super().get_admin_account()

    cluster = _BlockingCluster("cluster-1")
    add_done = threading.Event()

    def add_cluster():
        try:
            monitor.monitor_cluster(cluster, lambda *args: None, logger)
        finally:
            add_done.set()

    monitor.start()
    add_thread = threading.Thread(target=add_cluster, daemon=True)

    try:
        add_thread.start()
        assert _wait_until(account_lookup_started.is_set), "monitor_cluster() never started account lookup"

        monitor.stop()
        monitor.join(timeout=1)
        allow_account_lookup.set()

        assert _wait_until(add_done.is_set), "monitor_cluster() did not return after stop()"
        assert not monitor.clusters, "monitor_cluster() appended a cluster after stop()"
    finally:
        allow_account_lookup.set()
        add_thread.join(timeout=1)
        if monitor.is_alive():
            monitor.stop()
            monitor.join(timeout=1)


def test_start_rejects_second_start(group_monitor_module):
    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)

    try:
        monitor.start()
        with pytest.raises(RuntimeError, match="threads can only be started once"):
            monitor.start()
    finally:
        monitor.stop()
        monitor.join(timeout=1)


def test_start_rejects_restart_after_stop(group_monitor_module):
    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)

    monitor.stop()

    with pytest.raises(RuntimeError, match="cannot start a stopped GroupMonitor"):
        monitor.start()


def test_close_cluster_session_clears_session_when_close_raises(monkeypatch, caplog, group_monitor_module):
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredClusterWithCloseError)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster = _FakeCluster("cluster-1")

    monitor.monitor_cluster(cluster, lambda *args: None, logger)
    cluster_instance = _FakeMonitoredClusterWithCloseError.instances[(cluster.namespace, cluster.name)]

    with caplog.at_level(logging.WARNING):
        monitor.remove_cluster(cluster)

    assert cluster_instance.session is None
    assert "Failed to close monitor session for ns/cluster-1: close failed" in caplog.text


def test_stop_and_remove_overlap_single_claims_cleanup(monkeypatch, group_monitor_module):
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")

    for iteration in range(25):
        monitor = group_monitor_module.GroupMonitor(60, logger)
        cluster = _FakeCluster(f"cluster-{iteration}")

        try:
            monitor.monitor_cluster(cluster, lambda *args: None, logger)
            monitor._drain_wake_socket()
            monitor.start()

            cluster_key = (cluster.namespace, cluster.name)
            assert _wait_until(lambda: cluster_key in _FakeMonitoredCluster.instances), (
                "GroupMonitor did not create the monitored cluster"
            )

            cluster_instance = _FakeMonitoredCluster.instances[cluster_key]
            session = cluster_instance.session

            stop_thread = threading.Thread(target=monitor.stop)
            remove_thread = threading.Thread(target=monitor.remove_cluster, args=(cluster,))

            stop_thread.start()
            remove_thread.start()

            stop_thread.join(timeout=1)
            remove_thread.join(timeout=1)
            monitor.join(timeout=1)

            assert not stop_thread.is_alive()
            assert not remove_thread.is_alive()
            assert not monitor.is_alive()
            assert session.close_count == 1
            assert cluster_instance.session is None
        finally:
            if monitor.is_alive():
                monitor.stop()
                monitor.join(timeout=1)


def test_remove_cluster_closes_removed_cluster_session(monkeypatch, group_monitor_module):
    select_entered = threading.Event()
    original_select = group_monitor_module.select.select

    def instrumented_select(*args, **kwargs):
        select_entered.set()
        return original_select(*args, **kwargs)

    monkeypatch.setattr(group_monitor_module.select, "select", instrumented_select)
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster = _FakeCluster("cluster-1")

    try:
        monitor.monitor_cluster(cluster, lambda *args: None, logger)
        monitor._drain_wake_socket()
        monitor.start()

        assert _wait_until(select_entered.is_set), "GroupMonitor never entered select()"

        cluster_instance = _FakeMonitoredCluster.instances[(cluster.namespace, cluster.name)]
        session = cluster_instance.session

        monitor.remove_cluster(cluster)

        assert _wait_until(lambda: session.closed), (
            "remove_cluster() did not close the removed cluster session"
        )
        assert not cluster_instance.active
        assert cluster_instance.session is None
    finally:
        monitor.stop()
        monitor.join(timeout=1)


def test_stop_closes_active_cluster_sessions(monkeypatch, group_monitor_module):
    select_entered = threading.Event()
    original_select = group_monitor_module.select.select

    def instrumented_select(*args, **kwargs):
        select_entered.set()
        return original_select(*args, **kwargs)

    monkeypatch.setattr(group_monitor_module.select, "select", instrumented_select)
    monkeypatch.setattr(group_monitor_module, "MonitoredCluster", _FakeMonitoredCluster)

    logger = logging.getLogger("test")
    monitor = group_monitor_module.GroupMonitor(60, logger)
    cluster = _FakeCluster("cluster-1")

    try:
        monitor.monitor_cluster(cluster, lambda *args: None, logger)
        monitor._drain_wake_socket()
        monitor.start()

        assert _wait_until(select_entered.is_set), "GroupMonitor never entered select()"

        cluster_instance = _FakeMonitoredCluster.instances[(cluster.namespace, cluster.name)]
        session = cluster_instance.session

        monitor.stop()
        monitor.join(timeout=1)

        assert not monitor.is_alive(), "stop() did not stop GroupMonitor"
        assert session.closed, "stop() did not close the active cluster session"
        assert cluster_instance.session is None
        assert monitor._wake_reader is None
        assert monitor._wake_writer is None

        monitor.stop()
    finally:
        if monitor.is_alive():
            monitor.stop()
            monitor.join(timeout=1)
