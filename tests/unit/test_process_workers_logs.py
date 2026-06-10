# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from datetime import timedelta
import pathlib


TESTS_ROOT = pathlib.Path(__file__).resolve().parents[1]
SUBTEST_FAILURE = "FAIL: test_x (__main__.T.test_x) (pod='mypod')"


def _write_log(path: pathlib.Path, issue: str) -> None:
    path.write_text(
        f"{issue}\n"
        "Ran 1 test in 0.001s\n"
        "FAILED (failures=1)\n"
    )


def _run_processor(monkeypatch, tmp_path, expected_failures: str) -> bool:
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    from ci.jobs.auxiliary import process_workers_logs

    log_path = tmp_path / "worker.log"
    expected_path = tmp_path / "expected-failures.txt"
    _write_log(log_path, SUBTEST_FAILURE)
    expected_path.write_text(expected_failures)

    return process_workers_logs.run(
        str(expected_path),
        [str(log_path)],
        timedelta(seconds=1),
    )


def test_subtest_failure_is_unexpected_failure(monkeypatch, tmp_path):
    assert _run_processor(monkeypatch, tmp_path, "") is False


def test_subtest_failure_can_be_expected(monkeypatch, tmp_path):
    assert _run_processor(monkeypatch, tmp_path, f"{SUBTEST_FAILURE}\n") is True
