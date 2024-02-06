# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from logging import Logger

from .innodbcluster.cluster_api import MySQLPod
import typing
from typing import Any, Optional, Callable, TYPE_CHECKING, Union
import mysqlsh
import kopf
import time
if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession
    from mysqlsh import Dba, Cluster

mysql = mysqlsh.mysql
mysqlx = mysqlsh.mysqlx


# MySQL connection errors that are not supposed to happen while connecting to
# a cluster member. If these happen there's either a bug or someone/thing broke
# the cluster. There's no point in retrying after these.
FATAL_CONNECT_ERRORS = set([
    # Authentication errors aren't supposed to happen because we
    # only use an account we own, so access denied would indicate
    # something or someone broke our account or worse.
    mysql.ErrorCode.ER_ACCESS_DENIED_ERROR,
    mysql.ErrorCode.ER_ACCOUNT_HAS_BEEN_LOCKED
])

# Same as above, but for errors that happen while executing SQL.
FATAL_SQL_ERRORS = set([
    mysql.ErrorCode.ER_MUST_CHANGE_PASSWORD,
    mysql.ErrorCode.ER_NO_DB_ERROR,
    mysql.ErrorCode.ER_NO_SUCH_TABLE,
    mysql.ErrorCode.ER_UNKNOWN_SYSTEM_VARIABLE,
    mysql.ErrorCode.ER_SPECIFIC_ACCESS_DENIED_ERROR,
    mysql.ErrorCode.ER_TABLEACCESS_DENIED_ERROR,
    mysql.ErrorCode.ER_COLUMNACCESS_DENIED_ERROR
])

FATAL_MYSQL_ERRORS = FATAL_CONNECT_ERRORS.union(FATAL_SQL_ERRORS)


def check_fatal_connect(err, where, logger) -> bool:
    if err.code in FATAL_MYSQL_ERRORS:
        logger.error(
            f"Unexpected error connecting to MySQL. This error is not expected and may indicate a bug or corrupted cluster deployment: error={err} target={where}")
        return True
    return False


def check_fatal(err, where, context, logger) -> bool:
    if err.code in FATAL_SQL_ERRORS:
        logger.error(
            f"Unexpected MySQL error. This error is not expected and may indicate a bug or corrupted cluster deployment: error={err} target={where}{' context=%s' % context if context else ''}")
        return True
    return False


class Timeout(Exception):
    pass


class GiveUp(Exception):
    def __init__(self, real_exc=None):
        self.real_exc = real_exc


T = typing.TypeVar("T")


class RetryLoop:
    def __init__(self, logger: Logger, timeout: int = 60,
                 max_tries: Optional[int] = None,
                 is_retriable: Optional[Callable] = None,
                 backoff: Callable[[int], int] = lambda i: i+1):
        self.logger = logger
        self.timeout = timeout
        self.max_tries = max_tries
        self.backoff = backoff
        self.is_retriable = is_retriable

    def call(self, f: Callable[..., T], *args, **kwargs) -> T:
        delay = 1
        tries = 0
        total_wait = 0
        while True:
            try:
                tries += 1
                return f(*args)
            except (kopf.PermanentError, kopf.TemporaryError) as err:
                # Don't retry kopf errors
                raise
            except GiveUp as err:
                self.logger.error(
                    f"Error executing {f.__qualname__}, giving up: {err.real_exc}")
                if err.real_exc:
                    raise err.real_exc
                else:
                    return None
            except mysqlsh.Error as err:
                if self.is_retriable and not self.is_retriable(err):
                    raise

                if total_wait < self.timeout and (self.max_tries is None or tries < self.max_tries):
                    self.logger.info(
                        f"Error executing {f.__qualname__}, retrying after {delay}s: {err}")
                    time.sleep(delay)
                    total_wait += delay
                    delay = self.backoff(delay)
                else:
                    self.logger.error(
                        f"Error executing {f.__qualname__}, giving up: {err}")
                    raise


class SessionWrap:
    def __init__(self, session: Union['ClassicSession', dict]) -> None:
        if isinstance(session, dict):
            try:
                self.session = mysql.get_session(session)
            except mysqlsh.Error as e:
                url = session.copy()
                if "password" in url:
                    del url["password"]
                url = mysqlsh.globals.shell.unparse_uri(url)
                raise mysqlsh.Error(
                    e.code, f"Error connecting to {url}: {e.msg}")
        else:
            self.session = session

    def __enter__(self) -> 'ClassicSession':
        return self.session

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.session.close()

    def __getattr__(self, name) -> Any:
        return getattr(self.session, name)


class DbaWrap:
    def __init__(self, dba: 'Dba'):
        self.dba = dba

    def __enter__(self):
        return self.dba

    def __exit__(self, exc_type, exc_value, traceback):
        self.dba.session.close()

    def __getattr__(self, name):
        return getattr(self.dba, name)


class ClusterWrap:
    def __init__(self, cluster: 'Cluster'):
        self.cluster = cluster

    def __enter__(self):
        return self.cluster

    def __exit__(self, exc_type, exc_value, traceback):
        self.cluster.disconnect()

    def __getattr__(self, name):
        return getattr(self.cluster, name)


def connect_dba(target: dict, logger: Logger, **kwargs) -> 'Dba':
    return RetryLoop(logger, **kwargs).call(mysqlsh.connect_dba, target)


def connect_to_pod(pod: MySQLPod, logger: Logger, **kwargs):
    def connect(target):
        session = mysqlsh.mysql.get_session(target)
        # avoid trouble with global autocommit=0
        session.run_sql("set autocommit=1")
        # make sure there's no global ansi_quotes or anything like that
        session.run_sql("set sql_mode=''")
        try:
            # avoid problems with GR consistency during queries, if GR is running
            session.run_sql("set group_replication_consistency='EVENTUAL'")
        except:
            pass
        return SessionWrap(session)

    return RetryLoop(logger, **kwargs).call(connect, pod.endpoint_co)


def jump_to_primary(session, account):
    # Check if we're already the PRIMARY
    res = session.run_sql(
        "SELECT member_role, member_host, (member_host = cast(coalesce(@@report_host, @@hostname) as char ascii)) as me"
        " FROM performance_schema.replication_group_members"
        " ORDER BY member_host")

    r = res.fetch_one()
    while r:
        if r[0] == "PRIMARY":
            if r[2]:  # we're the primary
                return session
            else:
                # connect to the PRIMARY using the same credentials
                co = mysqlsh.globals.shell.parse_uri(session.uri)
                co["user"], co["password"] = account
                co["host"] = r[1]
                try:
                    return mysqlx.get_session(co)
                except mysqlsh.Error as e:
                    print(
                        f"Could not connect to {co['host']}:{co['port']}: {e}")
                    # continue in case we're in multi-primary mode

        r = res.fetch_one()

    return None


def get_valid_cluster_handle(cluster, logger):
    """
    Try to get a cluster handle from any ONLINE pod.
    """
    ignore_pods = []

    def try_once():
        last_err = None
        for pod in cluster.get_pods():
            if pod.name not in ignore_pods:
                try:
                    dba = mysqlsh.connect_dba(pod.endpoint_co)
                except Exception as e:
                    logger.warning(
                        f"Could not connect: target={pod.endpoint} error={e}")
                    last_err = e
                    continue

                try:
                    return pod, dba, dba.get_cluster()
                except Exception as e:
                    logger.warning(
                        f"get_cluster: target={pod.endpoint} error={e}")
                    last_err = e
                    continue

        # If failed because of exception, then throw exception which will
        # cause a retry
        if last_err:
            raise last_err
        # If failed because no pods, then just return None
        return None, None, None

    return RetryLoop(logger).call(try_once)


def query_membership_info(session):
    row = session.run_sql("""SELECT m.member_id, m.member_role, m.member_state, s.view_id, m.member_version,
            (SELECT count(*) FROM performance_schema.replication_group_members) as member_count,
            (SELECT count(*) FROM performance_schema.replication_group_members WHERE member_state <> 'UNREACHABLE') as reachable_member_count
    FROM performance_schema.replication_group_members m
        JOIN performance_schema.replication_group_member_stats s
        ON m.member_id = s.member_id
    WHERE m.member_id = @@server_uuid""").fetch_one()

    if row:
        member_id = row[0] or ""
        role = row[1] or ""
        status = row[2]
        view_id = row[3] or ""
        version = row[4]
        member_count = row[5]
        reachable_member_count = row[6]
    else:
        member_id = ""
        role = ""
        status = "OFFLINE"
        view_id = ""
        version = ""
        member_count = None
        reachable_member_count = None

    return member_id, role, status, view_id, version, member_count, reachable_member_count


def query_members(session) -> list[tuple]:
    res = session.run_sql("""SELECT m.member_id, m.member_role, m.member_state,
        s.view_id, concat(m.member_host, ':', m.member_port), m.member_version
    FROM performance_schema.replication_group_members m
        JOIN performance_schema.replication_group_member_stats s
        ON m.member_id = s.member_id""")

    members = []
    row = res.fetch_one()
    while row:
        member_id = row[0] or ""
        role = row[1] or ""
        status = row[2]
        view_id = row[3] or ""
        endpoint = row[4] or ""
        version = row[5]

        members.append((member_id, role, status, view_id, endpoint, version))

        row = res.fetch_one()

    return members


def parse_uri(uri):
    return mysqlsh.globals.shell.parse_uri(uri)
