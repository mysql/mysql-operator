# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from collections.abc import Callable, Sequence
from typing import Any, Optional


JsonDict = dict[str, Any]
JsonPatch = list[JsonDict]


def build_remove_finalizer_json_patch(finalizers: Optional[Sequence[str]], finalizer: str) -> Optional[JsonPatch]:
    if not finalizers or finalizer not in finalizers:
        return None

    finalizer_index = finalizers.index(finalizer)
    path = f"/metadata/finalizers/{finalizer_index}"
    return [
        {"op": "test", "path": path, "value": finalizer},
        {"op": "remove", "path": path},
    ]


def remove_finalizer_from_body(body: Optional[JsonDict], finalizer: str) -> None:
    if body is None:
        return

    metadata = body.get("metadata")
    if not metadata:
        return

    finalizers = metadata.get("finalizers")
    if isinstance(finalizers, list) and finalizer in finalizers:
        finalizers.remove(finalizer)


def _treat_not_found_as_success(body: Optional[JsonDict], finalizer: str) -> JsonDict:
    if body is None:
        return {}

    remove_finalizer_from_body(body, finalizer)
    return body


def remove_finalizer_with_json_patch(
    read_current: Callable[[], JsonDict],
    patch_current: Callable[[JsonPatch], JsonDict],
    finalizer: str,
    max_attempts: int = 3,
) -> JsonDict:
    """
    Remove one finalizer token without sending the full finalizer list back.

    This is used for resources that can race with Kopf or other controllers
    updating metadata.finalizers while the object is already being deleted.
    """

    last_error: Optional[Exception] = None
    current: Optional[JsonDict] = None

    for _ in range(max_attempts):
        try:
            current = read_current()
        except Exception as exc:
            if getattr(exc, "status", None) == 404:
                # The object can disappear before the first read or before a
                # retry after a conflicting metadata update. That still means
                # the finalizer is effectively gone.
                return _treat_not_found_as_success(current, finalizer)
            raise

        patch = build_remove_finalizer_json_patch(
            current.get("metadata", {}).get("finalizers"),
            finalizer,
        )
        if patch is None:
            return current

        try:
            return patch_current(patch)
        except Exception as exc:
            status = getattr(exc, "status", None)
            if status == 404:
                # The object can disappear between the read and JSON Patch call
                # while deletion is already in progress. Treat that as success.
                return _treat_not_found_as_success(current, finalizer)
            if status not in (409, 422):
                raise
            last_error = exc

    try:
        current = read_current()
    except Exception as exc:
        if getattr(exc, "status", None) == 404:
            # The object can also disappear after the last retryable conflict,
            # before the final verification read. That still means success.
            return _treat_not_found_as_success(current, finalizer)
        raise

    if build_remove_finalizer_json_patch(current.get("metadata", {}).get("finalizers"), finalizer) is None:
        return current

    if last_error is not None:
        raise last_error

    return current
