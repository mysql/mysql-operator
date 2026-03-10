import importlib
import pathlib
import sys
import types


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _drop_plugin_modules():
    for module_name in [
        "mysqloperator.controller.plugins",
        "mysqloperator.controller.utils",
        "mysqlsh",
    ]:
        sys.modules.pop(module_name, None)


def _load_plugins_module():
    _drop_plugin_modules()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))

    mysqlsh_stub = types.ModuleType("mysqlsh")

    class FakeMySQLShError(Exception):
        def __init__(self, code, message):
            super().__init__(message)
            self.code = code

    mysqlsh_stub.Error = FakeMySQLShError
    mysqlsh_stub.mysql = types.SimpleNamespace(
        ErrorCode=types.SimpleNamespace(ER_UDF_EXISTS=1125)
    )
    sys.modules["mysqlsh"] = mysqlsh_stub

    utils_stub = types.ModuleType("mysqloperator.controller.utils")
    utils_stub.version_to_int = lambda version: tuple(int(part) for part in version.split("."))
    sys.modules["mysqloperator.controller.utils"] = utils_stub

    module = importlib.import_module("mysqloperator.controller.plugins")
    return module, FakeMySQLShError


def test_udf_install_statements_omit_if_not_exists():
    plugins, _ = _load_plugins_module()

    udf_statements = plugins.SQL_INSTALL_MASKING_UDF[1:] + plugins.SQL_INSTALL_KEYRING_UDF[1:]

    assert udf_statements
    assert all(stmt.startswith("CREATE FUNCTION ") for stmt in udf_statements)
    assert all("IF NOT EXISTS" not in stmt for stmt in udf_statements)


def test_run_plugin_sql_ignores_existing_udf_errors():
    plugins, FakeMySQLShError = _load_plugins_module()
    executed = []
    warnings = []
    errors = []
    duplicate_stmt = "CREATE FUNCTION gen_blocklist RETURNS STRING SONAME 'data_masking.so'"
    followup_stmt = "CREATE FUNCTION gen_range RETURNS INTEGER SONAME 'data_masking.so'"

    class FakeSession:
        def run_sql(self, stmt):
            executed.append(stmt)
            if stmt == duplicate_stmt:
                raise FakeMySQLShError(1125, "UDF already exists")

    logger = types.SimpleNamespace(
        warn=lambda message: warnings.append(message),
        error=lambda message: errors.append(message),
    )

    plugins.run_plugin_sql(FakeSession(), [duplicate_stmt, followup_stmt], logger)

    assert executed == [duplicate_stmt, followup_stmt]
    assert warnings == [f'UDF Already exists, ignored for "{duplicate_stmt}"']
    assert errors == []
