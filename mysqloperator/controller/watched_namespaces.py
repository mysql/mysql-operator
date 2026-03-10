#!/usr/bin/env python

# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from __future__ import annotations


class WatchedNamespaces:
    def __init__(self, namespaces: str | None):
        raw: list[str] = []

        for namespace in (namespaces or "").split(","):
            namespace = namespace.strip()
            if namespace:
                raw.append(namespace)

        self._namespaces = list(dict.fromkeys(raw))

    def validate(self) -> None:
        invalid = [namespace for namespace in self._namespaces if any(ch in namespace for ch in ("*", "?", "!"))]
        if invalid:
            raise ValueError(
                "Invalid values: deployment.namespaces / OPERATOR_NAMESPACES supports explicit namespace names only. "
                f"Remove pattern entries: {', '.join(invalid)}"
            )

    def get_namespaces(self) -> list[str]:
        self.validate()
        return list(self._namespaces)

    def is_clusterwide(self) -> bool:
        return len(self._namespaces) == 0

    def resolve_operator_namespaces(self) -> list[str] | None:
        namespaces = self.get_namespaces()
        return None if not namespaces else namespaces