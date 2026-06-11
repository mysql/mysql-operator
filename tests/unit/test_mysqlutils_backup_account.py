# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import sys
import types

import pytest


@pytest.fixture
def mysqlutils_module(monkeypatch):
    for module_name in [
        "mysqloperator.controller.mysqlutils",
        "mysqlsh",
    ]:
        sys.modules.pop(module_name, None)

    mysqlsh_stub = types.ModuleType("mysqlsh")
    mysqlsh_stub.mysql = types.SimpleNamespace(
        ErrorCode=types.SimpleNamespace(CR_MIN_ERROR=2000, CR_MAX_ERROR=2999)
    )
    monkeypatch.setitem(sys.modules, "mysqlsh", mysqlsh_stub)

    module = importlib.import_module("mysqloperator.controller.mysqlutils")
    yield module
    sys.modules.pop("mysqloperator.controller.mysqlutils", None)


class _FakeSession:
    def __init__(self):
        self.calls = []

    def run_sql(self, statement, params=None):
        self.calls.append((statement, params))


def test_setup_backup_account_recreates_fixed_user(mysqlutils_module):
    session = _FakeSession()

    mysqlutils_module.setup_backup_account(session, "mysqlbackup", "secret")

    assert session.calls[0] == (
        "DROP USER IF EXISTS ?@?",
        ["mysqlbackup", "%"])
    assert session.calls[1] == (
        "CREATE USER ?@? IDENTIFIED BY ?",
        ["mysqlbackup", "%", "secret"])
