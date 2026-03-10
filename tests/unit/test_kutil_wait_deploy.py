import importlib
import json
import pathlib
import sys
import types

import pytest


TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _drop_modules(*module_names):
    for module_name in module_names:
        sys.modules.pop(module_name, None)


@pytest.fixture
def kutil_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules("utils.kutil", "setup.config", "setup.defaults")

    module = importlib.import_module("utils.kutil")
    yield module

    _drop_modules("utils.kutil", "setup.config", "setup.defaults")


def test_deployment_label_selector_supports_match_expressions(kutil_module):
    deploy = {
        "spec": {
            "selector": {
                "matchLabels": {
                    "app": "mysql-operator",
                    "component": "controller",
                },
                "matchExpressions": [
                    {
                        "key": "scope",
                        "operator": "In",
                        "values": ["global", "scoped"],
                    },
                    {
                        "key": "legacy",
                        "operator": "DoesNotExist",
                    },
                ],
            },
        },
    }

    assert kutil_module._deployment_label_selector(deploy) == (
        "app=mysql-operator,component=controller,scope in (global,scoped),!legacy"
    )


def test_wait_deploy_fails_fast_on_fatal_pod_state(monkeypatch, kutil_module):
    diagnostics = []
    deploy = {
        "spec": {
            "replicas": 1,
            "selector": {
                "matchLabels": {
                    "app": "mysql-operator",
                },
            },
        },
        "status": {
            "replicas": 1,
            "readyReplicas": 0,
            "updatedReplicas": 0,
            "availableReplicas": 0,
        },
    }

    monkeypatch.setattr(
        kutil_module,
        "wait_deploy_exists",
        lambda ns, name, timeout=300, checkabort=lambda: None: {"NAME": name},
    )
    monkeypatch.setattr(
        kutil_module,
        "get_deploy",
        lambda ns, name, check=False: deploy,
    )

    def fake_kubectl(
        cmd,
        rsrc=None,
        args=None,
        timeout=None,
        check=True,
        ignore=None,
        timeout_diagnostics=None,
        cmd_output_log=None,
    ):
        assert (cmd, rsrc) == ("get", "po")
        return types.SimpleNamespace(
            stdout=json.dumps(
                {
                    "items": [
                        {
                            "metadata": {
                                "name": "mysql-operator-abc",
                            },
                            "status": {
                                "phase": "Running",
                                "containerStatuses": [
                                    {
                                        "name": "mysql-operator",
                                        "state": {
                                            "waiting": {
                                                "reason": "CrashLoopBackOff",
                                            },
                                        },
                                    },
                                ],
                            },
                        },
                    ],
                }
            ).encode("utf8")
        )

    monkeypatch.setattr(kutil_module, "kubectl", fake_kubectl)
    monkeypatch.setattr(
        kutil_module,
        "store_deploy_diagnostics",
        lambda ns, name: diagnostics.append(("deploy", ns, name)),
    )
    monkeypatch.setattr(
        kutil_module,
        "store_pod_diagnostics",
        lambda ns, name: diagnostics.append(("pod", ns, name)),
    )
    monkeypatch.setattr(
        kutil_module.time,
        "sleep",
        lambda seconds: pytest.fail(
            "wait_deploy should fail before the first retry sleep"
        ),
    )

    with pytest.raises(
        Exception,
        match=(
            r"Deployment test-ns / mysql-operator pod mysql-operator-abc "
            r"entered fatal startup state: "
            r"container mysql-operator waiting: CrashLoopBackOff"
        ),
    ):
        kutil_module.wait_deploy("test-ns", "mysql-operator", timeout=5)

    assert diagnostics == [
        ("deploy", "test-ns", "mysql-operator"),
        ("pod", "test-ns", "mysql-operator-abc"),
    ]
