#!/usr/bin/env python

# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .watched_namespaces import WatchedNamespaces


DEFAULT_DEPLOYMENT_NAME = "mysql-operator"
OPERATOR_CONTAINER_NAME = "mysql-operator"

_MISSING = object()
_NO_DEFAULT = object()


class OperatorTopologyError(ValueError):
    pass


def _read_field(obj: Any, field: str, default: Any = _NO_DEFAULT) -> Any:
    if isinstance(obj, dict):
        if default is _NO_DEFAULT:
            return obj[field]
        return obj.get(field, default)

    value = getattr(obj, field, _MISSING)
    if value is not _MISSING:
        return value

    # Kubernetes client models expose wire-format names such as matchLabels
    # through attribute_map, but the Python attribute is snake_case.
    attribute_map = getattr(obj, "attribute_map", None)
    if isinstance(attribute_map, dict):
        for attribute_name, wire_name in attribute_map.items():
            if wire_name != field:
                continue

            value = getattr(obj, attribute_name, _MISSING)
            if value is not _MISSING:
                return value
            break

    if default is _NO_DEFAULT:
        raise AttributeError(f"{type(obj).__name__!r} object has no attribute {field!r}")
    return default


def _dig(obj: Any, *path: str, default: Any = _MISSING) -> Any:
    current = obj
    for field in path:
        current = _read_field(current, field, _MISSING)
        if current is _MISSING:
            if default is _MISSING:
                raise KeyError(".".join(path))
            return default
    return current


def canonicalize_namespaces(namespaces: Iterable[str] | None) -> tuple[str, ...]:
    raw_namespaces = [str(namespace).strip() for namespace in (namespaces or []) if str(namespace).strip()]
    watched_namespaces = WatchedNamespaces(",".join(raw_namespaces))
    watched_namespaces.validate()
    return tuple(sorted(watched_namespaces.get_namespaces()))


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true"}


@dataclass(frozen=True)
class OperatorTopology:
    scope: str
    standalone: bool
    namespaces: tuple[str, ...]

    def __post_init__(self) -> None:
        namespaces = canonicalize_namespaces(self.namespaces)
        object.__setattr__(self, "namespaces", namespaces)

        if self.scope not in {"global", "scoped"}:
            raise OperatorTopologyError(
                f"Unsupported operator topology scope: {self.scope!r}"
            )

        if self.scope == "global" and namespaces:
            raise OperatorTopologyError(
                "Global operator topology must not declare explicit namespaces."
            )

        if self.scope == "scoped" and not namespaces:
            raise OperatorTopologyError(
                "Scoped operator topology must declare at least one namespace."
            )

    @property
    def is_global(self) -> bool:
        return self.scope == "global"

    def describe(self) -> str:
        if self.is_global:
            mode = "standalone" if self.standalone else "non-standalone"
            return f"global {mode}"

        mode = "standalone" if self.standalone else "non-standalone"
        watched = ", ".join(self.namespaces)
        return f"scoped {mode} watching [{watched}]"

    def overlaps(self, other: "OperatorTopology") -> bool:
        if self.is_global or other.is_global:
            return True
        return len(self.overlapping_namespaces(other)) > 0

    def overlapping_namespaces(self, other: "OperatorTopology") -> tuple[str, ...]:
        if self.is_global or other.is_global:
            return ()
        return tuple(sorted(set(self.namespaces).intersection(other.namespaces)))

    @classmethod
    def from_env(
        cls,
        namespaces: str | None,
        standalone: Any,
    ) -> "OperatorTopology":
        watched_namespaces = WatchedNamespaces(namespaces)
        watched_namespaces.validate()
        explicit_namespaces = canonicalize_namespaces(watched_namespaces.get_namespaces())
        scope = "global" if len(explicit_namespaces) == 0 else "scoped"
        return cls(
            scope=scope,
            standalone=_parse_bool(standalone),
            namespaces=explicit_namespaces,
        )


@dataclass(frozen=True)
class ResolvedOperatorDeploymentTopology:
    namespace: str
    name: str
    topology: OperatorTopology
    source: str = "env"

    @property
    def ref(self) -> str:
        return f"{self.namespace}/{self.name}"


def deployment_ref(deployment: Any) -> str:
    return f"{deployment_namespace(deployment)}/{deployment_name(deployment)}"


def deployment_name(deployment: Any) -> str:
    return str(_dig(deployment, "metadata", "name", default="") or "")


def deployment_namespace(deployment: Any) -> str:
    return str(_dig(deployment, "metadata", "namespace", default="") or "")


def deployment_labels(deployment: Any) -> dict[str, Any]:
    labels = _dig(deployment, "metadata", "labels", default=None)
    return labels if isinstance(labels, dict) else {}


def deployment_generation(deployment: Any) -> int | None:
    generation = _dig(deployment, "metadata", "generation", default=None)
    if generation in (None, ""):
        return None
    if isinstance(generation, int):
        return generation
    try:
        return int(str(generation))
    except (TypeError, ValueError):
        return None


def operator_container_exists(deployment: Any) -> bool:
    containers = _dig(deployment, "spec", "template", "spec", "containers", default=None)
    if not isinstance(containers, list):
        return False

    for container in containers:
        if _read_field(container, "name", "") == OPERATOR_CONTAINER_NAME:
            return True
    return False


def operator_container_env_map(deployment: Any) -> dict[str, str]:
    env_map: dict[str, str] = {}
    containers = _dig(deployment, "spec", "template", "spec", "containers", default=None)
    if not isinstance(containers, list):
        return env_map

    for container in containers:
        if _read_field(container, "name", "") != OPERATOR_CONTAINER_NAME:
            continue

        env_entries = _read_field(container, "env", []) or []
        for env_entry in env_entries:
            env_name = _read_field(env_entry, "name", "")
            if env_name:
                env_map[str(env_name)] = str(_read_field(env_entry, "value", "") or "")
        break

    return env_map


def is_legacy_global_operator_deployment(deployment: Any) -> bool:
    if not operator_container_exists(deployment):
        return False

    env_map = operator_container_env_map(deployment)
    if "OPERATOR_NAMESPACES" in env_map or "OPERATOR_STANDALONE" in env_map:
        return False

    selector_labels = _dig(
        deployment,
        "spec",
        "selector",
        "matchLabels",
        default=None,
    )
    if not isinstance(selector_labels, dict):
        return False

    template_labels = _dig(
        deployment,
        "spec",
        "template",
        "metadata",
        "labels",
        default=None,
    )
    if not isinstance(template_labels, dict):
        return False

    return (
        deployment_name(deployment) == DEFAULT_DEPLOYMENT_NAME
        and selector_labels == {"name": DEFAULT_DEPLOYMENT_NAME}
        and template_labels == {"name": DEFAULT_DEPLOYMENT_NAME}
    )


def is_recognized_operator_deployment(deployment: Any) -> bool:
    if not operator_container_exists(deployment):
        return False

    env_map = operator_container_env_map(deployment)
    if "OPERATOR_NAMESPACES" in env_map or "OPERATOR_STANDALONE" in env_map:
        return True

    labels = deployment_labels(deployment)
    has_managed_operator_labels = (
        labels.get("app.kubernetes.io/name") == DEFAULT_DEPLOYMENT_NAME
        and labels.get("app.kubernetes.io/component") == "controller"
    )
    return has_managed_operator_labels or is_legacy_global_operator_deployment(deployment)


def deployment_generation_source(deployment: Any) -> str:
    generation = deployment_generation(deployment)
    if generation == 1:
        return "env-install"
    return "env-upgrade"


def resolve_operator_deployment_topology(
    deployment: Any,
) -> ResolvedOperatorDeploymentTopology | None:
    if not is_recognized_operator_deployment(deployment):
        return None

    namespace = deployment_namespace(deployment)
    name = deployment_name(deployment)
    ref = f"{namespace}/{name}"
    env_map = operator_container_env_map(deployment)
    has_namespaces = "OPERATOR_NAMESPACES" in env_map
    has_standalone = "OPERATOR_STANDALONE" in env_map
    if has_namespaces or has_standalone:
        if not (has_namespaces and has_standalone):
            raise OperatorTopologyError(
                f"Recognized operator deployment {ref} has incomplete topology env configuration."
            )

        topology = OperatorTopology.from_env(
            env_map.get("OPERATOR_NAMESPACES", ""),
            env_map.get("OPERATOR_STANDALONE", ""),
        )
        return ResolvedOperatorDeploymentTopology(
            namespace=namespace,
            name=name,
            topology=topology,
            source=deployment_generation_source(deployment),
        )

    if is_legacy_global_operator_deployment(deployment):
        return ResolvedOperatorDeploymentTopology(
            namespace=namespace,
            name=name,
            topology=OperatorTopology(
                scope="global",
                standalone=False,
                namespaces=(),
            ),
            source="legacy-global",
        )

    raise OperatorTopologyError(
        f"Recognized operator deployment {ref} has no topology env configuration and does not match a supported legacy global topology."
    )
