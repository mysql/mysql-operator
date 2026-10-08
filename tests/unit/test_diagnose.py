# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import importlib.util
import logging
from pathlib import Path
import sys
import types
from unittest.mock import Mock

import pytest


@pytest.fixture
def diagnose_module(monkeypatch):
    controller = importlib.import_module("mysqloperator.controller")
    for name in ("shellutils", "utils", "consts", "errors"):
        stub = types.ModuleType(f"mysqloperator.controller.{name}")
        monkeypatch.setitem(sys.modules, stub.__name__, stub)
        monkeypatch.setattr(controller, name, stub, raising=False)
    cluster_api = types.ModuleType("mysqloperator.controller.innodbcluster.cluster_api")
    cluster_api.InnoDBCluster = type("InnoDBCluster", (), {})
    cluster_api.MySQLPod = type("MySQLPod", (), {})
    monkeypatch.setitem(sys.modules, cluster_api.__name__, cluster_api)
    mysqlsh = types.ModuleType("mysqlsh")
    mysqlsh.mysql = types.SimpleNamespace()
    mysqlsh.Error = type("ShellError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "mysqlsh", mysqlsh)
    path = Path(__file__).resolve().parents[2] / "mysqloperator/controller/diagnose.py"
    spec = importlib.util.spec_from_file_location("mysqloperator.controller._diagnose_role_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("role_fields,expected_type", [
    ({}, "PRIMARY"),
    ({"clusterRole": "PRIMARY"}, "PRIMARY"),
    ({"clusterRole": "REPLICA"}, "REPLICA"),
    ({"clusterRole": "UNRECOGNIZED"}, "UNKNOWN"),
])
def test_instance_diagnosis_reports_cluster_role(diagnose_module, role_fields, expected_type):
    pod = types.SimpleNamespace(name="mycluster-0", endpoint="mycluster-0:3306",
                                instance_type="group-member")
    shell_status = {
        **role_fields,
        "defaultReplicaSet": {
            "status": "OK",
            "groupViewId": "view-1",
            "topology": {pod.endpoint: {"status": "ONLINE", "memberRole": "PRIMARY"}},
        },
    }
    dba = Mock()
    dba.session.run_sql.return_value.fetch_one.return_value = ["uuid:1-10"]
    dba.get_cluster.return_value.status.return_value = shell_status

    result = diagnose_module.diagnose_instance(pod, logging.getLogger(__name__), dba)

    assert result.cluster_in_cluster_set_type.value == expected_type
    assert result.status.value == "ONLINE"
