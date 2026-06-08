# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import logging
import pathlib
import sys
import types
import unittest


_STUBBED_MODULES = [
    "mysqloperator.controller.innodbcluster.cluster_controller",
    "mysqloperator.controller.innodbcluster.cluster_objects",
    "mysqloperator.controller.innodbcluster.router_objects",
    "mysqloperator.controller.innodbcluster.cluster_api",
    "mysqloperator.controller.backup.backup_objects",
    "mysqloperator.controller.backup",
    "mysqloperator.controller.diagnose",
    "mysqloperator.controller.shellutils",
    "mysqloperator.controller.config",
    "mysqloperator.controller.mysqlutils",
    "kopf._cogs.structs.bodies",
    "kopf._cogs.structs",
    "kopf._cogs",
    "kopf",
    "kubernetes.client.rest",
    "kubernetes.client",
    "kubernetes",
    "mysqlsh",
]


def _clear_stubbed_modules():
    for module_name in _STUBBED_MODULES:
        sys.modules.pop(module_name, None)


def _ensure_repo_on_path():
    repo_root = pathlib.Path(__file__).resolve().parents[2]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))


def _install_mysqlsh_stub():
    class FakeMysqlshError(Exception):
        def __init__(self, code=0, msg=""):
            super().__init__(msg)
            self.code = code
            self.msg = msg

    mysql_error_codes = types.SimpleNamespace(
        CR_MAX_ERROR=2999,
        CR_MIN_ERROR=2000,
        ER_ACCESS_DENIED_ERROR=1045,
        ER_ACCOUNT_HAS_BEEN_LOCKED=3118,
        ER_MUST_CHANGE_PASSWORD=1820,
        ER_NO_DB_ERROR=1046,
        ER_NO_SUCH_TABLE=1146,
        ER_UNKNOWN_SYSTEM_VARIABLE=1193,
        ER_SPECIFIC_ACCESS_DENIED_ERROR=1227,
        ER_TABLEACCESS_DENIED_ERROR=1142,
        ER_COLUMNACCESS_DENIED_ERROR=1143,
    )
    mysqlsh_stub = types.ModuleType("mysqlsh")
    mysqlsh_stub.Error = FakeMysqlshError
    mysqlsh_stub.mysql = types.SimpleNamespace(ErrorCode=mysql_error_codes)
    mysqlsh_stub.mysqlx = types.SimpleNamespace()
    mysqlsh_stub.globals = types.SimpleNamespace(
        shell=types.SimpleNamespace(parse_uri=lambda uri: {}, unparse_uri=lambda uri: "")
    )
    sys.modules["mysqlsh"] = mysqlsh_stub
    return mysqlsh_stub


def _install_kopf_stub():
    class TemporaryError(Exception):
        def __init__(self, msg="", delay=None):
            super().__init__(msg)
            self.delay = delay

    class PermanentError(Exception):
        pass

    kopf_stub = types.ModuleType("kopf")
    kopf_stub.TemporaryError = TemporaryError
    kopf_stub.PermanentError = PermanentError
    sys.modules["kopf"] = kopf_stub

    for module_name in ["kopf._cogs", "kopf._cogs.structs"]:
        sys.modules[module_name] = types.ModuleType(module_name)
    bodies_stub = types.ModuleType("kopf._cogs.structs.bodies")
    bodies_stub.Body = dict
    sys.modules["kopf._cogs.structs.bodies"] = bodies_stub


def _install_kubernetes_stub():
    class ApiException(Exception):
        def __init__(self, status=None):
            super().__init__(f"status={status}")
            self.status = status

    for module_name in ["kubernetes", "kubernetes.client"]:
        sys.modules[module_name] = types.ModuleType(module_name)
    kubernetes_rest_stub = types.ModuleType("kubernetes.client.rest")
    kubernetes_rest_stub.ApiException = ApiException
    sys.modules["kubernetes.client.rest"] = kubernetes_rest_stub


def _install_cluster_api_stub():
    cluster_api_stub = types.ModuleType(
        "mysqloperator.controller.innodbcluster.cluster_api"
    )
    cluster_api_stub.InnoDBCluster = type("InnoDBCluster", (), {})
    cluster_api_stub.MySQLPod = type("MySQLPod", (), {})
    cluster_api_stub.client = types.SimpleNamespace()
    sys.modules["mysqloperator.controller.innodbcluster.cluster_api"] = cluster_api_stub


def _install_controller_dependency_stubs():
    shellutils_stub = types.ModuleType("mysqloperator.controller.shellutils")
    shellutils_stub.check_fatal = lambda *args, **kwargs: False
    shellutils_stub.check_fatal_connect = lambda *args, **kwargs: False
    shellutils_stub.query_membership_info = (
        lambda session: ("", "", "OFFLINE", "", "", None, None)
    )
    shellutils_stub.connect_dba = lambda *args, **kwargs: None
    shellutils_stub.DbaWrap = object
    sys.modules["mysqloperator.controller.shellutils"] = shellutils_stub

    config_stub = types.ModuleType("mysqloperator.controller.config")
    sys.modules["mysqloperator.controller.config"] = config_stub

    mysqlutils_stub = types.ModuleType("mysqloperator.controller.mysqlutils")
    mysqlutils_stub.count_gtids = lambda gtids: 0
    sys.modules["mysqloperator.controller.mysqlutils"] = mysqlutils_stub

    for module_name in [
        "mysqloperator.controller.innodbcluster.cluster_objects",
        "mysqloperator.controller.innodbcluster.router_objects",
        "mysqloperator.controller.backup",
        "mysqloperator.controller.backup.backup_objects",
    ]:
        sys.modules[module_name] = types.ModuleType(module_name)


def _install_common_stubs():
    _ensure_repo_on_path()
    _clear_stubbed_modules()
    mysqlsh_stub = _install_mysqlsh_stub()
    _install_kopf_stub()
    _install_kubernetes_stub()
    _install_cluster_api_stub()
    _install_controller_dependency_stubs()
    return mysqlsh_stub


def _load_diagnose_module():
    _install_common_stubs()
    module = importlib.import_module("mysqloperator.controller.diagnose")
    return module


def _load_cluster_controller_module():
    _install_common_stubs()
    importlib.import_module("mysqloperator.controller.diagnose")
    module = importlib.import_module(
        "mysqloperator.controller.innodbcluster.cluster_controller"
    )
    return module


class _Result:
    def __init__(self, rows):
        self._rows = list(rows)

    def fetch_one(self):
        if not self._rows:
            return None
        return self._rows.pop(0)


class _CandidateSession:
    def run_sql(self, statement, params=None):
        statement_upper = statement.upper()
        if "GTID_EXECUTED" in statement_upper:
            return _Result([[""]])
        if "GTID_SUBTRACT" in statement_upper:
            return _Result([[None]])
        raise AssertionError(f"unexpected SQL: {statement}")


class _ReadReplicaProbeSession:
    def run_sql(self, statement, params=None):
        if "@@server_uuid" in statement and "@@version" in statement:
            return _Result([["read-replica-uuid", "9.6.0"]])
        raise AssertionError(f"unexpected SQL: {statement}")


class _FakeCluster:
    def __init__(self, status):
        self._status = status
        self.status_calls = []

    def status(self, options=None):
        self.status_calls.append(options)
        return self._status


class _FakeDba:
    def __init__(self, cluster, session):
        self._cluster = cluster
        self.session = session

    def get_cluster(self):
        return self._cluster


class _FakeReadReplicaPod:
    name = "mycluster-trr-0"
    endpoint = "mycluster-trr-0.mycluster-trr-instances.ns.svc.cluster.local:3306"
    endpoint_url_safe = {"host": "mycluster-trr-0", "port": 3306}
    instance_type = "read-replica"

    def __init__(self):
        self.membership_updates = []
        self.readiness_updates = []

    def update_membership_status(self, *args, **kwargs):
        self.membership_updates.append((args, kwargs))

    def update_member_readiness_gate(self, *args):
        self.readiness_updates.append(args)


def _cluster_status_for_read_replica(replica_status):
    rr_endpoint = _FakeReadReplicaPod.endpoint
    return {
        "defaultReplicaSet": {
            "status": "OK",
            "groupViewId": "view-1",
            "topology": {
                "mycluster-0.mycluster-instances.ns.svc.cluster.local:3306": {
                    "status": "ONLINE",
                    "memberRole": "PRIMARY",
                    "readReplicas": {
                        rr_endpoint: {
                            "status": replica_status,
                        }
                    },
                }
            },
        }
    }


class ReadReplicaReconcileTests(unittest.TestCase):
    def tearDown(self):
        _clear_stubbed_modules()

    def test_offline_read_replica_in_cluster_metadata_is_rejoinable(self):
        diagnose_module = _load_diagnose_module()
        status = _cluster_status_for_read_replica("OFFLINE")
        cluster = _FakeCluster(status)
        pod = _FakeReadReplicaPod()
        session = _CandidateSession()
        dba = _FakeDba(cluster, session)

        candidate = diagnose_module.diagnose_cluster_candidate(
            session,
            cluster,
            pod,
            dba,
            logging.getLogger(__name__),
        )

        self.assertEqual(
            diagnose_module.CandidateDiagStatus.REJOINABLE,
            candidate.status,
        )

    def test_read_replica_probe_uses_cluster_status_for_readiness(self):
        cluster_controller_module = _load_cluster_controller_module()
        status = _cluster_status_for_read_replica("ONLINE")
        pod = _FakeReadReplicaPod()
        controller = object.__new__(cluster_controller_module.ClusterController)
        controller.cluster = types.SimpleNamespace(name="mycluster", namespace="ns")
        controller.dba_cluster = _FakeCluster(status)

        minfo = controller.probe_member_status(
            pod,
            _ReadReplicaProbeSession(),
            False,
            logging.getLogger(__name__),
        )

        self.assertEqual(
            (
                "read-replica-uuid",
                "READ_REPLICA",
                "ONLINE",
                "",
                "9.6.0",
                None,
                None,
            ),
            minfo,
        )
        self.assertEqual(
            [
                (
                    ("read-replica-uuid", "READ_REPLICA", "ONLINE", "", "9.6.0"),
                    {"joined": False},
                )
            ],
            pod.membership_updates,
        )
        self.assertEqual([("ready", True)], pod.readiness_updates)


if __name__ == "__main__":
    unittest.main()
