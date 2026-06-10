# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib.util
import sys
import types


def _identity_decorator(*args, **kwargs):
    def decorate(fn):
        return fn

    return decorate


def _install_kopf_stub() -> None:
    if importlib.util.find_spec("kopf") is not None:
        return

    kopf_stub = types.ModuleType("kopf")
    kopf_stub.TemporaryError = type("TemporaryError", (Exception,), {})
    kopf_stub.PermanentError = type("PermanentError", (Exception,), {})
    kopf_stub.OperatorSettings = type("OperatorSettings", (), {})
    kopf_stub.Patch = dict
    kopf_stub.Body = dict
    kopf_stub.adopt = lambda *args, **kwargs: None
    kopf_stub.configure = lambda *args, **kwargs: None
    kopf_stub.operator = lambda *args, **kwargs: None
    kopf_stub.on = types.SimpleNamespace(
        cleanup=_identity_decorator,
        create=_identity_decorator,
        delete=_identity_decorator,
        field=_identity_decorator,
        login=_identity_decorator,
        resume=_identity_decorator,
        startup=_identity_decorator,
        update=_identity_decorator,
    )

    bodies_stub = types.ModuleType("kopf._cogs.structs.bodies")
    bodies_stub.Body = dict

    sys.modules["kopf"] = kopf_stub
    sys.modules["kopf._cogs"] = types.ModuleType("kopf._cogs")
    sys.modules["kopf._cogs.structs"] = types.ModuleType("kopf._cogs.structs")
    sys.modules["kopf._cogs.structs.bodies"] = bodies_stub


class _MysqlshError(Exception):
    def __init__(self, code=0, msg=""):
        super().__init__(msg)
        self.code = code
        self.msg = msg


class _MysqlErrorCode:
    CR_MIN_ERROR = 2000
    CR_MAX_ERROR = 2999
    ER_ACCESS_DENIED_ERROR = 1045
    ER_ACCOUNT_HAS_BEEN_LOCKED = 3118
    ER_MUST_CHANGE_PASSWORD = 1820
    ER_NO_DB_ERROR = 1046
    ER_NO_SUCH_TABLE = 1146
    ER_UNKNOWN_SYSTEM_VARIABLE = 1193
    ER_SPECIFIC_ACCESS_DENIED_ERROR = 1227
    ER_TABLEACCESS_DENIED_ERROR = 1142
    ER_COLUMNACCESS_DENIED_ERROR = 1143
    ER_NONEXISTING_GRANT = 1141
    ER_UDF_EXISTS = 1125


def _install_mysqlsh_stub() -> None:
    if importlib.util.find_spec("mysqlsh") is not None:
        return

    mysqlsh_stub = types.ModuleType("mysqlsh")
    mysqlsh_stub.Error = _MysqlshError
    mysqlsh_stub.mysql = types.SimpleNamespace(
        ErrorCode=_MysqlErrorCode,
        get_session=lambda *args, **kwargs: None,
    )
    mysqlsh_stub.mysqlx = types.SimpleNamespace()
    mysqlsh_stub.globals = types.SimpleNamespace(
        shell=types.SimpleNamespace(
            options=types.SimpleNamespace(useWizards=False, logLevel=0, verbose=0),
            parse_uri=lambda uri: {},
            unparse_uri=lambda uri: str(uri),
        ),
        session=None,
        dba=None,
    )
    mysqlsh_stub.connect_dba = lambda *args, **kwargs: None
    mysqlsh_stub.Dba = object
    mysqlsh_stub.Cluster = object

    sys.modules["mysqlsh"] = mysqlsh_stub


def _install_cryptography_stub() -> None:
    if importlib.util.find_spec("cryptography") is not None:
        return

    cryptography_stub = types.ModuleType("cryptography")
    x509_stub = types.ModuleType("cryptography.x509")
    cryptography_stub.x509 = x509_stub

    sys.modules["cryptography"] = cryptography_stub
    sys.modules["cryptography.x509"] = x509_stub


def _disable_kubernetes_config_loading() -> None:
    if importlib.util.find_spec("kubernetes") is None:
        return

    from kubernetes import config

    config.load_kube_config = lambda *args, **kwargs: None
    config.load_incluster_config = lambda *args, **kwargs: None


_install_kopf_stub()
_install_mysqlsh_stub()
_install_cryptography_stub()
_disable_kubernetes_config_loading()
