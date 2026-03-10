# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import mysqlsh
import threading
import time
import select
import socket
import os
from logging import Logger, getLogger
from typing import Callable, Optional, TYPE_CHECKING, Tuple, List

from . import shellutils, consts
from mysqloperator.controller.innodbcluster.cluster_api import InnoDBCluster
from mysqloperator.controller.shellutils import RetryLoop


mysql = mysqlsh.mysql
mysqlx = mysqlsh.mysqlx

k_connect_retry_interval = 10


def _get_group_monitor_poll_timeout_seconds(logger: Logger) -> int:
    env_value = os.getenv("OPERATOR_GROUP_MONITOR_POLL_TIMEOUT")
    if env_value is None:
        return consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS

    try:
        poll_timeout_seconds = int(env_value)
    except ValueError:
        logger.warning(
            "Invalid OPERATOR_GROUP_MONITOR_POLL_TIMEOUT=%r, using default %s seconds",
            env_value,
            consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS,
        )
        return consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS

    if poll_timeout_seconds <= 0:
        logger.warning(
            "Non-positive OPERATOR_GROUP_MONITOR_POLL_TIMEOUT=%r, using default %s seconds",
            env_value,
            consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS,
        )
        return consts.GROUP_MONITOR_POLL_TIMEOUT_SECONDS

    return poll_timeout_seconds


class MonitoredCluster:
    def __init__(self, cluster: InnoDBCluster,
                 account: Tuple[str, str],
                 handler: Callable[[InnoDBCluster, list[tuple], bool], None]):
        self.cluster = cluster
        self.account = account

        self.session = None
        self._active = True
        self.target = None
        self.target_not_primary = None
        self.last_connect_attempt = 0
        self.last_primary_id = None
        self.last_view_id = None

        self.handler = handler
        self.logger: Logger = getLogger(f"CM_{self.cluster.name}")

    @property
    def name(self) -> str:
        return self.cluster.name

    @property
    def namespace(self) -> str:
        return self.cluster.namespace

    @property
    def active(self) -> bool:
        return self._active

    def deactivate(self) -> None:
        self._active = False

    def ensure_connected(self) -> Optional['mysqlx.Session']:
        if not self.active:
            return None

        # TODO run a ping every X seconds
        if not self.session and (not self.last_connect_attempt or time.time() - self.last_connect_attempt > k_connect_retry_interval):
            self.logger.info(f"Trying to connect to a member of cluster {self.cluster.namespace}/{self.cluster.name}")
            self.last_connect_attempt = time.time()
            self.session = None
            self.connect_to_primary()

            # force a refresh after we connect so we don't miss anything
            # that happened while we were out
            if self.session:
                self.logger.info(f"Connection to member of {self.cluster.namespace}/{self.cluster.name} OK")
                self.on_view_change(None)
            else:
                self.logger.error(f"Connection to member of {self.cluster.namespace}/{self.cluster.name} FAILED")

        return self.session

    def connect_to_primary(self) -> None:
        while True:
            session, is_primary = self.find_primary()
            if not is_primary:
                if session:
                    self.logger.error(f"Could not connect to PRIMARY of cluster {self.cluster.namespace}/{self.cluster.name}")
                else:
                    self.logger.error(f"Could not connect to neither PRIMARY nor SECONDARY of cluster {self.cluster.namespace}/{self.cluster.name}")

            if session:
                try:
                    # extend number of seconds for the server to wait for a command to arrive to a full day
                    session.run_sql(f"set session mysqlx_wait_timeout = {24*60*60}")
                    session._enable_notices(["GRViewChanged"])
                    co = shellutils.parse_uri(session.uri)
                    self.target = f"{co['host']}:{co['port']}"
                    self.target_not_primary = not is_primary
                    self.session = session
                except mysqlsh.Error as e:
                    if mysql.ErrorCode.CR_MAX_ERROR >= e.code >= mysql.ErrorCode.CR_MIN_ERROR:
                        # Try again if the server we were connectd to is gone
                        continue
                    else:
                        raise
            else:
                self.session = None
            break

    def find_primary(self) -> Tuple[Optional['mysqlx.Session'], bool]:
        not_primary = None

        pods = self.cluster.get_pods()
        # Try to find the PRIMARY the easy way
        for pod in pods:
            member_info = pod.get_membership_info()
            if member_info and member_info.get("role") == "PRIMARY":
                session = self.try_connect(pod)
                if session:
                    s = shellutils.jump_to_primary(session, self.account)
                    if s:
                        if s != session:
                            session.close()
                        return s, True
                    else:
                        not_primary = session

        # Try to connect to anyone and find the primary from there
        for pod in pods:
            session = self.try_connect(pod)
            if session:
                s = shellutils.jump_to_primary(session, self.account)
                if s:
                    if s != session:
                        session.close()
                    return s, True
                else:
                    not_primary = session

        return not_primary, False

    def try_connect(self, pod) -> Optional['mysqlx.Session']:
        try:
            session = mysqlx.get_session(pod.xendpoint_co)
        except mysqlsh.Error as e:
            self.logger.error(f"ERROR CONNECTING TO {pod.xendpoint}: {e}")
            return None

        return session

    def handle_notice(self) -> None:
        if not self.active:
            return

        while self.active:
            session = self.session
            if not session:
                break
            try:
                # TODO hack to force unexpected async notice to be read, xsession should read packets itself
                session.run_sql("select 1")
                notice = session._fetch_notice()
                if not notice:
                    break
                self.logger.info(f"GOT NOTICE {notice}")
                self.on_view_change(notice.get("view_id"))
                if not self.active or not self.session or self.session is not session:
                    break

            except mysqlsh.Error as e:
                self.logger.error(f"ERROR FETCHING NOTICE: dest={self.target} error={e}")
                if self.session is session:
                    self.session.close()
                    self.session = None
                break

    def on_view_change(self, view_id: Optional[str]) -> None:
        if not self.active or not self.session:
            return

        members = shellutils.query_members(self.session)
        if not self.active:
            return
        self.handler(self.cluster, members, view_id != self.last_view_id)
        self.last_view_id = view_id

        primary = None
        force_reconnect = False
        for member_id, role, status, view_id, endpoint, version in members:
            if self.last_primary_id == member_id and role != "PRIMARY":
                force_reconnect = True
                break
            if role == "PRIMARY" and not primary:
                primary = member_id

        self.last_primary_id = primary

        # force reconnection if the PRIMARY changed or we're not connected to the PRIMARY
        if self.target_not_primary or force_reconnect:
            self.logger.info(f"PRIMARY changed for {self.cluster.namespace}/{self.cluster.name}")
            if self.session:
                self.session.close()
                self.session = None


# TODO change this to a per cluster kopf.daemon?
class GroupMonitor(threading.Thread):
    def __init__(self, poll_timeout_seconds: int, logger: Logger):
        super().__init__(daemon=True, name="group-monitor")

        self.clusters : List[MonitoredCluster] = []
        self._pending_cleanup: List[MonitoredCluster] = []
        self.stopped = False
        self._thread_started = False
        self._cleanup_complete = False
        self._lock = threading.Lock()
        self._wake_reader, self._wake_writer = socket.socketpair()
        self._wake_reader.setblocking(False)
        self._wake_writer.setblocking(False)

        self.poll_timeout_seconds = poll_timeout_seconds
        self.logger = logger

    def start(self) -> None:
        with self._lock:
            if self._thread_started:
                raise RuntimeError("threads can only be started once")
            if self._cleanup_complete:
                raise RuntimeError("cannot start a stopped GroupMonitor")
            self._thread_started = True

        try:
            super().start()
        except Exception:
            with self._lock:
                self._thread_started = False
            raise

    def _wake_monitor(self) -> None:
        wake_writer = self._wake_writer
        if wake_writer is None:
            return

        try:
            wake_writer.send(b"\0")
        except BlockingIOError:
            pass
        except OSError:
            pass

    def _drain_wake_socket(self) -> None:
        wake_reader = self._wake_reader
        if wake_reader is None:
            return

        while True:
            try:
                payload = wake_reader.recv(4096)
                if not payload or len(payload) < 4096:
                    break
            except BlockingIOError:
                break
            except OSError:
                break

    def _close_wake_sockets(self) -> None:
        with self._lock:
            wake_reader = self._wake_reader
            wake_writer = self._wake_writer
            self._wake_reader = None
            self._wake_writer = None

        for wake_socket in (wake_reader, wake_writer):
            if wake_socket is None:
                continue
            try:
                wake_socket.close()
            except OSError:
                pass

    def _close_cluster_session(self, cluster: MonitoredCluster) -> None:
        cluster.deactivate()
        session = cluster.session
        if not session:
            return

        cluster.session = None

        try:
            session.close()
        except Exception as exc:
            self.logger.warning(
                "Failed to close monitor session for %s/%s: %s",
                cluster.namespace,
                cluster.name,
                exc,
            )

    def _take_pending_cleanup_clusters(self) -> List[MonitoredCluster]:
        with self._lock:
            pending_cleanup = list(self._pending_cleanup)
            self._pending_cleanup.clear()
        return pending_cleanup

    def _take_all_clusters_for_cleanup(self) -> List[MonitoredCluster]:
        with self._lock:
            cleanup_candidates = [*self._pending_cleanup, *self.clusters]
            self._pending_cleanup.clear()
            self.clusters.clear()

        unique_clusters: List[MonitoredCluster] = []
        seen = set()
        for cluster in cleanup_candidates:
            cluster_id = id(cluster)
            if cluster_id in seen:
                continue
            seen.add(cluster_id)
            unique_clusters.append(cluster)
        return unique_clusters

    def _cleanup_clusters(self, clusters: List[MonitoredCluster]) -> None:
        for cluster in clusters:
            self._close_cluster_session(cluster)

    def _find_monitored_cluster(self, cluster: InnoDBCluster) -> Optional[MonitoredCluster]:
        for monitored_cluster in self.clusters:
            if monitored_cluster.name == cluster.name and monitored_cluster.namespace == cluster.namespace:
                return monitored_cluster
        return None

    def _thread_owns_cleanup(self) -> bool:
        with self._lock:
            return self._thread_started and not self._cleanup_complete

    def _finalize_shutdown(self) -> None:
        self._cleanup_clusters(self._take_all_clusters_for_cleanup())
        self._close_wake_sockets()
        with self._lock:
            self.stopped = True
            self._cleanup_complete = True

    def monitor_cluster(self, cluster: InnoDBCluster,
                        handler: Callable[[InnoDBCluster, list[tuple], bool], None],
                        logger: Logger) -> None:
        with self._lock:
            if self.stopped:
                return
            if self._find_monitored_cluster(cluster):
                return

        # We could get called here before the Secret is ready
        account = RetryLoop(self.logger).call(cluster.get_admin_account)

        with self._lock:
            if self.stopped:
                return
            if self._find_monitored_cluster(cluster):
                return
            target = MonitoredCluster(cluster, account, handler)
            self.clusters.append(target)
        self.logger.info(f"ADDED A MONITOR FOR {cluster.namespace}/{cluster.name}")
        self._wake_monitor()

    def remove_cluster(self, cluster: InnoDBCluster) -> None:
        removed_cluster = None
        with self._lock:
            for c in self.clusters:
                if c.name == cluster.name and c.namespace == cluster.namespace:
                    self.clusters.remove(c)
                    c.deactivate()
                    self._pending_cleanup.append(c)
                    removed_cluster = c
                    self.logger.info(f"REMOVED THE MONITOR OF CLUSTER {cluster.namespace}/{cluster.name}")
                    break

        if not removed_cluster:
            return

        if self._thread_owns_cleanup():
            self._wake_monitor()
        else:
            self._cleanup_clusters([removed_cluster])

    def _is_monitored_cluster(self, cluster: MonitoredCluster) -> bool:
        with self._lock:
            return cluster in self.clusters

    def run(self) -> None:
        try:
            while True:
                self._cleanup_clusters(self._take_pending_cleanup_clusters())

                with self._lock:
                    if self.stopped:
                        break
                    clusters = list(self.clusters)

                session_fds_to_cluster = {}
                for cluster in clusters:
                    if not cluster.active or not self._is_monitored_cluster(cluster):
                        continue
                    cluster.ensure_connected()
                    if not cluster.active or not self._is_monitored_cluster(cluster):
                        self._close_cluster_session(cluster)
                        continue
                    if cluster.session:
                        session_fds_to_cluster[cluster.session._get_socket_fd()] = cluster

                wake_reader = self._wake_reader
                if wake_reader is None:
                    break
                wake_reader_fd = wake_reader.fileno()

                ready, _, _ = select.select(
                    [wake_reader_fd, *session_fds_to_cluster.keys()],
                    [],
                    [],
                    self.poll_timeout_seconds,
                )

                if wake_reader_fd in ready:
                    self._drain_wake_socket()
                    continue

                for fd in ready:
                    if fd == wake_reader_fd:
                        continue
                    cluster = session_fds_to_cluster.get(fd)
                    if cluster and cluster.active and self._is_monitored_cluster(cluster):
                        cluster.handle_notice()
        finally:
            self._finalize_shutdown()

    def stop(self) -> None:
        with self._lock:
            self.stopped = True
            if self._cleanup_complete:
                return
            thread_owns_cleanup = self._thread_started and not self._cleanup_complete

        if thread_owns_cleanup:
            # stop() is asynchronous here; callers should join() to wait for cleanup.
            self._wake_monitor()
        else:
            self._finalize_shutdown()


def get_group_monitor() -> GroupMonitor:
    logger = getLogger("GROUP_MONITOR")
    poll_timeout_seconds = _get_group_monitor_poll_timeout_seconds(logger)
    return GroupMonitor(poll_timeout_seconds, logger)


g_group_monitor = get_group_monitor()
