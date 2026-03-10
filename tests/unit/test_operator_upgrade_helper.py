import importlib
import pathlib
import sys
import types
import unittest

import pytest


TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _drop_modules(*module_names):
    for module_name in module_names:
        sys.modules.pop(module_name, None)


@pytest.fixture
def operator_upgrade_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules(
        "e2e.mysqloperator.operator.operator_upgrade_t",
        "e2e.mysqloperator.operator",
        "utils.kutil",
        "utils.tutil",
        "utils.optesting",
        "setup.config",
    )

    tutil_module = types.ModuleType("utils.tutil")

    class OperatorTest(unittest.TestCase):
        pass

    tutil_module.OperatorTest = OperatorTest
    sys.modules["utils.tutil"] = tutil_module

    kutil_module = types.ModuleType("utils.kutil")
    sys.modules["utils.kutil"] = kutil_module

    optesting_module = types.ModuleType("utils.optesting")
    optesting_module.COMMON_OPERATOR_ERRORS = []
    sys.modules["utils.optesting"] = optesting_module

    config_module = types.ModuleType("setup.config")
    config_module.g_ts_cfg = types.SimpleNamespace(
        get_operator_image=lambda version=None: "repo/operator:current",
        get_current_lts_version=lambda: "8.4.5",
        operator_old_version_tag="8.0.31-2.0.7",
        operator_current_lts_version_tag="8.4.5-2.1.7",
        current_lts_version="8.4.5",
        operator_version_tag="9.7.0-2.2.8",
        version_tag="9.7.0",
        operator_upgrade_run_all_tests=False,
    )
    sys.modules["setup.config"] = config_module

    module = importlib.import_module("e2e.mysqloperator.operator.operator_upgrade_t")
    yield module

    _drop_modules(
        "e2e.mysqloperator.operator.operator_upgrade_t",
        "e2e.mysqloperator.operator",
        "utils.kutil",
        "utils.tutil",
        "utils.optesting",
        "setup.config",
    )


def test_sync_default_operator_clusterroles_patches_manifest_rules(
    operator_upgrade_module,
    monkeypatch,
):
    expected_rules = {
        "mysql-operator": [{"resources": ["deployments"], "verbs": ["get", "list"]}],
        "mysql-sidecar": [{"resources": ["pods"], "verbs": ["get"]}],
        "mysql-switchover": [{"resources": ["pods"], "verbs": ["get", "patch"]}],
    }
    patch_calls = []

    monkeypatch.setattr(
        operator_upgrade_module,
        "_load_default_operator_clusterrole_rules",
        lambda: expected_rules,
    )
    monkeypatch.setattr(
        operator_upgrade_module.kutil,
        "patch",
        lambda ns, rsrc, name, changes, type=None, data_as_type="yaml", w_ns=True: patch_calls.append(
            {
                "ns": ns,
                "rsrc": rsrc,
                "name": name,
                "changes": changes,
                "type": type,
                "data_as_type": data_as_type,
                "w_ns": w_ns,
            }
        ),
        raising=False,
    )

    operator_upgrade_module._sync_default_operator_clusterroles_from_manifest()

    assert patch_calls == [
        {
            "ns": None,
            "rsrc": "clusterrole",
            "name": "mysql-operator",
            "changes": {"rules": expected_rules["mysql-operator"]},
            "type": "merge",
            "data_as_type": "json",
            "w_ns": False,
        },
        {
            "ns": None,
            "rsrc": "clusterrole",
            "name": "mysql-sidecar",
            "changes": {"rules": expected_rules["mysql-sidecar"]},
            "type": "merge",
            "data_as_type": "json",
            "w_ns": False,
        },
        {
            "ns": None,
            "rsrc": "clusterrole",
            "name": "mysql-switchover",
            "changes": {"rules": expected_rules["mysql-switchover"]},
            "type": "merge",
            "data_as_type": "json",
            "w_ns": False,
        },
    ]


def test_change_operator_version_repairs_clusterroles_even_when_image_is_current(
    operator_upgrade_module,
    monkeypatch,
):
    sync_calls = []
    patch_dp_calls = []
    wait_pod_gone_calls = []
    wait_pod_calls = []

    monkeypatch.setattr(
        operator_upgrade_module,
        "_sync_default_operator_clusterroles_from_manifest",
        lambda: sync_calls.append("sync"),
    )
    monkeypatch.setattr(
        operator_upgrade_module.kutil,
        "ls_pod",
        lambda ns, pattern: [{"NAME": "mysql-operator-current"}],
        raising=False,
    )
    monkeypatch.setattr(
        operator_upgrade_module.kutil,
        "get_po",
        lambda ns, name: {"spec": {"containers": [{"image": "repo/operator:current"}]}},
        raising=False,
    )
    monkeypatch.setattr(
        operator_upgrade_module.kutil,
        "patch_dp",
        lambda *args, **kwargs: patch_dp_calls.append((args, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        operator_upgrade_module.kutil,
        "wait_pod_gone",
        lambda *args, **kwargs: wait_pod_gone_calls.append((args, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        operator_upgrade_module.kutil,
        "wait_pod",
        lambda *args, **kwargs: wait_pod_calls.append((args, kwargs)),
        raising=False,
    )
    monkeypatch.setattr(
        operator_upgrade_module.g_ts_cfg,
        "get_operator_image",
        lambda version=None: "repo/operator:current",
        raising=False,
    )

    operator_upgrade_module.change_operator_version()

    assert sync_calls == ["sync"]
    assert patch_dp_calls == []
    assert wait_pod_gone_calls == []
    assert wait_pod_calls == []
