# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import pytest

from mysqloperator.controller.finalizers import (
    build_remove_finalizer_json_patch,
    remove_finalizer_from_body,
    remove_finalizer_with_json_patch,
)


KOPF_FINALIZER = "kopf.zalando.org/KopfFinalizerMarker"
CLUSTER_FINALIZER = "mysql.oracle.com/cluster"


def _expected_remove_finalizer_patch(index, finalizer=CLUSTER_FINALIZER):
    path = f"/metadata/finalizers/{index}"
    return [
        {
            "op": "test",
            "path": path,
            "value": finalizer,
        },
        {
            "op": "remove",
            "path": path,
        },
    ]


class FakeApiException(Exception):
    def __init__(self, status, detail=None):
        message = f"status={status}" if detail is None else f"status={status} {detail}"
        super().__init__(message)
        self.status = status
        self.detail = detail


def test_build_remove_finalizer_json_patch_targets_only_requested_finalizer():
    patch = build_remove_finalizer_json_patch(
        [KOPF_FINALIZER, CLUSTER_FINALIZER],
        CLUSTER_FINALIZER,
    )

    assert patch == _expected_remove_finalizer_patch(1)


def test_remove_finalizer_from_body_keeps_other_finalizers():
    body = {
        "metadata": {
            "finalizers": [
                KOPF_FINALIZER,
                CLUSTER_FINALIZER,
            ]
        }
    }

    remove_finalizer_from_body(body, CLUSTER_FINALIZER)

    assert body["metadata"]["finalizers"] == [KOPF_FINALIZER]


def test_remove_finalizer_with_json_patch_succeeds_on_first_attempt():
    current = {"metadata": {"finalizers": [KOPF_FINALIZER, CLUSTER_FINALIZER]}}
    patched = {"metadata": {"finalizers": [KOPF_FINALIZER]}}
    read_calls = 0
    patches = []

    def read_current():
        nonlocal read_calls
        read_calls += 1
        return current

    def patch_current(patch):
        patches.append(patch)
        return patched

    result = remove_finalizer_with_json_patch(
        read_current,
        patch_current,
        CLUSTER_FINALIZER,
    )

    assert result is patched
    assert read_calls == 1
    assert patches == [_expected_remove_finalizer_patch(1)]


@pytest.mark.parametrize("status", [409, 422])
def test_remove_finalizer_with_json_patch_retries_after_race(status):
    reads = iter(
        [
            {
                "metadata": {
                    "finalizers": [
                        KOPF_FINALIZER,
                        CLUSTER_FINALIZER,
                    ]
                }
            },
            {
                "metadata": {
                    "finalizers": [
                        CLUSTER_FINALIZER,
                    ]
                }
            },
        ]
    )
    read_calls = 0
    patches = []
    patched = {"metadata": {"finalizers": []}}

    def read_current():
        nonlocal read_calls
        read_calls += 1
        return next(reads)

    def patch_current(patch):
        patches.append(patch)
        if len(patches) == 1:
            raise FakeApiException(status=status)
        return patched

    result = remove_finalizer_with_json_patch(
        read_current,
        patch_current,
        CLUSTER_FINALIZER,
    )

    assert result is patched
    assert read_calls == 2
    assert patches == [
        _expected_remove_finalizer_patch(1),
        _expected_remove_finalizer_patch(0),
    ]


def test_remove_finalizer_with_json_patch_is_noop_when_already_removed():
    patch_called = False
    read_calls = 0
    current = {"metadata": {"finalizers": [KOPF_FINALIZER]}}

    def read_current():
        nonlocal read_calls
        read_calls += 1
        return current

    def patch_current(_patch):
        nonlocal patch_called
        patch_called = True
        return {}

    result = remove_finalizer_with_json_patch(
        read_current,
        patch_current,
        CLUSTER_FINALIZER,
    )

    assert result is current
    assert read_calls == 1
    assert patch_called is False


def test_remove_finalizer_with_json_patch_treats_not_found_as_success():
    current = {
        "metadata": {
            "finalizers": [
                KOPF_FINALIZER,
                CLUSTER_FINALIZER,
            ]
        }
    }
    patches = []

    def read_current():
        return current

    def patch_current(patch):
        patches.append(patch)
        raise FakeApiException(status=404)

    result = remove_finalizer_with_json_patch(
        read_current,
        patch_current,
        CLUSTER_FINALIZER,
    )

    assert result is current
    assert current["metadata"]["finalizers"] == [KOPF_FINALIZER]
    assert patches == [_expected_remove_finalizer_patch(1)]


def test_remove_finalizer_with_json_patch_treats_not_found_on_first_read_as_success():
    read_calls = 0
    patch_called = False

    def read_current():
        nonlocal read_calls
        read_calls += 1
        raise FakeApiException(status=404)

    def patch_current(_patch):
        nonlocal patch_called
        patch_called = True
        return {}

    result = remove_finalizer_with_json_patch(
        read_current,
        patch_current,
        CLUSTER_FINALIZER,
    )

    assert result == {}
    assert read_calls == 1
    assert patch_called is False


@pytest.mark.parametrize("status", [409, 422])
def test_remove_finalizer_with_json_patch_treats_not_found_on_final_read_after_retry_exhaustion_as_success(status):
    read_bodies = [
        {"metadata": {"finalizers": [KOPF_FINALIZER, CLUSTER_FINALIZER]}}
        for _ in range(3)
    ]
    read_calls = 0
    patches = []
    failures = []

    def read_current():
        nonlocal read_calls
        if read_calls == len(read_bodies):
            read_calls += 1
            raise FakeApiException(status=404)

        body = read_bodies[read_calls]
        read_calls += 1
        return body

    def patch_current(patch):
        patches.append(patch)
        error = FakeApiException(status=status, detail=f"attempt={len(patches)}")
        failures.append(error)
        raise error

    result = remove_finalizer_with_json_patch(
        read_current,
        patch_current,
        CLUSTER_FINALIZER,
        max_attempts=3,
    )

    assert result is read_bodies[-1]
    assert read_calls == 4
    assert len(failures) == 3
    assert patches == [
        _expected_remove_finalizer_patch(1),
        _expected_remove_finalizer_patch(1),
        _expected_remove_finalizer_patch(1),
    ]
    assert read_bodies[-1]["metadata"]["finalizers"] == [KOPF_FINALIZER]


@pytest.mark.parametrize("status", [409, 422])
def test_remove_finalizer_with_json_patch_raises_last_retryable_error_after_exhaustion(status):
    read_bodies = [
        {"metadata": {"finalizers": [KOPF_FINALIZER, CLUSTER_FINALIZER]}}
        for _ in range(4)
    ]
    read_calls = 0
    patches = []
    failures = []

    def read_current():
        nonlocal read_calls
        body = read_bodies[read_calls]
        read_calls += 1
        return body

    def patch_current(patch):
        patches.append(patch)
        error = FakeApiException(status=status, detail=f"attempt={len(patches)}")
        failures.append(error)
        raise error

    with pytest.raises(FakeApiException) as exc_info:
        remove_finalizer_with_json_patch(
            read_current,
            patch_current,
            CLUSTER_FINALIZER,
            max_attempts=3,
        )

    assert exc_info.value is failures[-1]
    assert read_calls == 4
    assert len(failures) == 3
    assert patches == [
        _expected_remove_finalizer_patch(1),
        _expected_remove_finalizer_patch(1),
        _expected_remove_finalizer_patch(1),
    ]
    assert read_bodies[-1]["metadata"]["finalizers"] == [KOPF_FINALIZER, CLUSTER_FINALIZER]
