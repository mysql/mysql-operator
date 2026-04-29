# Copyright (c) 2020, 2026 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import time
import unittest
import os
import re
import json
import pathlib
import uuid
from dataclasses import dataclass, field
from utils import tutil
from utils import kutil
import logging
import yaml
import copy
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS
from setup.config import g_ts_cfg
from setup.image_digests import (
    HistoricalImageDigestCoverageError,
    KnownMissingImageArtifactError,
    canonicalize_image_name,
    lookup_historical_image_digest,
)
from datetime import datetime, timezone
from typing import Callable, Optional

from utils.helmutil import (
    HelmClusterInstallOptions,
    HelmOperatorInstallOptions,
    get_previous_operator_chart_release,
    install_cluster_with_helm,
    install_with_helm,
    upgrade_cluster_with_helm,
    upgrade_with_helm,
    resolve_install_with_helm_options,
    uninstall_with_helm,
    wait_for_namespace_empty,
)

# ==================================================================================================
# Helm Chart _helpers.tpl Replicas (Free Functions)
# ==================================================================================================

def get_prefix_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.prefixName'"""
    if operator_name == "mysql-operator":
        return "mysql"
    return f"{operator_ns}-{operator_name}"

def get_cluster_peering_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.clusterPeeringName'"""
    if operator_name == "mysql-operator":
        return "mysql-operator"
    return f"{operator_ns}-{operator_name}-operator"

def get_namespaced_peering_name(operator_name: str) -> str:
    """Matches helm template 'mysql-operator.peeringName'"""
    return operator_name

def get_env_var_peering_name(operator_ns: str, operator_name: str, watch_namespaces: str) -> str:
    """Matches helm template 'mysql-operator.envVarPeeringName'"""
    if normalize_watch_namespaces(watch_namespaces):
        return get_namespaced_peering_name(operator_name)
    return get_cluster_peering_name(operator_ns, operator_name)

def get_operator_role_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.operatorRoleName'"""
    return f"{get_prefix_name(operator_ns, operator_name)}-operator"

def get_sidecar_role_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.sidecarRoleName'"""
    return f"{get_prefix_name(operator_ns, operator_name)}-sidecar"

def get_switchover_role_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.switchoverRoleName'"""
    return f"{get_prefix_name(operator_ns, operator_name)}-switchover"

LEGACY_SWITCHOVER_SERVICE_ACCOUNT_NAME = "mysql-switchover-sa"
LEGACY_SWITCHOVER_ROLE_BINDING_NAME = "mysql-switchover-rb"
LEGACY_SWITCHOVER_RBAC_INTRODUCED_VERSION = "9.3.0"
DEFAULT_OPERATOR_CLUSTERROLE_NAMES = (
    "mysql-operator",
    "mysql-sidecar",
    "mysql-switchover",
)
DEFAULT_OPERATOR_DEPLOY_MANIFEST = (
    pathlib.Path(__file__).resolve().parents[4] / "deploy" / "deploy-operator.yaml"
)
HELM_FIELD_MANAGER = "helm"
# TODO - problem with 9.6.0 - clusterset is not thread safe
CLUSTERSET_CREATION_GAP_SECONDS = 30
INVALID_MYSQL_UPGRADE_ERROR_CODES = (
    "MY-014060",
)
FATAL_DD_UPGRADE_ERROR_CODES = (
    "MY-011014",
    "MY-010020",
    "MY-011015",
    "MY-010334",
    "MY-012526",
    "MY-013178",
    "MY-013380",
)
FATAL_DD_UPGRADE_ERROR_FRAGMENTS = (
    "Found partially upgraded DD. Aborting upgrade and deleting all DD tables. Start the upgrade process again.",
    "Data Dictionary initialization failed.",
    "Failed to upgrade the data dictionary.",
    "Failed to initialize DD Storage Engine.",
    "Failed to upgrade server.",
    "Upgrade is not supported after a crash or shutdown with innodb_fast_shutdown = 2.",
)
RAW_DEPLOY_MANIFEST_FILES = (
    "deploy-crds.yaml",
    "deploy-operator.yaml",
)
RAW_MANIFEST_BRIDGE_RELEASE = "9.6.0-2.2.7"
HISTORICAL_IMAGE_DIGEST_MAX_MYSQL_VERSION = "9.5.0"

OPERATOR_IMAGE_DIGEST_KEYS = frozenset(
    {
        "community-operator",
        "enterprise-operator",
    }
)
SERVER_IMAGE_DIGEST_KEYS = frozenset(
    {
        "community-server",
        "enterprise-server",
    }
)
ROUTER_IMAGE_DIGEST_KEYS = frozenset(
    {
        "community-router",
        "enterprise-router",
    }
)
SERVER_ROUTER_IMAGE_DIGEST_KEYS = (
    SERVER_IMAGE_DIGEST_KEYS | ROUTER_IMAGE_DIGEST_KEYS
)


def _dedupe_paths(paths: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for path in paths:
        if not path or path in seen:
            continue
        seen.add(path)
        deduped.append(path)
    return deduped


def get_raw_deploy_dir_candidates_for_release(release: str) -> list[str]:
    release = str(release)
    current_release = str(g_ts_cfg.operator_version_tag)
    candidates = []

    if release == current_release:
        deploy_path = g_ts_cfg.get_deploy_path()
        if deploy_path:
            candidates.append(deploy_path)

    historic_path = g_ts_cfg.get_deploy_historic_path()
    if historic_path:
        if release == current_release:
            candidates.append(os.path.join(historic_path, "deploy"))
        candidates.append(os.path.join(historic_path, release))
        candidates.append(os.path.join(historic_path, release, "deploy"))

    return _dedupe_paths(candidates)


def get_raw_deploy_dir_for_release(release: str) -> str:
    candidates = get_raw_deploy_dir_candidates_for_release(release)
    if not candidates:
        raise ValueError(
            "Raw deploy manifest paths are not configured. Set "
            "OPERATOR_TEST_DEPLOY_PATH/--deploy-path for the current release "
            "and OPERATOR_TEST_DEPLOY_HISTORIC_PATH/--deploy-historic-path "
            "for historic releases."
        )

    checked = []
    for candidate in candidates:
        missing = [
            filename
            for filename in RAW_DEPLOY_MANIFEST_FILES
            if not os.path.isfile(os.path.join(candidate, filename))
        ]
        if not missing:
            return candidate
        checked.append(f"{candidate} (missing: {', '.join(missing)})")

    raise FileNotFoundError(
        f"No complete raw deploy manifest directory found for release {release}. "
        f"Checked: {'; '.join(checked)}"
    )


def get_raw_deploy_manifest_path(release: str, filename: str) -> str:
    if filename not in RAW_DEPLOY_MANIFEST_FILES:
        raise ValueError(f"Unknown raw deploy manifest file {filename!r}")
    return os.path.join(get_raw_deploy_dir_for_release(release), filename)


def _set_container_env_value(container: dict, name: str, value: str) -> None:
    envs = container.setdefault("env", [])
    for env in envs:
        if env.get("name") == name:
            env["value"] = value
            env.pop("valueFrom", None)
            return
    envs.append({"name": name, "value": value})


def _ensure_rule_value(rule: dict, key: str, value: str) -> None:
    values = rule.setdefault(key, [])
    if value not in values:
        values.append(value)


def _patch_raw_operator_clusterrole_for_test_environment(doc: dict) -> None:
    if (
        doc.get("kind") != "ClusterRole"
        or doc.get("metadata", {}).get("name") != "mysql-operator"
    ):
        return

    for rule in doc.setdefault("rules", []):
        if "apps" not in rule.get("apiGroups", []):
            continue
        if "deployments" not in rule.get("resources", []):
            continue
        _ensure_rule_value(rule, "verbs", "list")
        return

    doc["rules"].append(
        {
            "apiGroups": ["apps"],
            "resources": ["deployments"],
            "verbs": ["list"],
        }
    )


def _patch_raw_operator_manifest_for_test_environment(
    docs: list[dict],
    release: str,
) -> None:
    for doc in docs:
        if not isinstance(doc, dict):
            continue

        _patch_raw_operator_clusterrole_for_test_environment(doc)

        if doc.get("kind") == "Namespace":
            custom_labels = g_ts_cfg.get_custom_operator_ns_labels()
            if custom_labels:
                doc.setdefault("metadata", {}).setdefault("labels", {}).update(
                    custom_labels
                )
            continue

        if (
            doc.get("kind") != "Deployment"
            or doc.get("metadata", {}).get("name") != "mysql-operator"
        ):
            continue

        containers = (
            doc.get("spec", {})
            .get("template", {})
            .get("spec", {})
            .get("containers", [])
        )
        operator_container = get_named_container(containers, "mysql-operator")
        if operator_container is None:
            raise ValueError(
                f"Raw operator manifest for release {release} has no "
                "mysql-operator container"
            )

        operator_container["image"] = g_ts_cfg.get_operator_image(release)
        operator_container["imagePullPolicy"] = g_ts_cfg.operator_pull_policy
        _set_container_env_value(
            operator_container,
            "MYSQL_OPERATOR_DEFAULT_REPOSITORY",
            g_ts_cfg.get_image_registry_repository(),
        )
        _set_container_env_value(
            operator_container,
            "MYSQL_OPERATOR_IMAGE_PULL_POLICY",
            g_ts_cfg.operator_pull_policy,
        )


def load_raw_deploy_manifest_docs(release: str, filename: str) -> list[dict]:
    manifest_path = get_raw_deploy_manifest_path(release, filename)
    with open(manifest_path, encoding="utf8") as manifest_file:
        return [
            doc for doc in yaml.safe_load_all(manifest_file)
            if doc is not None
        ]


def render_raw_deploy_manifest_for_test_environment(
    release: str,
    filename: str,
) -> str:
    docs = load_raw_deploy_manifest_docs(release, filename)
    if filename == "deploy-operator.yaml":
        _patch_raw_operator_manifest_for_test_environment(docs, release)
    return yaml.safe_dump_all(docs, sort_keys=False)


def get_raw_manifest_upgrade_release_chain() -> list[str]:
    chain = [
        g_ts_cfg.operator_current_lts_version_tag,
        RAW_MANIFEST_BRIDGE_RELEASE,
        g_ts_cfg.operator_version_tag,
    ]
    deduped = []
    for release in chain:
        if release and release not in deduped:
            deduped.append(release)
    return deduped


def get_cluster_switchover_service_account_name(cluster_name: str) -> str:
    return f"{cluster_name}-switchover-sa"

def get_cluster_switchover_role_binding_name(cluster_name: str) -> str:
    return f"{cluster_name}-switchover-rb"

def get_operator_rolebinding_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.operatorRoleBindingName'"""
    return f"{get_prefix_name(operator_ns, operator_name)}-operator-rolebinding"

def get_service_account_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.operatorSAName'"""
    return f"{get_prefix_name(operator_ns, operator_name)}-operator-sa"

def get_global_instance_name(operator_ns: str, operator_name: str) -> str:
    """Matches helm template 'mysql-operator.globalInstanceName'"""
    return f"{get_prefix_name(operator_ns, operator_name)}-operator"

def get_service_selector_labels(operator_ns: str, operator_name: str) -> dict:
    """Matches helm template 'mysql-operator.serviceSelectorLabels'"""
    return {
        "app.kubernetes.io/name": "mysql-operator",
        "app.kubernetes.io/instance": get_global_instance_name(operator_ns, operator_name),
        "app.kubernetes.io/component": "controller",
    }

def get_global_labels(release_name: str, operator_ns: str, operator_name: str) -> dict:
    """Matches helm template 'mysql-operator.globalLabels'"""
    return get_service_selector_labels(operator_ns, operator_name)

def get_legacy_deployment_selector_labels() -> dict:
    """Matches the legacy Helm deployment selector preserved on upgrade."""
    return {
        "name": "mysql-operator",
    }


def get_deployment_selector_labels(operator_ns: str, operator_name: str) -> dict:
    """Matches helm template 'mysql-operator.freshDeploymentSelectorLabels'"""
    return {
        "name": operator_name,
        **get_service_selector_labels(operator_ns, operator_name),
    }


def get_selector_labels(release_name: str, operator_ns: str, operator_name: str) -> dict:
    """Matches helm template 'mysql-operator.selectorLabels'"""
    return {
        **get_deployment_selector_labels(operator_ns, operator_name),
        **get_service_selector_labels(operator_ns, operator_name),
    }

def get_labels(release_name: str, operator_ns: str, operator_name: str, app_version: str = None, version: str = None) -> dict:
    """Matches helm template 'mysql-operator.labels'"""
    labels = get_selector_labels(release_name, operator_ns, operator_name)
    if version is not None:
        labels["version"] = str(version)
    if app_version is not None:
        labels["app.kubernetes.io/version"] = str(app_version)
    return labels


K8S_NAME_LABEL_LIMIT = 63
GLOBAL_INSTANCE_NAME_SUFFIX = "-operator"
TOPOLOGY_ANNOTATION_KEY = "mysql.oracle.com/operator-topology"


def get_max_custom_deployment_name_length(operator_ns: str) -> int:
    return max(
        0,
        min(
            K8S_NAME_LABEL_LIMIT,
            K8S_NAME_LABEL_LIMIT
            - (len(operator_ns) + 1 + len(GLOBAL_INSTANCE_NAME_SUFFIX)),
        ),
    )

# ==================================================================================================
# Regex Pattern Generators for Deletion / Listing
# ==================================================================================================

def get_crole_names_pattern(operator_ns: str, operator_name: str) -> str:
    return f"{get_prefix_name(operator_ns, operator_name)}-.*"

def get_crole_binding_pattern(operator_ns: str, operator_name: str) -> str:
    return f"{get_operator_role_name(operator_ns, operator_name)}.*"

def get_ckopf_peerings_pattern(operator_ns: str, operator_name: str) -> str:
    return get_cluster_peering_name(operator_ns, operator_name)

def get_sa_names_pattern(operator_ns: str, operator_name: str) -> str:
    return get_service_account_name(operator_ns, operator_name)

def get_role_names_pattern(operator_ns: str, operator_name: str) -> str:
    return f"{get_prefix_name(operator_ns, operator_name)}-.*"

def get_role_binding_pattern(operator_ns: str, operator_name: str) -> str:
    return f"{get_operator_role_name(operator_ns, operator_name)}.*"

def get_kopf_peerings_pattern(operator_name: str) -> str:
    return f"{get_namespaced_peering_name(operator_name)}.*"

# ==================================================================================================


def get_helm_release_target(helm_options) -> tuple[str, str, str]:
    return (
        helm_options.namespace,
        helm_options.release_name,
        helm_options.kube_context or "",
    )


def get_default_operator_deploy_manifest_path() -> pathlib.Path:
    try:
        return pathlib.Path(
            get_raw_deploy_manifest_path(
                g_ts_cfg.operator_version_tag,
                "deploy-operator.yaml",
            )
        )
    except (AttributeError, FileNotFoundError, ValueError):
        if DEFAULT_OPERATOR_DEPLOY_MANIFEST.is_file():
            return DEFAULT_OPERATOR_DEPLOY_MANIFEST
        raise


def _load_default_operator_clusterrole_rules(
    manifest_path: Optional[pathlib.Path] = None,
) -> dict[str, list[dict]]:
    rules_by_name = {}
    manifest_path = manifest_path or get_default_operator_deploy_manifest_path()

    with manifest_path.open(encoding="utf-8") as handle:
        for doc in yaml.safe_load_all(handle):
            if not doc or doc.get("kind") != "ClusterRole":
                continue

            name = doc.get("metadata", {}).get("name")
            if name in DEFAULT_OPERATOR_CLUSTERROLE_NAMES:
                rules_by_name[name] = doc["rules"]

    missing = set(DEFAULT_OPERATOR_CLUSTERROLE_NAMES) - set(rules_by_name)
    if missing:
        raise AssertionError(
            f"Missing default operator ClusterRoles in {manifest_path}: "
            f"{sorted(missing)}"
        )

    return rules_by_name


def _build_default_operator_clusterrole_manifest(
    name: str,
    rules: list[dict],
    *,
    helm_release_name: Optional[str] = None,
    helm_release_namespace: Optional[str] = None,
) -> dict:
    metadata = {"name": name}
    if helm_release_name and helm_release_namespace:
        metadata["labels"] = {"app.kubernetes.io/managed-by": "Helm"}
        metadata["annotations"] = {
            "meta.helm.sh/release-name": helm_release_name,
            "meta.helm.sh/release-namespace": helm_release_namespace,
        }
    return {
        "apiVersion": "rbac.authorization.k8s.io/v1",
        "kind": "ClusterRole",
        "metadata": metadata,
        "rules": rules,
    }


def _apply_default_operator_clusterrole(
    name: str,
    rules: list[dict],
    *,
    helm_release_name: Optional[str] = None,
    helm_release_namespace: Optional[str] = None,
) -> None:
    kutil.apply(
        None,
        yaml.safe_dump(
            _build_default_operator_clusterrole_manifest(
                name,
                rules,
                helm_release_name=helm_release_name,
                helm_release_namespace=helm_release_namespace,
            ),
            sort_keys=False,
        ),
        field_manager=HELM_FIELD_MANAGER,
        server_side=True,
        force_conflicts=True,
    )


def _clusterrole_rule_key(
    rule: dict,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        tuple(rule.get("apiGroups") or []),
        tuple(rule.get("resources") or []),
        tuple(rule.get("resourceNames") or []),
        tuple(rule.get("nonResourceURLs") or []),
    )


def _merge_missing_clusterrole_rules(
    existing_rules: list[dict],
    default_rules: list[dict],
) -> tuple[list[dict], bool]:
    merged_rules = copy.deepcopy(existing_rules)
    rule_indexes = {
        _clusterrole_rule_key(rule): index
        for index, rule in enumerate(merged_rules)
    }
    changed = False

    for default_rule in default_rules:
        index = rule_indexes.get(_clusterrole_rule_key(default_rule))
        if index is None:
            merged_rules.append(copy.deepcopy(default_rule))
            changed = True
            continue

        existing_verbs = merged_rules[index].setdefault("verbs", [])
        for verb in default_rule.get("verbs") or []:
            if verb not in existing_verbs:
                existing_verbs.append(verb)
                changed = True

    return merged_rules, changed


def _sync_existing_operator_clusterrole_missing_rules(
    name: str,
    default_rules: list[dict],
) -> None:
    clusterrole = kutil.get(None, "clusterrole", name, check=False)
    if clusterrole is None:
        return

    merged_rules, changed = _merge_missing_clusterrole_rules(
        clusterrole.get("rules") or [],
        default_rules,
    )
    if not changed:
        return

    _apply_default_operator_clusterrole(
        name,
        merged_rules,
    )


def _sync_default_operator_clusterroles_from_manifest(
    *,
    create_missing_only: bool = False,
    helm_release_name: Optional[str] = None,
    helm_release_namespace: Optional[str] = None,
) -> None:
    for name, rules in _load_default_operator_clusterrole_rules().items():
        if create_missing_only:
            if kutil.get(None, "clusterrole", name, check=False) is not None:
                _sync_existing_operator_clusterrole_missing_rules(name, rules)
                continue
            _apply_default_operator_clusterrole(
                name,
                rules,
                helm_release_name=helm_release_name,
                helm_release_namespace=helm_release_namespace,
            )
            continue

        _apply_default_operator_clusterrole(
            name,
            rules,
            helm_release_name=helm_release_name,
            helm_release_namespace=helm_release_namespace,
        )


KubectlManifestObject = tuple[str, str, str]
KubectlInstallKey = tuple[str, str, str]

_KUBECTL_CLUSTER_SCOPED_KINDS = {
    "ClusterKopfPeering",
    "ClusterRole",
    "ClusterRoleBinding",
}
_KUBECTL_OPERATOR_INSTALLS: dict[KubectlInstallKey, "KubectlOperatorInstallResult"] = {}


@dataclass
class KubectlOperatorInstallResult:
    install_key: KubectlInstallKey
    operator_ns: str
    operator_deploy_name: str
    artifacts: Optional[dict] = None
    manifest_objects: list[KubectlManifestObject] = field(default_factory=list)


@dataclass(frozen=True)
class _HelmClusterInstallTracker:
    namespace: str
    cluster_name: str
    options: HelmClusterInstallOptions


@dataclass(frozen=True)
class _MysqlUpgradeAbortCheck:
    namespace: str
    cluster_names: tuple[str, ...]
    server_instances: int
    previous_release: str
    target_release: str


def _current_kube_context() -> str:
    return getattr(g_ts_cfg, "k8s_context", None) or ""


def get_kubectl_install_target(
    operator_ns: str,
    operator_deploy_name: str,
    *,
    kube_context: Optional[str] = None,
) -> KubectlInstallKey:
    return (
        operator_ns,
        operator_deploy_name,
        kube_context or _current_kube_context(),
    )


def _copy_kubectl_install_result(
    install_result: KubectlOperatorInstallResult,
) -> KubectlOperatorInstallResult:
    return KubectlOperatorInstallResult(
        install_key=tuple(install_result.install_key),
        operator_ns=install_result.operator_ns,
        operator_deploy_name=install_result.operator_deploy_name,
        artifacts=copy.deepcopy(install_result.artifacts),
        manifest_objects=list(install_result.manifest_objects),
    )


def _set_tracked_kubectl_install_result(
    install_result: KubectlOperatorInstallResult,
) -> KubectlOperatorInstallResult:
    tracked_install_result = _copy_kubectl_install_result(install_result)
    _KUBECTL_OPERATOR_INSTALLS[tracked_install_result.install_key] = (
        tracked_install_result
    )
    return _copy_kubectl_install_result(tracked_install_result)


def _get_tracked_kubectl_install_result(
    install_key: KubectlInstallKey,
) -> Optional[KubectlOperatorInstallResult]:
    tracked_install_result = _KUBECTL_OPERATOR_INSTALLS.get(install_key)
    if tracked_install_result is None:
        return None
    return _copy_kubectl_install_result(tracked_install_result)


def _clear_tracked_kubectl_install_result(
    install_key: KubectlInstallKey,
) -> None:
    _KUBECTL_OPERATOR_INSTALLS.pop(install_key, None)


def _manifest_object_from_manifest(
    manifest: Optional[dict],
    *,
    default_namespace: Optional[str] = None,
) -> Optional[KubectlManifestObject]:
    if not isinstance(manifest, dict):
        return None

    kind = str(manifest.get("kind", "")).strip()
    metadata = manifest.get("metadata")
    if not kind or not isinstance(metadata, dict):
        return None

    name = str(metadata.get("name", "")).strip()
    if not name:
        return None

    namespace = str(metadata.get("namespace", "")).strip()
    if (
        not namespace
        and kind not in _KUBECTL_CLUSTER_SCOPED_KINDS
        and default_namespace
    ):
        namespace = default_namespace

    return (kind, name, namespace)


def _append_manifest_object(
    manifest_objects: list[KubectlManifestObject],
    manifest_object: Optional[KubectlManifestObject],
) -> None:
    if manifest_object is None or manifest_object in manifest_objects:
        return
    manifest_objects.append(manifest_object)


def _get_kubectl_manifest_objects_from_artifacts(
    artifacts: Optional[dict],
    *,
    operator_ns: Optional[str] = None,
) -> list[KubectlManifestObject]:
    manifest_objects: list[KubectlManifestObject] = []
    if not isinstance(artifacts, dict):
        return manifest_objects

    for manifest_type in (
        "clusterroles",
        "clusterrolebindings",
        "clusterkopfpeerings",
        "secrets",
        "serviceaccounts",
        "roles",
        "rolebindings",
        "kopfpeerings",
    ):
        for manifest in (artifacts.get(manifest_type) or {}).values():
            _append_manifest_object(
                manifest_objects,
                _manifest_object_from_manifest(
                    manifest,
                    default_namespace=operator_ns,
                ),
            )

    _append_manifest_object(
        manifest_objects,
        _manifest_object_from_manifest(
            artifacts.get("deployment"),
            default_namespace=operator_ns,
        ),
    )
    return manifest_objects


def _get_operator_deployment_name_from_artifacts(artifacts: Optional[dict]) -> str:
    if not isinstance(artifacts, dict):
        return ""
    deployment = artifacts.get("deployment")
    if not isinstance(deployment, dict):
        return ""
    metadata = deployment.get("metadata")
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("name", "")).strip()


def _build_kubectl_install_result(
    *,
    operator_ns: str,
    operator_deploy_name: str,
    artifacts: Optional[dict] = None,
    manifest_objects: Optional[list[KubectlManifestObject]] = None,
    kube_context: Optional[str] = None,
) -> KubectlOperatorInstallResult:
    return KubectlOperatorInstallResult(
        install_key=get_kubectl_install_target(
            operator_ns,
            operator_deploy_name,
            kube_context=kube_context,
        ),
        operator_ns=operator_ns,
        operator_deploy_name=operator_deploy_name,
        artifacts=copy.deepcopy(artifacts),
        manifest_objects=list(manifest_objects or []),
    )


def _track_kubectl_install_from_artifacts(
    *,
    operator_ns: str,
    operator_deploy_name: str,
    artifacts: Optional[dict],
    kube_context: Optional[str] = None,
) -> KubectlOperatorInstallResult:
    install_result = _build_kubectl_install_result(
        operator_ns=operator_ns,
        operator_deploy_name=operator_deploy_name,
        artifacts=artifacts,
        manifest_objects=_get_kubectl_manifest_objects_from_artifacts(
            artifacts,
            operator_ns=operator_ns,
        ),
        kube_context=kube_context,
    )
    return _set_tracked_kubectl_install_result(install_result)


def _delete_kubectl_manifest_objects(
    manifest_objects: list[KubectlManifestObject],
) -> None:
    cleanup_error = None

    for object_type, object_name, object_namespace in reversed(manifest_objects):
        namespace = object_namespace or None
        if namespace is None and object_type not in _KUBECTL_CLUSTER_SCOPED_KINDS:
            logging.getLogger(__name__).warning(
                "Skipping kubectl cleanup for manifest object %s/%s because no namespace was captured",
                object_type,
                object_name,
            )
            continue

        try:
            kutil.delete(namespace, object_type.lower(), object_name, timeout=60)
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc

    if cleanup_error is not None:
        raise cleanup_error


def _resolve_kubectl_install_result(
    *,
    operator_ns: str,
    operator_deploy_name: str,
    artifacts: Optional[dict] = None,
    install_result: Optional[KubectlOperatorInstallResult] = None,
    install_key: Optional[KubectlInstallKey] = None,
    kube_context: Optional[str] = None,
) -> KubectlOperatorInstallResult:
    if install_result is not None:
        return _copy_kubectl_install_result(install_result)

    effective_install_key = install_key or get_kubectl_install_target(
        operator_ns,
        operator_deploy_name,
        kube_context=kube_context,
    )
    tracked_install_result = _get_tracked_kubectl_install_result(
        effective_install_key
    )
    if tracked_install_result is not None:
        return tracked_install_result

    if artifacts is not None:
        return _build_kubectl_install_result(
            operator_ns=operator_ns,
            operator_deploy_name=operator_deploy_name,
            artifacts=artifacts,
            manifest_objects=_get_kubectl_manifest_objects_from_artifacts(
                artifacts,
                operator_ns=operator_ns,
            ),
            kube_context=effective_install_key[2],
        )

    raise RuntimeError(
        "No tracked kubectl operator install state for "
        f"{operator_ns}/{operator_deploy_name}"
    )


def get_referenced_image_pull_secret_names(
    deployment: Optional[dict],
    serviceaccounts: Optional[dict],
) -> list[str]:
    names = []
    seen = set()

    def add_secret_refs(secret_refs) -> None:
        for secret_ref in secret_refs or []:
            if not isinstance(secret_ref, dict):
                continue
            name = str(secret_ref.get("name", "")).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            names.append(name)

    pod_spec = (
        deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        if deployment
        else {}
    )
    add_secret_refs(pod_spec.get("imagePullSecrets"))

    for service_account in (serviceaccounts or {}).values():
        add_secret_refs(service_account.get("imagePullSecrets"))

    return names


def remove_operator(operator_ns,
                    operator_deploy_name,
                    crole_names_pattern=None, crole_binding_pattern=None, ckopf_peerings_pattern=None,
                    sa_names_pattern=None,
                    role_names_pattern=None, role_binding_pattern=None, kopf_peerings_pattern=None,
                    *,
                    artifacts: Optional[dict] = None,
                    install_result: Optional[KubectlOperatorInstallResult] = None,
                    install_key: Optional[KubectlInstallKey] = None,
                    kube_context: Optional[str] = None,
                    return_manifests: bool = True,
                    delete_namespace: bool = True,
                    check_namespace_empty: bool = False,
                    check_namespace_empty_timeout: int = 60) -> Optional[dict]:
    resolved_install_result = _resolve_kubectl_install_result(
        operator_ns=operator_ns,
        operator_deploy_name=operator_deploy_name,
        artifacts=artifacts,
        install_result=install_result,
        install_key=install_key,
        kube_context=kube_context,
    )
    artifacts = (
        copy.deepcopy(resolved_install_result.artifacts)
        if return_manifests and resolved_install_result.artifacts is not None
        else None
    )

    cleanup_error = None
    try:
        print_operator_traceback_context(operator_ns, operator_deploy_name)
    except Exception as exc:
        print(
            f"Failed to scan operator logs for Traceback before removing "
            f"{operator_ns}/{operator_deploy_name}: {exc}"
        )

    try:
        _delete_kubectl_manifest_objects(resolved_install_result.manifest_objects)
    except Exception as exc:
        cleanup_error = exc

    namespace_ready_for_deletion = True
    if operator_ns in kutil.ls_ns_ex() and check_namespace_empty:
        try:
            wait_for_namespace_empty(
                operator_ns,
                timeout=check_namespace_empty_timeout,
            )
        except Exception as exc:
            namespace_ready_for_deletion = False
            if cleanup_error is None:
                cleanup_error = exc

    if operator_ns in kutil.ls_ns_ex() and delete_namespace and namespace_ready_for_deletion:
        try:
            kutil.delete_ns(operator_ns)
        except Exception as exc:
            if cleanup_error is None:
                cleanup_error = exc

    if cleanup_error is None:
        _clear_tracked_kubectl_install_result(resolved_install_result.install_key)
    else:
        raise cleanup_error

    return artifacts


def install_operator(artifacts, operator_ns, check_ns:bool =True) -> KubectlOperatorInstallResult:
    operator_deploy_name = _get_operator_deployment_name_from_artifacts(artifacts)
    if not operator_deploy_name:
        raise ValueError(
            "operator deployment name could not be resolved from artifacts"
        )

    if check_ns and operator_ns in kutil.ls_ns_ex():
        raise Exception(f"Namespace {operator_ns} already exists")

    install_result = _build_kubectl_install_result(
        operator_ns=operator_ns,
        operator_deploy_name=operator_deploy_name,
        artifacts=artifacts,
    )

    try:
        for manifest_type in ["clusterroles", "clusterrolebindings", "clusterkopfpeerings"]:
            if manifest_type in artifacts:
                for manifest in artifacts[manifest_type].values():
                    kutil.apply(None, y=yaml.dump(manifest, default_flow_style=False, sort_keys=False))
                    _append_manifest_object(
                        install_result.manifest_objects,
                        _manifest_object_from_manifest(
                            manifest,
                            default_namespace=operator_ns,
                        ),
                    )

        kutil.create_ns(operator_ns, artifacts.get("namespace_labels", {}))

        for manifest_type in ["secrets", "serviceaccounts", "roles", "rolebindings", "kopfpeerings"]:
            if manifest_type in artifacts:
                for manifest in artifacts[manifest_type].values():
                    kutil.apply(None, y=yaml.dump(manifest, default_flow_style=False, sort_keys=False))
                    _append_manifest_object(
                        install_result.manifest_objects,
                        _manifest_object_from_manifest(
                            manifest,
                            default_namespace=operator_ns,
                        ),
                    )

        kutil.apply(ns=operator_ns, y=yaml.dump(artifacts["deployment"], default_flow_style=False, sort_keys=False))
        _append_manifest_object(
            install_result.manifest_objects,
            _manifest_object_from_manifest(
                artifacts["deployment"],
                default_namespace=operator_ns,
            ),
        )
    except Exception as exc:
        tracked_install_result = _set_tracked_kubectl_install_result(
            install_result
        )
        exc.kubectl_install_result = tracked_install_result
        raise

    return _set_tracked_kubectl_install_result(install_result)


def restore_operator(artifacts, operator_ns) -> KubectlOperatorInstallResult:
    # Restore paths run immediately after Helm/kubectl teardown and should
    # tolerate a namespace that still exists but is otherwise ready to reuse.
    return install_operator(artifacts, operator_ns, check_ns=False)


def strip_k8s_metadata(manifest):
    stripped = copy.deepcopy(manifest)

    if "metadata" in stripped:
        stripped["metadata"].pop("creationTimestamp", None)
        stripped["metadata"].pop("resourceVersion", None)
        stripped["metadata"].pop("uid", None)
        stripped["metadata"].pop("ownerReferences", None)
        stripped["metadata"].pop("generation", None)
        if "annotations" in stripped["metadata"]:
            stripped["metadata"]["annotations"].pop("kubectl.kubernetes.io/last-applied-configuration", None)
            if not stripped["metadata"]["annotations"]:
                stripped["metadata"].pop("annotations", None)
    stripped.pop("status", None)

    return stripped


def get_operator_artifacts(operator_ns, operator_deploy_name, crole_names_pattern, crole_binding_pattern, ckopf_peerings_pattern,
                           sa_names_pattern,
                           role_names_pattern, role_binding_pattern, kopf_peerings_pattern) -> Optional[dict]:
    if operator_ns not in kutil.ls_ns_ex():
        return None

    ns_labels = kutil.get_ns(operator_ns).get("metadata", {}).get("labels", {})
    deploy = strip_k8s_metadata(kutil.get_deploy(operator_ns, operator_deploy_name))

    clusterroles =        {role["NAME"]: strip_k8s_metadata(kutil.get_clusterrole(role["NAME"])) for role in kutil.ls_clusterrole(crole_names_pattern)} if crole_names_pattern else {}
    clusterrolebindings = {binding["NAME"]: strip_k8s_metadata(kutil.get_clusterrolebinding(binding["NAME"])) for binding in kutil.ls_clusterrolebinding(crole_binding_pattern)} if crole_binding_pattern else {}
    clusterkopfpeerings = {peering["NAME"]: strip_k8s_metadata(kutil.get_clusterkopfpeering(peering["NAME"])) for peering in kutil.ls_clusterkopfpeering(ckopf_peerings_pattern)} if ckopf_peerings_pattern else {}
    sas =                 {sa["NAME"]: strip_k8s_metadata(kutil.get_sa(operator_ns, sa["NAME"])) for sa in kutil.ls_sa(operator_ns, sa_names_pattern)} if sa_names_pattern else {}
    secrets = {}
    for secret_name in get_referenced_image_pull_secret_names(deploy, sas):
        secret = kutil.get_secret(operator_ns, secret_name, check=False)
        if not secret:
            continue
        secrets[secret_name] = strip_k8s_metadata(secret)
    roles =               {role["NAME"]: strip_k8s_metadata(kutil.get_role(operator_ns, role["NAME"])) for role in kutil.ls_role(operator_ns, role_names_pattern)} if role_names_pattern else {}
    rolebindings =        {binding["NAME"]: strip_k8s_metadata(kutil.get_rolebinding(operator_ns, binding["NAME"])) for binding in kutil.ls_rolebinding(operator_ns, role_binding_pattern)} if role_binding_pattern else {}
    kopfpeerings =        {peering["NAME"]: strip_k8s_metadata(kutil.get_kopfpeering(operator_ns, peering["NAME"])) for peering in kutil.ls_kopfpeering(operator_ns, kopf_peerings_pattern)} if kopf_peerings_pattern else {}

    return {
        'namespace_labels': ns_labels,
        'deployment': deploy,
        'clusterroles': clusterroles,
        'clusterrolebindings': clusterrolebindings,
        'clusterkopfpeerings': clusterkopfpeerings,
        'serviceaccounts': sas,
        'secrets': secrets,
        'roles': roles,
        'rolebindings': rolebindings,
        'kopfpeerings': kopfpeerings
    }


def get_explicit_watch_namespaces(watch_namespaces: str) -> list[str]:
    namespaces = copy.deepcopy(watch_namespaces)
    if not namespaces:
        return []

    normalized = []
    seen = set()
    for namespace in namespaces.split(','):
        namespace = namespace.strip()
        if not namespace:
            continue
        if namespace in seen:
            continue
        seen.add(namespace)
        normalized.append(namespace)

    return normalized


def normalize_watch_namespaces(watch_namespaces: str) -> str:
    return ",".join(get_explicit_watch_namespaces(watch_namespaces))


def get_canonical_topology_namespaces(watch_namespaces: str) -> list[str]:
    return sorted(get_explicit_watch_namespaces(watch_namespaces))


def get_topology_annotation_value(
    watch_namespaces: str = "",
    standalone: bool = False,
) -> str:
    namespaces = get_canonical_topology_namespaces(watch_namespaces)
    scope = "global" if not namespaces else "scoped"
    return json.dumps(
        {
            "version": 1,
            "scope": scope,
            "standalone": standalone,
            "namespaces": namespaces,
        },
        separators=(",", ":"),
    )


def get_patched_artifacts(artifacts: dict, release_name: str, operator_ns: str, operator_name: str,
                          custom_meta: dict = None, custom_spec: dict = None, watch_namespaces: str = "",
                          standalone: bool = False, operator_debug: bool = False) -> dict:
    if not artifacts:
        raise ValueError("The artifacts dictionary is None or empty. Ensure the base operator manifests were successfully extracted.")

    patched = copy.deepcopy(artifacts)
    custom_meta = custom_meta or {}
    custom_spec = custom_spec or {}

    custom_template_spec = custom_spec.get("template", {}).get("spec", {})
    custom_containers_by_name = {
        container["name"]: container
        for container in custom_template_spec.get("containers", [])
        if container.get("name")
    }

    # Map object names precisely based on the free functions derived from helm logic
    operator_role_name = get_operator_role_name(operator_ns, operator_name)
    sidecar_role_name = get_sidecar_role_name(operator_ns, operator_name)
    switchover_role_name = get_switchover_role_name(operator_ns, operator_name)
    sa_name = get_service_account_name(operator_ns, operator_name)
    rolebinding_name = get_operator_rolebinding_name(operator_ns, operator_name)
    normalized_watch_namespaces = normalize_watch_namespaces(watch_namespaces)
    peering_name = get_env_var_peering_name(operator_ns, operator_name, normalized_watch_namespaces)
    global_labels = get_global_labels(release_name, operator_ns, operator_name)
    deployment_selector_labels = get_deployment_selector_labels(operator_ns, operator_name)

    # 0. Clean up system-managed labels and Patch Helm Annotations
    if 'namespace_labels' in patched:
        patched['namespace_labels'].pop('kubernetes.io/metadata.name', None)

    def patch_helm_annotations(manifest):
        if not manifest or 'metadata' not in manifest:
            return
        annotations = manifest['metadata'].get('annotations')
        if annotations:
            if 'meta.helm.sh/release-name' in annotations:
                annotations['meta.helm.sh/release-name'] = str(release_name)
            if 'meta.helm.sh/release-namespace' in annotations:
                annotations['meta.helm.sh/release-namespace'] = str(operator_ns)

    def patch_metadata_labels(manifest, labels):
        if not manifest or 'metadata' not in manifest:
            return
        manifest['metadata'].setdefault('labels', {}).update(labels)

    for category in ['serviceaccounts', 'clusterroles', 'clusterrolebindings', 'clusterkopfpeerings', 'kopfpeerings', 'roles', 'rolebindings']:
        for manifest in patched.get(category, {}).values():
            patch_helm_annotations(manifest)
    patch_helm_annotations(patched.get('deployment'))

    # 1. ServiceAccounts
    if 'serviceaccounts' not in patched or not patched['serviceaccounts']:
        patched['serviceaccounts'] = {
            sa_name: {
                "apiVersion": "v1",
                "kind": "ServiceAccount",
                "metadata": {"name": sa_name, "namespace": operator_ns}
            }
        }
    else:
        for sa_manifest in patched.get('serviceaccounts', {}).values():
            sa_manifest['metadata']['name'] = sa_name
            sa_manifest['metadata']['namespace'] = operator_ns
            patch_metadata_labels(sa_manifest, global_labels)

    for secret_manifest in patched.get('secrets', {}).values():
        secret_manifest.setdefault('metadata', {})['namespace'] = operator_ns

    # 2. ClusterRoles
    for cr_manifest in patched.get('clusterroles', {}).values():
        name = cr_manifest['metadata'].get('name')
        if name == 'mysql-operator':
            cr_manifest['metadata']['name'] = operator_role_name
        elif name == 'mysql-sidecar':
            cr_manifest['metadata']['name'] = sidecar_role_name
        elif name == 'mysql-switchover':
            cr_manifest['metadata']['name'] = switchover_role_name
        patch_metadata_labels(cr_manifest, global_labels)

    # 3. ClusterRoleBindings
    for crb_manifest in patched.get('clusterrolebindings', {}).values():
        crb_manifest['metadata']['name'] = rolebinding_name
        crb_manifest['roleRef']['name'] = operator_role_name
        patch_metadata_labels(crb_manifest, global_labels)

        if 'subjects' in crb_manifest and len(crb_manifest['subjects']) > 0:
            crb_manifest['subjects'][0]['name'] = sa_name
            crb_manifest['subjects'][0]['namespace'] = operator_ns

    # 4. Peerings Configuration
    if not standalone:
        if normalized_watch_namespaces:
            patched['clusterkopfpeerings'] = {}
            patched['kopfpeerings'] = {}
            for ns in get_explicit_watch_namespaces(normalized_watch_namespaces):
                patched['kopfpeerings'][f'{ns}-{peering_name}'] = {
                    "apiVersion": "zalando.org/v1",
                    "kind": "KopfPeering",
                    "metadata": {
                        "name": peering_name,
                        "namespace": ns,
                        "labels": copy.deepcopy(global_labels),
                    }
                }
        else:
            for kp_manifest in patched.get('clusterkopfpeerings', {}).values():
                kp_manifest['metadata']['name'] = peering_name
                patch_metadata_labels(kp_manifest, global_labels)
            for kp_manifest in patched.get('kopfpeerings', {}).values():
                kp_manifest['metadata']['name'] = peering_name
                patch_metadata_labels(kp_manifest, global_labels)
    else:
        patched['clusterkopfpeerings'] = {}
        patched['kopfpeerings'] = {}

    # 5. Roles & RoleBindings
    for r_manifest in patched.get('roles', {}).values():
        r_manifest['metadata']['namespace'] = operator_ns
        patch_metadata_labels(r_manifest, global_labels)

    for rb_manifest in patched.get('rolebindings', {}).values():
        rb_manifest['metadata']['namespace'] = operator_ns
        patch_metadata_labels(rb_manifest, global_labels)
        if 'subjects' in rb_manifest and len(rb_manifest['subjects']) > 0:
            rb_manifest['subjects'][0]['name'] = sa_name
            rb_manifest['subjects'][0]['namespace'] = operator_ns

    # 6. Deployment
    deploy = patched.get('deployment')
    if deploy:
        deploy['metadata']['name'] = operator_name
        deploy['metadata']['namespace'] = operator_ns
        deploy['spec']['template']['spec']['serviceAccountName'] = sa_name
        deploy['spec']['template']['spec'].pop('serviceAccount', None)

        deploy_metadata_labels = deploy['metadata'].setdefault('labels', {})
        deploy_template_labels = deploy['spec']['template']['metadata'].setdefault('labels', {})
        deploy_selector_labels = deploy['spec'].setdefault('selector', {}).setdefault('matchLabels', {})

        version_label = deploy_metadata_labels.get('version')
        app_version_label = deploy_metadata_labels.get('app.kubernetes.io/version')

        # Merge Custom Meta
        for k, v in custom_meta.get("labels", {}).items():
            deploy_metadata_labels[k] = str(v)
        for k, v in custom_meta.get("annotations", {}).items():
            deploy["metadata"].setdefault("annotations", {})[k] = str(v)
        deploy["metadata"].setdefault("annotations", {})[
            TOPOLOGY_ANNOTATION_KEY
        ] = get_topology_annotation_value(
            normalized_watch_namespaces,
            standalone,
        )

        # Enforce Helm-aligned labels
        deploy_metadata_labels.update(get_labels(release_name, operator_ns, operator_name, app_version_label, version_label))
        deploy_metadata_labels.update(global_labels)
        deploy_selector_labels.clear()
        deploy_selector_labels.update(deployment_selector_labels)
        deploy_template_labels.update(get_labels(release_name, operator_ns, operator_name, app_version_label, version_label))

        # Merge Custom Spec
        if "template" in custom_spec and "metadata" in custom_spec["template"]:
            for k, v in custom_spec["template"]["metadata"].get("labels", {}).items():
                deploy_template_labels[k] = str(v)
            for k, v in custom_spec["template"]["metadata"].get("annotations", {}).items():
                deploy["spec"]["template"]["metadata"].setdefault("annotations", {})[k] = str(v)

        if "replicas" in custom_spec:
            deploy["spec"]["replicas"] = custom_spec["replicas"]

        if standalone and "strategy" not in custom_spec:
            deploy["spec"]["strategy"] = {
                "type": "Recreate",
            }

        if "strategy" in custom_spec:
            deploy["spec"]["strategy"] = copy.deepcopy(custom_spec["strategy"])

        if "template" in custom_spec and "spec" in custom_spec["template"]:
            if "nodeSelector" in custom_template_spec:
                deploy["spec"]["template"]["spec"]["nodeSelector"] = custom_template_spec["nodeSelector"]
            if "affinity" in custom_template_spec:
                deploy["spec"]["template"]["spec"]["affinity"] = custom_template_spec["affinity"]

        # Environment Variables
        containers = deploy['spec']['template']['spec']['containers']
        for container in containers:
            if container['name'] == 'mysql-operator':
                # Convert the env list into a dict for precise updates without destroying valueFrom structs
                env_dict = {e['name']: e for e in container.get('env', [])}

                custom_container = custom_containers_by_name.get(container['name'], {})
                for env_entry in custom_container.get('env', []):
                    env_name = env_entry.get('name')
                    if env_name:
                        env_dict[env_name] = copy.deepcopy(env_entry)

                env_dict["OPERATOR_DEPLOYMENT_NAME"] = {"name": "OPERATOR_DEPLOYMENT_NAME", "value": operator_name}
                env_dict["OPERATOR_PEERING_NAME"] = {"name": "OPERATOR_PEERING_NAME", "value": peering_name}
                env_dict["OPERATOR_ROLE_NAME"] = {"name": "OPERATOR_ROLE_NAME", "value": operator_role_name}
                env_dict["SIDECAR_ROLE_NAME"] = {"name": "SIDECAR_ROLE_NAME", "value": sidecar_role_name}
                env_dict["SWITCHOVER_ROLE_NAME"] = {"name": "SWITCHOVER_ROLE_NAME", "value": switchover_role_name}
                env_dict["OPERATOR_NAMESPACES"] = {"name": "OPERATOR_NAMESPACES", "value": normalized_watch_namespaces}
                env_dict["OPERATOR_STANDALONE"] = {"name": "OPERATOR_STANDALONE", "value": str(standalone).lower()}
                env_dict["MYSQL_OPERATOR_DEBUG"] = {"name": "MYSQL_OPERATOR_DEBUG", "value": "1" if operator_debug else "0"}

                container['env'] = list(env_dict.values())

    return patched


def get_operator_envs_from_deployment(operator_deployment: dict) -> dict:
    containers = operator_deployment["spec"]["template"]["spec"].get("containers", [])
    assert len(containers) == 1, f"Expected exactly 1 container, got {len(containers)}"

    op_container = containers[0]
    assert op_container["name"] == "mysql-operator", f"Unexpected container name: {op_container['name']}"

    return {e["name"]: e.get("value") or e.get("valueFrom") for e in op_container.get("env", [])}


def strip_managed_operator_labels_from_deployment(operator_deployment: dict) -> None:
    for labels in (
        operator_deployment.get("metadata", {}).get("labels"),
        operator_deployment.get("spec", {})
        .get("template", {})
        .get("metadata", {})
        .get("labels"),
    ):
        if not labels:
            continue
        for key in list(labels):
            if key.startswith("app.kubernetes.io/"):
                labels.pop(key, None)


def apply_legacy_global_operator_fingerprint(operator_deployment: dict) -> None:
    strip_managed_operator_labels_from_deployment(operator_deployment)

    metadata = operator_deployment.setdefault("metadata", {})
    metadata.setdefault("annotations", {}).pop(TOPOLOGY_ANNOTATION_KEY, None)
    metadata["name"] = "mysql-operator"

    selector_labels = (
        operator_deployment.setdefault("spec", {})
        .setdefault("selector", {})
        .setdefault("matchLabels", {})
    )
    selector_labels.clear()
    selector_labels["name"] = "mysql-operator"

    template_labels = (
        operator_deployment.setdefault("spec", {})
        .setdefault("template", {})
        .setdefault("metadata", {})
        .setdefault("labels", {})
    )
    template_labels.clear()
    template_labels["name"] = "mysql-operator"

    container = get_named_container(
        operator_deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", []),
        "mysql-operator",
    )
    if container is None:
        return

    container["env"] = [
        env
        for env in container.get("env", [])
        if env.get("name") not in {"OPERATOR_NAMESPACES", "OPERATOR_STANDALONE"}
    ]


def apply_initial_raw_manifest_operator_fingerprint(
    operator_deployment: dict,
) -> None:
    metadata = operator_deployment.setdefault("metadata", {})
    labels = metadata.setdefault("labels", {})
    labels["app.kubernetes.io/managed-by"] = "mysql-operator"
    labels["app.kubernetes.io/created-by"] = "mysql-operator"


def get_named_container(containers: Optional[list], name: str) -> Optional[dict]:
    for container in containers or []:
        if container.get("name") == name:
            return container
    return None


def is_operator_deployment(deployment: Optional[dict]) -> bool:
    if not isinstance(deployment, dict):
        return False

    containers = (
        deployment.get("spec", {})
        .get("template", {})
        .get("spec", {})
        .get("containers", [])
    )
    return get_named_container(containers, "mysql-operator") is not None


def list_cluster_operator_deployments() -> list[tuple[str, str]]:
    operator_deployments: list[tuple[str, str]] = []

    for namespace in sorted(kutil.ls_ns_ex()):
        for deploy in sorted(kutil.ls_deploy(namespace), key=lambda deploy: deploy["NAME"]):
            deployment_name = deploy["NAME"]
            deployment = kutil.get_deploy(
                namespace,
                deployment_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            if not is_operator_deployment(deployment):
                continue
            operator_deployments.append((namespace, deployment_name))

    return operator_deployments


def get_operator_cleanup_patterns(
    operator_ns: str,
    operator_deploy_name: str,
) -> dict[str, str]:
    return {
        "crole_names_pattern": get_crole_names_pattern(
            operator_ns,
            operator_deploy_name,
        ),
        "crole_binding_pattern": get_crole_binding_pattern(
            operator_ns,
            operator_deploy_name,
        ),
        "ckopf_peerings_pattern": get_ckopf_peerings_pattern(
            operator_ns,
            operator_deploy_name,
        ),
        "sa_names_pattern": get_sa_names_pattern(
            operator_ns,
            operator_deploy_name,
        ),
        "role_names_pattern": get_role_names_pattern(
            operator_ns,
            operator_deploy_name,
        ),
        "role_binding_pattern": get_role_binding_pattern(
            operator_ns,
            operator_deploy_name,
        ),
        "kopf_peerings_pattern": get_kopf_peerings_pattern(
            operator_deploy_name,
        ),
    }


def print_operator_log(operator_ns: str, operator_deploy_name: str) -> None:
    operator_pods = kutil.ls_po(operator_ns, pattern=f"{operator_deploy_name}-.*")
    if not operator_pods:
        print(
            f"No operator pods found for deployment "
            f"{operator_ns}/{operator_deploy_name}"
        )
        return

    for pod in operator_pods:
        operator_pod = pod["NAME"]
        try:
            contents = kutil.logs(
                operator_ns,
                [operator_pod, "mysql-operator"],
                since_time=0,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
        except Exception as exc:
            print(
                f"Failed to fetch operator log for "
                f"{operator_ns}/{operator_pod}: {exc}"
            )
            continue

        _print_traceback_context(
            f"operator log for {operator_ns}/{operator_pod}",
            contents,
            lines_before=100,
        )
        if _operator_container_has_previous_logs(operator_ns, operator_pod):
            try:
                previous = kutil.logs(
                    operator_ns,
                    [operator_pod, "mysql-operator"],
                    prev=True,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
            except Exception:
                previous = ""
            if previous:
                _print_traceback_context(
                    f"previous operator log for {operator_ns}/{operator_pod}",
                    previous,
                    lines_before=100,
                )
                print(previous)

        print(
            f"Operator log for {operator_ns}/{operator_pod} "
            f"(matching deployment {operator_deploy_name}):"
        )
        print(contents)


def _tail_text(contents: str, lines: int = 10) -> str:
    if not contents:
        return ""
    return "\n".join(contents.splitlines()[-lines:])


def _traceback_context(contents: str, lines_before: int = 100) -> Optional[str]:
    if not contents:
        return None

    log_lines = contents.splitlines()
    for index, line in enumerate(log_lines):
        if "Traceback" in line:
            start = max(0, index - lines_before)
            return "\n".join(log_lines[start:])

    return None


def _print_traceback_context(label: str, contents: str, *, lines_before: int = 100) -> bool:
    snippet = _traceback_context(contents, lines_before=lines_before)
    if not snippet:
        return False

    print(f"Traceback context from {label}:")
    print(snippet)
    return True


def _operator_container_has_previous_logs(operator_ns: str, operator_pod_name: str) -> bool:
    pod = kutil.get_po(
        operator_ns,
        operator_pod_name,
        check=False,
        cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
    )
    if not pod:
        return False

    for status in pod.get("status", {}).get("containerStatuses", []):
        if status.get("name") != "mysql-operator":
            continue
        if status.get("restartCount", 0) > 0:
            return True
        last_state = status.get("lastState", {})
        if last_state.get("terminated"):
            return True

    return False


def print_operator_pod_log_tail(
    operator_ns: str,
    operator_pod_name: str,
    *,
    lines: int = 10,
    include_previous: bool = True,
) -> None:
    try:
        current = kutil.logs(
            operator_ns,
            [operator_pod_name, "mysql-operator"],
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
    except Exception as exc:
        print(f"Failed to fetch current operator log for {operator_ns}/{operator_pod_name}: {exc}")
    else:
        _print_traceback_context(
            f"operator log for {operator_ns}/{operator_pod_name}",
            current,
            lines_before=100,
        )
        print(f"Last {lines} lines of operator log for {operator_ns}/{operator_pod_name}:")
        print(_tail_text(current, lines))

    if not include_previous:
        return

    if not _operator_container_has_previous_logs(operator_ns, operator_pod_name):
        return

    try:
        previous = kutil.logs(
            operator_ns,
            [operator_pod_name, "mysql-operator"],
            prev=True,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
    except Exception:
        return

    if previous:
        _print_traceback_context(
            f"previous operator log for {operator_ns}/{operator_pod_name}",
            previous,
            lines_before=100,
        )
        print(f"Last {lines} lines of previous operator log for {operator_ns}/{operator_pod_name}:")
        print(_tail_text(previous, lines))


def print_operator_pod_traceback_context(
    operator_ns: str,
    operator_pod_name: str,
    *,
    lines_before: int = 100,
    include_previous: bool = True,
) -> bool:
    found = False

    try:
        current = kutil.logs(
            operator_ns,
            [operator_pod_name, "mysql-operator"],
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
    except Exception as exc:
        print(
            f"Failed to fetch current operator log while scanning for Traceback "
            f"for {operator_ns}/{operator_pod_name}: {exc}"
        )
    else:
        found |= _print_traceback_context(
            f"operator log for {operator_ns}/{operator_pod_name}",
            current,
            lines_before=lines_before,
        )

    if not include_previous:
        return found

    if not _operator_container_has_previous_logs(operator_ns, operator_pod_name):
        return found

    try:
        previous = kutil.logs(
            operator_ns,
            [operator_pod_name, "mysql-operator"],
            prev=True,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
    except Exception as exc:
        print(
            f"Failed to fetch previous operator log while scanning for Traceback "
            f"for {operator_ns}/{operator_pod_name}: {exc}"
        )
        return found

    found |= _print_traceback_context(
        f"previous operator log for {operator_ns}/{operator_pod_name}",
        previous,
        lines_before=lines_before,
    )
    return found


def print_operator_traceback_context(
    operator_ns: str,
    operator_deploy_name: str,
    *,
    lines_before: int = 100,
) -> bool:
    operator_pods = kutil.ls_po(operator_ns, pattern=f"{operator_deploy_name}-.*")
    if not operator_pods:
        return False

    found = False
    for pod in operator_pods:
        found |= print_operator_pod_traceback_context(
            operator_ns,
            pod["NAME"],
            lines_before=lines_before,
            include_previous=True,
        )
    return found


def print_operator_log_tail(operator_ns: str, operator_deploy_name: str, *, lines: int = 10) -> None:
    operator_pods = kutil.ls_po(operator_ns, pattern=f"{operator_deploy_name}-.*")
    if not operator_pods:
        print(f"No operator pods found for deployment {operator_ns}/{operator_deploy_name}")
        return

    for pod in operator_pods:
        print_operator_pod_log_tail(
            operator_ns,
            pod["NAME"],
            lines=lines,
            include_previous=pod.get("STATUS") in ("CrashLoopBackOff", "Completed"),
        )


def wait_for_operator_pod_failure(operator_ns: str, operator_name: str, timeout: int = 60) -> str:
    deadline = time.time() + timeout

    while time.time() < deadline:
        pods = kutil.ls_po(operator_ns, pattern=f"{operator_name}.*")
        for pod in pods:
            if pod["STATUS"] in ("CrashLoopBackOff", "Error", "Completed"):
                print_operator_pod_log_tail(
                    operator_ns,
                    pod["NAME"],
                    include_previous=pod["STATUS"] in ("CrashLoopBackOff", "Completed"),
                )
                return pod["NAME"]
            pod_obj = kutil.get_po(operator_ns, pod["NAME"], check=False)
            if not pod_obj:
                continue
            for status in pod_obj.get("status", {}).get("containerStatuses", []):
                state = status.get("state", {})
                waiting = state.get("waiting", {})
                terminated = state.get("terminated", {})
                if waiting.get("reason") in ("CrashLoopBackOff", "Error"):
                    print_operator_pod_log_tail(
                        operator_ns,
                        pod["NAME"],
                        include_previous=True,
                    )
                    return pod["NAME"]
                if terminated:
                    print_operator_pod_log_tail(
                        operator_ns,
                        pod["NAME"],
                        include_previous=True,
                    )
                    return pod["NAME"]
        time.sleep(1)

    print_operator_log_tail(operator_ns, operator_name)
    raise AssertionError(f"Operator pod {operator_ns}/{operator_name} did not fail within {timeout}s")


def get_operator_pod_logs(operator_ns: str, operator_pod_name: str) -> str:
    contents = []

    if _operator_container_has_previous_logs(operator_ns, operator_pod_name):
        try:
            previous = kutil.logs(
                operator_ns,
                [operator_pod_name, "mysql-operator"],
                prev=True,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
        except Exception:
            previous = ""
        if previous:
            contents.append(previous)

    try:
        current = kutil.logs(
            operator_ns,
            [operator_pod_name, "mysql-operator"],
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
    except Exception:
        current = ""
    if current:
        contents.append(current)

    return "\n".join(contents)


def wait_for_operator_failure_log_fragment(
    operator_ns: str,
    operator_name: str,
    error_fragment: str,
    *,
    timeout: int = 60,
) -> str:
    deadline = time.time() + timeout
    last_pod_name = None
    last_contents = ""

    while time.time() < deadline:
        pods = kutil.ls_po(operator_ns, pattern=f"{operator_name}.*")
        for pod in pods:
            pod_name = pod["NAME"]
            contents = get_operator_pod_logs(operator_ns, pod_name)
            if contents:
                last_pod_name = pod_name
                last_contents = contents
                if error_fragment in contents:
                    return pod_name
        time.sleep(1)

    if last_pod_name is not None:
        print_operator_pod_log_tail(
            operator_ns,
            last_pod_name,
            include_previous=True,
        )
    else:
        print_operator_log_tail(operator_ns, operator_name)

    tail = _tail_text(last_contents, 20) if last_contents else "<no operator logs collected>"
    raise AssertionError(
        f"Operator log for {operator_ns}/{operator_name} did not contain "
        f"{error_fragment!r} within {timeout}s. Last log tail:\n{tail}"
    )


def assert_no_peering(testcase, operator_ns: str, operator_name: str) -> None:
    testcase.assertEqual(
        kutil.ls_clusterkopfpeering(get_ckopf_peerings_pattern(operator_ns, operator_name)),
        [],
    )

    matching_peerings = []
    for namespace in kutil.ls_ns_ex():
        for peering in kutil.ls_kopfpeering(namespace, get_kopf_peerings_pattern(operator_name)):
            matching_peerings.append(f"{namespace}/{peering['NAME']}")

    testcase.assertEqual(matching_peerings, [])


def assert_mapping_contains(
    testcase: unittest.TestCase,
    actual: Optional[dict],
    expected: dict,
    *,
    label: str,
) -> None:
    testcase.assertIsInstance(actual, dict, msg=f"{label} is missing")
    for key, value in expected.items():
        testcase.assertEqual(
            actual.get(key),
            value,
            msg=f"{label} missing {key}={value!r}: {actual!r}",
        )


def assert_resource_requirements_contain(
    testcase: unittest.TestCase,
    actual: Optional[dict],
    expected: dict,
    *,
    label: str,
) -> None:
    testcase.assertIsInstance(actual, dict, msg=f"{label} is missing")
    for resource_type, resource_values in expected.items():
        assert_mapping_contains(
            testcase,
            actual.get(resource_type),
            resource_values,
            label=f"{label}.{resource_type}",
        )


def get_worker_nodes(
    testcase: unittest.TestCase,
    *,
    min_count: int = 3,
) -> list[dict[str, str]]:
    worker_nodes = []

    for node in kutil.ls_nodes():
        if not isinstance(node, dict):
            continue

        roles = str(node.get("ROLES", ""))
        if "control-plane" in roles or "master" in roles:
            continue

        node_name = str(node.get("NAME", "")).strip()
        if not node_name:
            continue

        node_labels = kutil.get_node_labels(node_name)
        hostname_label = str(
            node_labels.get("kubernetes.io/hostname", "")
        ).strip()
        if not hostname_label:
            testcase.fail(
                f"Worker node {node_name} is missing "
                "kubernetes.io/hostname label"
            )

        worker_nodes.append(
            {
                "name": node_name,
                "hostname": hostname_label,
            }
        )

    worker_nodes = sorted(worker_nodes, key=lambda node: node["name"])
    if len(worker_nodes) < min_count:
        testcase.skipTest(
            f"This test requires at least {min_count} worker nodes, "
            f"found {len(worker_nodes)}"
        )

    return worker_nodes


def get_worker_node_names(
    testcase: unittest.TestCase,
    *,
    min_count: int = 3,
) -> list[str]:
    return [
        worker_node["name"]
        for worker_node in get_worker_nodes(
            testcase,
            min_count=min_count,
        )
    ]


def create_blocker_pod(
    namespace: str,
    pod_name: str,
    *,
    node_name: str,
    labels: dict[str, str],
) -> None:
    pod_manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": pod_name,
            "labels": labels,
        },
        "spec": {
            "nodeName": node_name,
            "terminationGracePeriodSeconds": 0,
            "containers": [
                {
                    "name": "blocker",
                    "image": g_ts_cfg.get_operator_image(),
                    "imagePullPolicy": "IfNotPresent",
                    "command": ["bash", "-c", "sleep infinity"],
                },
            ],
        },
    }

    kutil.apply(
        namespace,
        yaml.safe_dump(pod_manifest, default_flow_style=False, sort_keys=False),
    )
    kutil.wait_pod(namespace, pod_name, "Running", timeout=300)


def delete_pods(namespace: str, pod_names: list[str]) -> None:
    for pod_name in pod_names:
        if not pod_name:
            continue
        try:
            kutil.delete_po(namespace, pod_name, timeout=120, force=True)
        except Exception:
            pass


def assert_helm_warning_present(
    testcase: unittest.TestCase,
    output_sections: dict[str, str],
    warning_fragment: str,
) -> None:
    testcase.assertIn(
        warning_fragment,
        output_sections.get("NOTES", ""),
        msg=f"Helm NOTES missing warning fragment: {warning_fragment!r}",
    )
    testcase.assertIn(
        warning_fragment,
        output_sections.get("MANIFEST", ""),
        msg=f"Helm MANIFEST missing warning fragment: {warning_fragment!r}",
    )


def assert_helm_warning_absent(
    testcase: unittest.TestCase,
    output_sections: dict[str, str],
    warning_fragment: str,
) -> None:
    testcase.assertNotIn(
        warning_fragment,
        output_sections.get("NOTES", ""),
        msg=f"Helm NOTES unexpectedly contained warning fragment: {warning_fragment!r}",
    )
    testcase.assertNotIn(
        warning_fragment,
        output_sections.get("MANIFEST", ""),
        msg=f"Helm MANIFEST unexpectedly contained warning fragment: {warning_fragment!r}",
    )


class OperatorTest(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def test_1_check_security(self):
        """
        Ensure PodSecurityContext has required restrictions.
        """

        def check_pod(pod, process):
            # kubectl exec runs as the mysql user
            out = kutil.execp("mysql-operator", [pod, "mysql-operator"], ["id"])
            self.assertTrue(out.startswith(b"uid="))
            self.assertNotEqual(f"uid=0(root) gid=0(root) groups=0(root)", out.strip().decode("utf-8"))

            # cmdline of process 1 is mysqld
            out = kutil.execp("mysql-operator", [pod, "mysql-operator"], ["cat", "/proc/1/cmdline"])
            actual_process = out.split(b"\0")[0].decode("utf-8")
            self.assertIn(
                os.path.basename(actual_process),
                (process, f"{process}.bin"),
            )

            # /proc/1 is owned by (runs as) uid=mysql/27, gid=mysql/27
            out = kutil.execp("mysql-operator", [pod, "mysql-operator"], ["stat", "/proc/1"])
            access = [line for line in out.split(b"\n") if line.startswith(b"Access")][0].strip().decode("utf-8")
            self.assertTrue(access)
            self.assertNotEqual(f"Access: (0555/dr-xr-xr-x)  Uid: ({0:5}/{'root':>8})   Gid: ({0:5}/{'root':>8})", access)

        p = kutil.ls_po("mysql-operator", pattern="mysql-operator-.*")[0]["NAME"]
        check_pod(p, "mysqlsh")


# ==================================================================================================
# Base class to eliminate boilerplate for operator test setup/teardown
# ==================================================================================================
class OperatorSingleAndMultipleBaseTest(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.operator_ns = "mysql-operator"
        cls.operator_deploy_name = "mysql-operator"
        cls.crole_names_pattern = get_crole_names_pattern(cls.operator_ns, cls.operator_deploy_name)
        cls.crole_binding_pattern = get_crole_binding_pattern(cls.operator_ns, cls.operator_deploy_name)
        cls.ckopf_peerings_pattern = get_ckopf_peerings_pattern(cls.operator_ns, cls.operator_deploy_name)
        cls.sa_names_pattern = get_sa_names_pattern(cls.operator_ns, cls.operator_deploy_name)
        cls.role_names_pattern = get_role_names_pattern(cls.operator_ns, cls.operator_deploy_name)
        cls.role_binding_pattern = get_role_binding_pattern(cls.operator_ns, cls.operator_deploy_name)
        cls.kopf_peerings_pattern = get_kopf_peerings_pattern(cls.operator_deploy_name)
        cls.get_initial_artifacts()

    def setUp(self):
        super().setUp()
        self._ensure_clean_operator_test_start()

    @classmethod
    def get_initial_artifacts(cls):
        cls.artifacts = get_operator_artifacts(operator_ns=cls.operator_ns,
                                operator_deploy_name=cls.operator_deploy_name,
                                crole_names_pattern=cls.crole_names_pattern,
                                crole_binding_pattern=cls.crole_binding_pattern,
                                ckopf_peerings_pattern=cls.ckopf_peerings_pattern,
                                sa_names_pattern=cls.sa_names_pattern,
                                role_names_pattern=cls.role_names_pattern,
                                role_binding_pattern=cls.role_binding_pattern,
                                kopf_peerings_pattern=cls.kopf_peerings_pattern
                        )
        if cls.artifacts is not None:
            _track_kubectl_install_from_artifacts(
                operator_ns=cls.operator_ns,
                operator_deploy_name=cls.operator_deploy_name,
                artifacts=cls.artifacts,
            )
        else:
            _clear_tracked_kubectl_install_result(
                get_kubectl_install_target(
                    cls.operator_ns,
                    cls.operator_deploy_name,
                )
            )

    @classmethod
    def tearDownClass(cls):
        new_artifacts = get_operator_artifacts(operator_ns=cls.operator_ns,
                                operator_deploy_name=cls.operator_deploy_name,
                                crole_names_pattern=cls.crole_names_pattern,
                                crole_binding_pattern=cls.crole_binding_pattern,
                                ckopf_peerings_pattern=cls.ckopf_peerings_pattern,
                                sa_names_pattern=cls.sa_names_pattern,
                                role_names_pattern=cls.role_names_pattern,
                                role_binding_pattern=cls.role_binding_pattern,
                                kopf_peerings_pattern=cls.kopf_peerings_pattern
                        )

        if cls.artifacts != new_artifacts:
            restore_operator(cls.artifacts, cls.operator_ns)

        super().tearDownClass()

    def _is_default_operator(
        self,
        operator_ns: str,
        operator_deploy_name: str,
    ) -> bool:
        return (
            operator_ns == self.operator_ns
            and operator_deploy_name == self.operator_deploy_name
        )

    def _remove_unexpected_operator(
        self,
        operator_ns: str,
        operator_deploy_name: str,
    ) -> None:
        deployment = kutil.get_deploy(
            operator_ns,
            operator_deploy_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        if deployment is None:
            return

        print(
            f"WARNING: unexpected operator found before test start: "
            f"{operator_ns}/{operator_deploy_name}; removing it"
        )
        cleanup_patterns = get_operator_cleanup_patterns(
            operator_ns,
            operator_deploy_name,
        )
        live_artifacts = get_operator_artifacts(
            operator_ns=operator_ns,
            operator_deploy_name=operator_deploy_name,
            **cleanup_patterns,
        )
        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_deploy_name,
            artifacts=live_artifacts,
            return_manifests=False,
            delete_namespace=operator_ns != self.operator_ns,
            check_namespace_empty=False,
        )

    def _ensure_default_operator_present(self) -> None:
        default_operator = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        if default_operator is None:
            if self.artifacts is None:
                raise RuntimeError(
                    "default operator mysql-operator/mysql-operator is missing "
                    "and no baseline artifacts were captured"
                )
            print(
                "WARNING: default operator mysql-operator/mysql-operator is "
                "missing before test start; reinstalling it"
            )
            restore_operator(self.artifacts, self.operator_ns)

        try:
            kutil.wait_deploy(
                self.operator_ns,
                self.operator_deploy_name,
                timeout=300,
            )
        except Exception:
            print_operator_log_tail(
                self.operator_ns,
                self.operator_deploy_name,
                lines=20,
            )
            raise

    def _ensure_clean_operator_test_start(self) -> None:
        for operator_ns, operator_deploy_name in list_cluster_operator_deployments():
            if self._is_default_operator(operator_ns, operator_deploy_name):
                continue
            self._remove_unexpected_operator(
                operator_ns,
                operator_deploy_name,
            )

        self._ensure_default_operator_present()

    def remove_operator(self) -> None:
        return remove_operator(operator_ns=self.operator_ns,
                                operator_deploy_name=self.operator_deploy_name,
                                artifacts=self.artifacts,
                                delete_namespace=True,
                                check_namespace_empty=False,
                            )

    def _remove_default_operator_or_fail(self) -> dict:
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)
        return original_artifacts

    def _restore_default_operator(self, original_artifacts: dict) -> None:
        restore_operator(original_artifacts, self.operator_ns)
        kutil.wait_deploy(
            self.operator_ns,
            self.operator_deploy_name,
            timeout=300,
        )

    def add_operator_cleanup(self, operator_ns: str, operator_name: str) -> None:
        # Keep transient operator installs from leaking when a test fails after install.
        self.addCleanup(
            remove_operator,
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            return_manifests=False,
            delete_namespace=True,
            check_namespace_empty=True,
        )

    def _cleanup_operator_if_present(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        delete_namespace: bool = True,
        check_namespace_empty: bool = True,
    ) -> None:
        if operator_ns not in kutil.ls_ns_ex():
            return

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            return_manifests=False,
            delete_namespace=delete_namespace,
            check_namespace_empty=check_namespace_empty,
        )

    def _cleanup_cluster_namespace_if_present(
        self,
        *,
        namespace: str,
        cluster_name: str,
        secret_name: str,
        delete_namespace: bool = True,
    ) -> None:
        if namespace not in kutil.ls_ns_ex():
            return

        if kutil.get(namespace, "ic", cluster_name, check=False) is not None:
            kutil.delete_ic(namespace, cluster_name)
            self.wait_pods_gone(f"{cluster_name}.*", ns=namespace)
            self.wait_routers_gone(f"{cluster_name}-router.*", ns=namespace)
            self.wait_ic_gone(cluster_name, ns=namespace)

        try:
            kutil.delete_pvc(namespace, None)
        except Exception:
            pass

        try:
            kutil.delete_secret(namespace, secret_name)
        except Exception:
            pass

        if delete_namespace and namespace in kutil.ls_ns_ex():
            kutil.delete_ns(namespace)

    def _get_single_helm_operator_pod(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict:
        operator_pods = self._list_active_operator_pods(
            namespace=namespace,
            deployment_name=deployment_name,
        )
        self.assertEqual(
            len(operator_pods),
            1,
            msg=(
                f"Expected exactly 1 active operator pod for deployment "
                f"{namespace}/{deployment_name}, got "
                f"{[pod.get('metadata', {}).get('name') for pod in operator_pods]!r}"
            ),
        )
        return operator_pods[0]

    def _get_single_operator_pod(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict:
        operator_pods = self._list_active_operator_pods(
            namespace=namespace,
            deployment_name=deployment_name,
        )
        self.assertEqual(
            len(operator_pods),
            1,
            msg=(
                f"Expected exactly 1 active operator pod for deployment "
                f"{namespace}/{deployment_name}, got "
                f"{[pod.get('metadata', {}).get('name') for pod in operator_pods]!r}"
            ),
        )
        return operator_pods[0]

    def _list_active_operator_pods(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> list[dict]:
        operator_pods = kutil.ls_po(
            namespace,
            pattern=f"{deployment_name}.*",
        )
        active_operator_pods: list[dict] = []
        for operator_pod in operator_pods:
            if operator_pod.get("STATUS") == "Terminating":
                continue
            pod_name = operator_pod["NAME"]
            pod = kutil.get_po(
                namespace,
                pod_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            if pod is None:
                continue
            if pod.get("metadata", {}).get("deletionTimestamp"):
                continue
            active_operator_pods.append(pod)
        return active_operator_pods

    def _wait_for_replacement_operator_pod(
        self,
        *,
        namespace: str,
        deployment_name: str,
        previous_pod_name: str,
        timeout: int = 300,
    ) -> dict:
        last_pod_names: list[str] = []
        deployment = kutil.get_deploy(
            namespace,
            deployment_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        selector_labels = (
            deployment.get("spec", {})
            .get("selector", {})
            .get("matchLabels", {})
            if deployment
            else {}
        )

        def pod_matches_deployment(pod: dict) -> bool:
            pod_labels = pod.get("metadata", {}).get("labels", {})
            return all(
                pod_labels.get(key) == value
                for key, value in selector_labels.items()
            )

        def has_operator_container(pod: dict) -> bool:
            return (
                get_named_container(
                    pod.get("spec", {}).get("containers", []),
                    "mysql-operator",
                )
                is not None
            )

        def operator_container_ready(pod: dict) -> bool:
            status = get_named_container(
                pod.get("status", {}).get("containerStatuses", []),
                "mysql-operator",
            )
            return bool(status and status.get("ready"))

        def pod_rank(pod: dict) -> tuple[bool, str, str]:
            metadata = pod.get("metadata", {})
            return (
                operator_container_ready(pod),
                metadata.get("creationTimestamp", ""),
                metadata.get("name", ""),
            )

        for _ in range(timeout):
            operator_pods = self._list_active_operator_pods(
                namespace=namespace,
                deployment_name=deployment_name,
            )
            last_pod_names = [
                pod.get("metadata", {}).get("name", "")
                for pod in operator_pods
            ]
            replacement_pods = [
                pod
                for pod in operator_pods
                if pod.get("metadata", {}).get("name") != previous_pod_name
                and pod_matches_deployment(pod)
                and has_operator_container(pod)
            ]
            if replacement_pods:
                return sorted(replacement_pods, key=pod_rank)[-1]
            time.sleep(1)

        raise AssertionError(
            f"Expected replacement operator pod for "
            f"{namespace}/{deployment_name} after {previous_pod_name}, "
            f"got {last_pod_names!r}"
        )

    def _wait_for_operator_pod_rollout(
        self,
        *,
        namespace: str,
        deployment_name: str,
        previous_pod_name: Optional[str] = None,
    ) -> dict:
        kutil.wait_deploy(namespace, deployment_name, timeout=300)

        operator_pod = None
        if previous_pod_name:
            operator_pod = self._wait_for_replacement_operator_pod(
                namespace=namespace,
                deployment_name=deployment_name,
                previous_pod_name=previous_pod_name,
            )

        if operator_pod is None:
            operator_pod = self._get_single_operator_pod(
                namespace=namespace,
                deployment_name=deployment_name,
            )
        kutil.wait_pod(
            namespace,
            operator_pod["metadata"]["name"],
            "Running",
            timeout=300,
        )
        current_operator_pod = kutil.get_po(
            namespace,
            operator_pod["metadata"]["name"],
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        return current_operator_pod or operator_pod

    def _apply_raw_operator_release(
        self,
        release: str,
        *,
        previous_pod_name: Optional[str] = None,
    ) -> dict:
        for filename in RAW_DEPLOY_MANIFEST_FILES:
            kutil.apply(
                None,
                render_raw_deploy_manifest_for_test_environment(
                    release,
                    filename,
                ),
            )

        operator_pod = self._wait_for_operator_pod_rollout(
            namespace=self.operator_ns,
            deployment_name=self.operator_deploy_name,
            previous_pod_name=previous_pod_name,
        )
        self._assert_raw_operator_selector_compatible()
        self._assert_helm_operator_image_tag(operator_pod, release)
        return operator_pod

    def _remove_live_default_operator_if_present(self) -> None:
        if self.operator_ns not in kutil.ls_ns_ex():
            return
        if kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        ) is None:
            return

        live_artifacts = get_operator_artifacts(
            operator_ns=self.operator_ns,
            operator_deploy_name=self.operator_deploy_name,
            crole_names_pattern=self.crole_names_pattern,
            crole_binding_pattern=self.crole_binding_pattern,
            ckopf_peerings_pattern=self.ckopf_peerings_pattern,
            sa_names_pattern=self.sa_names_pattern,
            role_names_pattern=self.role_names_pattern,
            role_binding_pattern=self.role_binding_pattern,
            kopf_peerings_pattern=self.kopf_peerings_pattern,
        )
        if live_artifacts is None:
            return

        remove_operator(
            operator_ns=self.operator_ns,
            operator_deploy_name=self.operator_deploy_name,
            artifacts=live_artifacts,
            return_manifests=False,
            delete_namespace=True,
            check_namespace_empty=False,
        )

    def _assert_raw_operator_selector_compatible(self) -> dict:
        deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        selector_labels = (
            deployment.get("spec", {})
            .get("selector", {})
            .get("matchLabels")
        )
        expected_selector_labels = get_legacy_deployment_selector_labels()
        self.assertEqual(
            selector_labels,
            expected_selector_labels,
            msg=(
                f"Raw operator Deployment selector must stay "
                f"{expected_selector_labels!r} for kubectl-apply upgrades, "
                f"got {selector_labels!r}"
            ),
        )
        template_labels = (
            deployment.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels")
        )
        assert_mapping_contains(
            self,
            template_labels,
            expected_selector_labels,
            label=(
                f"Deployment {self.operator_ns}/{self.operator_deploy_name} "
                "spec.template.metadata.labels"
            ),
        )
        return deployment

    def _get_single_operator_deployment_name(
        self,
        *,
        namespace: str,
    ) -> str:
        operator_deployment_names = [
            deployment_name
            for operator_ns, deployment_name in list_cluster_operator_deployments()
            if operator_ns == namespace
        ]
        self.assertEqual(
            len(operator_deployment_names),
            1,
            msg=(
                f"Expected exactly 1 operator Deployment in namespace "
                f"{namespace}, got {operator_deployment_names}"
            ),
        )
        return operator_deployment_names[0]

    @staticmethod
    def _get_endpoint_target_pod_names(endpoints: Optional[dict]) -> list[str]:
        target_pod_names: set[str] = set()
        for subset in (endpoints or {}).get("subsets", []) or []:
            for addresses_key in ("addresses", "notReadyAddresses"):
                for address in subset.get(addresses_key, []) or []:
                    target_ref = address.get("targetRef") or {}
                    target_name = target_ref.get("name")
                    if target_name:
                        target_pod_names.add(target_name)
        return sorted(target_pod_names)

    def _assert_helm_operator_deployment_selector_state(
        self,
        *,
        namespace: str,
        deployment_name: str,
        expected_deployment_selector_labels: dict,
        expect_service_selector_labels: bool = True,
    ) -> dict:
        operator_deployment = kutil.get_deploy(namespace, deployment_name)
        actual_deployment_selector = (
            operator_deployment.get("spec", {})
            .get("selector", {})
            .get("matchLabels")
        )
        self.assertEqual(
            actual_deployment_selector,
            expected_deployment_selector_labels,
            msg=(
                f"Unexpected Deployment selector for {namespace}/{deployment_name}: "
                f"{actual_deployment_selector!r}"
            ),
        )

        deployment_template_labels = (
            operator_deployment.get("spec", {})
            .get("template", {})
            .get("metadata", {})
            .get("labels")
        )
        assert_mapping_contains(
            self,
            deployment_template_labels,
            expected_deployment_selector_labels,
            label=(
                f"Deployment {namespace}/{deployment_name} "
                "spec.template.metadata.labels"
            ),
        )
        if expect_service_selector_labels:
            assert_mapping_contains(
                self,
                deployment_template_labels,
                get_service_selector_labels(namespace, deployment_name),
                label=(
                    f"Deployment {namespace}/{deployment_name} "
                    "spec.template.metadata.labels"
                ),
            )

        operator_pod = self._wait_for_helm_operator_pod_rollout(
            namespace=namespace,
            deployment_name=deployment_name,
        )
        operator_pod_labels = operator_pod.get("metadata", {}).get("labels")
        operator_pod_name = operator_pod["metadata"]["name"]
        assert_mapping_contains(
            self,
            operator_pod_labels,
            expected_deployment_selector_labels,
            label=f"Pod {namespace}/{operator_pod_name} metadata.labels",
        )
        if expect_service_selector_labels:
            assert_mapping_contains(
                self,
                operator_pod_labels,
                get_service_selector_labels(namespace, deployment_name),
                label=f"Pod {namespace}/{operator_pod_name} metadata.labels",
            )
        return operator_pod

    def _assert_helm_operator_service_isolated(
        self,
        *,
        namespace: str,
        deployment_name: str,
        expected_pod_name: Optional[str] = None,
    ) -> None:
        expected_service_selector = get_service_selector_labels(
            namespace,
            deployment_name,
        )

        operator_service = None
        for _ in range(60):
            operator_service = kutil.get(
                namespace,
                "svc",
                deployment_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            if operator_service is not None:
                break
            time.sleep(1)
        self.assertIsNotNone(
            operator_service,
            msg=f"Expected Service {namespace}/{deployment_name} to exist",
        )
        self.assertEqual(
            operator_service.get("spec", {}).get("selector"),
            expected_service_selector,
            msg=(
                f"Unexpected Service selector for {namespace}/{deployment_name}: "
                f"{operator_service.get('spec', {}).get('selector')!r}"
            ),
        )

        operator_pod = None
        if expected_pod_name is None:
            operator_pod = self._wait_for_helm_operator_pod_rollout(
                namespace=namespace,
                deployment_name=deployment_name,
            )
            expected_pod_name = operator_pod["metadata"]["name"]
        else:
            operator_pod = kutil.get_po(
                namespace,
                expected_pod_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )

        self.assertIsNotNone(
            operator_pod,
            msg=f"Expected operator pod {namespace}/{expected_pod_name} to exist",
        )
        assert_mapping_contains(
            self,
            operator_pod.get("metadata", {}).get("labels"),
            expected_service_selector,
            label=f"Pod {namespace}/{expected_pod_name} metadata.labels",
        )

        last_target_pod_names = []
        last_endpoints = None
        for _ in range(120):
            last_endpoints = kutil.get(
                namespace,
                "endpoints",
                deployment_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            last_target_pod_names = self._get_endpoint_target_pod_names(
                last_endpoints,
            )
            if last_target_pod_names == [expected_pod_name]:
                return
            time.sleep(1)

        raise AssertionError(
            f"Service {namespace}/{deployment_name} should route only to "
            f"{expected_pod_name}, got endpoint targets {last_target_pod_names!r}. "
            f"Last Endpoints object: {last_endpoints!r}"
        )

    def _assert_helm_operator_selector_isolation(
        self,
        *,
        namespace: str,
        deployment_name: str,
        expected_deployment_selector_labels: dict,
        expect_service_selector_labels: bool = True,
        check_service_isolation: bool = True,
    ) -> dict:
        operator_pod = self._assert_helm_operator_deployment_selector_state(
            namespace=namespace,
            deployment_name=deployment_name,
            expected_deployment_selector_labels=expected_deployment_selector_labels,
            expect_service_selector_labels=expect_service_selector_labels,
        )
        if check_service_isolation:
            self._assert_helm_operator_service_isolated(
                namespace=namespace,
                deployment_name=deployment_name,
                expected_pod_name=operator_pod["metadata"]["name"],
            )
        return operator_pod

    def _wait_for_helm_operator_pod_rollout(
        self,
        *,
        namespace: str,
        deployment_name: str,
        previous_pod_name: Optional[str] = None,
    ) -> dict:
        kutil.wait_deploy(namespace, deployment_name, timeout=300)

        operator_pod = None
        if previous_pod_name:
            operator_pod = self._wait_for_replacement_operator_pod(
                namespace=namespace,
                deployment_name=deployment_name,
                previous_pod_name=previous_pod_name,
            )

        if operator_pod is None:
            operator_pod = self._get_single_helm_operator_pod(
                namespace=namespace,
                deployment_name=deployment_name,
            )
        kutil.wait_pod(
            namespace,
            operator_pod["metadata"]["name"],
            "Running",
            timeout=300,
        )
        current_operator_pod = kutil.get_po(
            namespace,
            operator_pod["metadata"]["name"],
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        return current_operator_pod or operator_pod

    @staticmethod
    def _split_image_reference(image: str) -> tuple[str, str]:
        image_reference = image.split("@", 1)[0]
        last_slash = image_reference.rfind("/")
        last_colon = image_reference.rfind(":")
        if last_colon <= last_slash:
            raise AssertionError(
                f"Expected a tagged image reference, got {image}"
            )
        return (
            image_reference[last_slash + 1 : last_colon],
            image_reference[last_colon + 1 :],
        )

    @staticmethod
    def _extract_runtime_image_digest(image_id: Optional[str]) -> Optional[str]:
        if not image_id:
            return None

        digest_match = re.search(r"(sha256:[0-9a-f]{64})$", image_id)
        if digest_match is None:
            return None
        return digest_match.group(1)

    @staticmethod
    def _format_image_identity_details(
        *,
        pod_name: str,
        container_name: str,
        spec_image: str,
        runtime_image_id: Optional[str],
        expected_digest: Optional[str] = None,
    ) -> str:
        details = (
            f"pod={pod_name}, container={container_name}, "
            f"spec image={spec_image}, runtime imageID={runtime_image_id}"
        )
        if expected_digest is not None:
            details = f"{details}, expected digest={expected_digest}"
        return details

    @classmethod
    def _expected_historical_image_digest_for_tag(
        cls,
        image_name: str,
        tag: str,
    ) -> Optional[str]:
        canonical_image_name = canonicalize_image_name(image_name)
        if (
            canonical_image_name in OPERATOR_IMAGE_DIGEST_KEYS
            and tag == g_ts_cfg.operator_version_tag
        ):
            return None
        if (
            canonical_image_name in SERVER_ROUTER_IMAGE_DIGEST_KEYS
            and tag == g_ts_cfg.version_tag
        ):
            return None
        if cls._is_after_historical_image_digest_cutoff(tag):
            return None
        return lookup_historical_image_digest(canonical_image_name, tag)

    @classmethod
    def _is_after_historical_image_digest_cutoff(cls, release: str) -> bool:
        return cls._parse_mysql_version(
            cls._mysql_version_from_release(release)
        ) > cls._parse_mysql_version(HISTORICAL_IMAGE_DIGEST_MAX_MYSQL_VERSION)

    @classmethod
    def _should_check_raw_manifest_cluster_operator_image_tag(
        cls,
        release: str,
    ) -> bool:
        return (
            release == g_ts_cfg.operator_version_tag
            or not cls._is_after_historical_image_digest_cutoff(release)
        )

    def _assert_pod_container_image_identity(
        self,
        *,
        pod: dict,
        container_name: str,
        spec_container_key: str,
        status_container_key: str,
        expected_tag: Optional[str],
        expected_image_name_keys,
        optional: bool = False,
    ) -> None:
        pod_metadata = pod.get("metadata", {})
        pod_name = pod_metadata.get("name", "<unknown>")
        pod_namespace = pod_metadata.get("namespace")
        spec_containers = pod.get("spec", {}).get(spec_container_key, [])
        status_containers = pod.get("status", {}).get(status_container_key, [])
        spec_container = get_named_container(spec_containers, container_name)
        if spec_container is None:
            if optional:
                return
            self.fail(
                f"Expected pod {pod_name} to have {spec_container_key} "
                f"entry {container_name}, found "
                f"{[container.get('name') for container in spec_containers]}"
            )
        status_container = get_named_container(status_containers, container_name)
        self.assertIsNotNone(
            status_container,
            msg=(
                f"Expected pod {pod_name} to have {status_container_key} "
                f"entry {container_name}, found "
                f"{[container.get('name') for container in status_containers]}"
            ),
        )
        assert status_container is not None
        spec_image = spec_container.get("image", "")
        self.assertTrue(
            spec_image,
            msg=f"Expected image for {pod_name}/{container_name}, got {spec_container}",
        )
        image_name, tag = self._split_image_reference(spec_image)
        canonical_image_name = canonicalize_image_name(image_name)
        runtime_image_id = status_container.get("imageID")
        if not runtime_image_id:
            runtime_image_id = self._wait_for_pod_container_runtime_image_id(
                pod_namespace=pod_namespace,
                pod_name=pod_name,
                container_name=container_name,
                status_container_key=status_container_key,
            )
            if runtime_image_id:
                status_container["imageID"] = runtime_image_id
        identity_details = self._format_image_identity_details(
            pod_name=pod_name,
            container_name=container_name,
            spec_image=spec_image,
            runtime_image_id=runtime_image_id,
        )
        self.assertIn(
            canonical_image_name,
            set(expected_image_name_keys),
            msg=(
                f"Unexpected image name key {canonical_image_name}. "
                f"Expected one of {sorted(set(expected_image_name_keys))}. "
                f"{identity_details}"
            ),
        )
        if expected_tag is not None:
            self.assertEqual(
                tag,
                expected_tag,
                msg=(
                    f"Unexpected image tag {tag}; expected {expected_tag}. "
                    f"{identity_details}"
                ),
            )
        self.assertTrue(
            runtime_image_id,
            msg=f"Missing runtime imageID for {identity_details}",
        )

        try:
            expected_digest = self._expected_historical_image_digest_for_tag(
                canonical_image_name,
                tag,
            )
        except KnownMissingImageArtifactError as exc:
            self.fail(
                f"Historical image digest check blocked by known missing OCR artifact. "
                f"{identity_details}. {exc}"
            )
        except HistoricalImageDigestCoverageError as exc:
            self.fail(
                f"Missing embedded historical image digest coverage. "
                f"{identity_details}. {exc}"
            )

        if expected_digest is None:
            return

        runtime_digest = self._extract_runtime_image_digest(runtime_image_id)
        expected_identity_details = self._format_image_identity_details(
            pod_name=pod_name,
            container_name=container_name,
            spec_image=spec_image,
            runtime_image_id=runtime_image_id,
            expected_digest=expected_digest,
        )
        self.assertIsNotNone(
            runtime_digest,
            msg=f"Could not parse runtime digest from imageID. {expected_identity_details}",
        )
        self.assertEqual(
            runtime_digest,
            expected_digest,
            msg=(
                f"Unexpected runtime digest {runtime_digest}. "
                f"{expected_identity_details}"
            ),
        )

    @staticmethod
    def _get_pod_container_runtime_image_id(
        pod: dict,
        *,
        container_name: str,
        status_container_key: str,
    ) -> Optional[str]:
        status_container = get_named_container(
            pod.get("status", {}).get(status_container_key, []),
            container_name,
        )
        if status_container is None:
            return None
        return status_container.get("imageID")

    def _wait_for_pod_container_runtime_image_id(
        self,
        *,
        pod_namespace: Optional[str],
        pod_name: str,
        container_name: str,
        status_container_key: str,
        timeout: int = 60,
        delay: int = 2,
    ) -> Optional[str]:
        if not pod_namespace or not pod_name or pod_name == "<unknown>":
            return None

        deadline = time.time() + timeout
        while True:
            refreshed_pod = kutil.get_po(
                pod_namespace,
                pod_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            if refreshed_pod:
                runtime_image_id = self._get_pod_container_runtime_image_id(
                    refreshed_pod,
                    container_name=container_name,
                    status_container_key=status_container_key,
                )
                if runtime_image_id:
                    return runtime_image_id

            if time.time() >= deadline:
                return None
            time.sleep(delay)

    def _assert_helm_operator_image_tag(
        self,
        pod: dict,
        expected_release: str,
    ) -> None:
        self._assert_pod_container_image_identity(
            pod=pod,
            container_name="mysql-operator",
            spec_container_key="containers",
            status_container_key="containerStatuses",
            expected_tag=expected_release,
            expected_image_name_keys=OPERATOR_IMAGE_DIGEST_KEYS,
        )

    def _assert_helm_cluster_runtime_image_identities(
        self,
        *,
        namespace: str,
        cluster_name: str,
        server_instances: int,
        router_instances: int,
        expected_release: str,
        expected_operator_release: Optional[str] = None,
        check_operator_image_tag: bool = True,
    ) -> None:
        expected_mysql_version = self._mysql_version_from_release(
            expected_release
        )
        expected_operator_tag = (
            expected_operator_release
            if expected_operator_release is not None
            else expected_release
        )
        if not check_operator_image_tag:
            expected_operator_tag = None

        for instance in range(server_instances):
            pod = kutil.get_po(namespace, f"{cluster_name}-{instance}")
            self._assert_pod_container_image_identity(
                pod=pod,
                container_name="fixdatadir",
                spec_container_key="initContainers",
                status_container_key="initContainerStatuses",
                expected_tag=expected_operator_tag,
                expected_image_name_keys=OPERATOR_IMAGE_DIGEST_KEYS,
            )
            self._assert_pod_container_image_identity(
                pod=pod,
                container_name="initconf",
                spec_container_key="initContainers",
                status_container_key="initContainerStatuses",
                expected_tag=expected_operator_tag,
                expected_image_name_keys=OPERATOR_IMAGE_DIGEST_KEYS,
            )
            self._assert_pod_container_image_identity(
                pod=pod,
                container_name="initmysql",
                spec_container_key="initContainers",
                status_container_key="initContainerStatuses",
                expected_tag=expected_mysql_version,
                expected_image_name_keys=SERVER_IMAGE_DIGEST_KEYS,
                optional=True,
            )
            self._assert_pod_container_image_identity(
                pod=pod,
                container_name="mysql",
                spec_container_key="containers",
                status_container_key="containerStatuses",
                expected_tag=expected_mysql_version,
                expected_image_name_keys=SERVER_IMAGE_DIGEST_KEYS,
            )
            self._assert_pod_container_image_identity(
                pod=pod,
                container_name="sidecar",
                spec_container_key="containers",
                status_container_key="containerStatuses",
                expected_tag=expected_operator_tag,
                expected_image_name_keys=OPERATOR_IMAGE_DIGEST_KEYS,
            )

        if not router_instances:
            return

        router_pods = sorted(
            kutil.ls_po(namespace, pattern=f"{cluster_name}-router-.*"),
            key=lambda pod: pod["NAME"],
        )
        self.assertEqual(
            len(router_pods),
            router_instances,
            msg=(
                f"Expected {router_instances} router pods for "
                f"{namespace}/{cluster_name}, got {len(router_pods)}"
            ),
        )
        for router_pod in router_pods:
            pod = kutil.get_po(namespace, router_pod["NAME"])
            self._assert_pod_container_image_identity(
                pod=pod,
                container_name="router",
                spec_container_key="containers",
                status_container_key="containerStatuses",
                expected_tag=expected_mysql_version,
                expected_image_name_keys=ROUTER_IMAGE_DIGEST_KEYS,
            )

    def _resolve_resident_helm_chart_source_overrides(
        self,
        *,
        resident_operator_deployment: Optional[dict] = None,
        include_deployment_topology: bool = False,
    ) -> dict:
        source_helm_options = resolve_install_with_helm_options(
            namespace=self.operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment=(
                resident_operator_deployment
                if resident_operator_deployment is not None
                else kutil.get_deploy(
                    self.operator_ns,
                    self.operator_deploy_name,
                )
            ),
        )
        overrides = {
            "image": {
                "registry": source_helm_options.operator_registry,
                "repository": source_helm_options.operator_repository,
            },
            "envs": {
                "imagesDefaultRegistry": source_helm_options.operator_registry,
                "imagesDefaultRepository": source_helm_options.operator_repository,
            },
        }
        if include_deployment_topology:
            overrides["deployment"] = {
                "namespaces": source_helm_options.operator_namespaces_list,
                "standalone": source_helm_options.standalone,
            }
        return overrides

    @staticmethod
    def _helm_operator_overrides_change_topology(
        operator_overrides: Optional[dict],
    ) -> bool:
        deployment_overrides = (
            operator_overrides.get("deployment", {})
            if isinstance(operator_overrides, dict)
            else {}
        )
        return any(
            key in deployment_overrides
            for key in ("namespaces", "standalone")
        )

    def _apply_helm_operator_with_synced_default_clusterroles(
        self,
        options: HelmOperatorInstallOptions,
        *,
        install_if_missing: bool,
        wait_for_deployment_name: Optional[str] = None,
    ):
        original_wait_for_helm = options.wait_for_helm
        original_replicas = options.deployment_replicas
        original_operator_replicas = options.operator_values.get("replicas")
        had_explicit_replicas = "replicas" in options.explicit_operator_values
        original_explicit_replicas = options.explicit_operator_values.get("replicas")
        options.wait_for_helm = False
        options.operator_values["replicas"] = 0
        options.explicit_operator_values["replicas"] = 0
        try:
            if install_if_missing:
                result = install_with_helm(
                    options.namespace,
                    resolved_options=options,
                )
            else:
                result = upgrade_with_helm(
                    options.namespace,
                    resolved_options=options,
                )
            _sync_default_operator_clusterroles_from_manifest(
                create_missing_only=(
                    options.app_version != g_ts_cfg.operator_version_tag
                ),
                helm_release_name=options.release_name,
                helm_release_namespace=options.namespace,
            )
            deployment_name = wait_for_deployment_name or options.deployment_name
            kutil.patch(
                options.namespace,
                "deploy",
                deployment_name,
                {"spec": {"replicas": original_replicas}},
                type="merge",
                data_as_type="json",
                field_manager=HELM_FIELD_MANAGER,
            )
            if wait_for_deployment_name is not None:
                kutil.wait_deploy(
                    options.namespace,
                    wait_for_deployment_name,
                    timeout=300,
                )
            return result
        finally:
            options.wait_for_helm = original_wait_for_helm
            if original_operator_replicas is None:
                options.operator_values.pop("replicas", None)
            else:
                options.operator_values["replicas"] = original_operator_replicas
            if had_explicit_replicas:
                options.explicit_operator_values["replicas"] = original_explicit_replicas
            else:
                options.explicit_operator_values.pop("replicas", None)

    def _print_operator_log_from_candidates(
        self,
        *,
        namespace: str,
        deployment_names: list[str],
    ) -> None:
        for deployment_name in deployment_names:
            if not deployment_name:
                continue
            if kutil.get_deploy(namespace, deployment_name, check=False) is None:
                continue
            try:
                print_operator_log(namespace, deployment_name)
                return
            except Exception:
                continue

    @staticmethod
    def _pod_container_has_previous_logs(
        pod: dict,
        container_name: str,
    ) -> bool:
        pod_status = pod.get("status", {})
        for status_key in ("initContainerStatuses", "containerStatuses"):
            for status in pod_status.get(status_key, []):
                if status.get("name") != container_name:
                    continue
                if status.get("restartCount", 0) > 0:
                    return True
                if status.get("lastState", {}).get("terminated"):
                    return True
                return False
        return False

    @staticmethod
    def _pod_container_names(pod: dict) -> list[str]:
        container_names: list[str] = []
        for container_key in ("initContainers", "containers"):
            for container in pod.get("spec", {}).get(container_key, []):
                container_name = container.get("name")
                if container_name and container_name not in container_names:
                    container_names.append(container_name)
        return container_names

    def _print_helm_cluster_pod_diagnostics(
        self,
        *,
        namespace: str,
        cluster_name: str,
    ) -> None:
        print(f"==== Cluster pod diagnostics for {namespace}/{cluster_name} ====")

        try:
            pods = sorted(
                kutil.ls_po(namespace, pattern=f"{cluster_name}.*"),
                key=lambda pod: pod["NAME"],
            )
        except Exception as exc:
            print(f"Failed to list pods for {namespace}/{cluster_name}: {exc}")
            return

        if not pods:
            print(f"No pods found for {namespace}/{cluster_name}")
            return

        for pod_row in pods:
            pod_name = pod_row["NAME"]
            pod = kutil.get_po(
                namespace,
                pod_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            if pod:
                print(f"==== image identities for {namespace}/{pod_name} ====")
                for container_key, status_key in (
                    ("initContainers", "initContainerStatuses"),
                    ("containers", "containerStatuses"),
                ):
                    for container in pod.get("spec", {}).get(container_key, []):
                        container_name = container.get("name")
                        status = get_named_container(
                            pod.get("status", {}).get(status_key, []),
                            container_name,
                        )
                        runtime_image_id = None
                        if status is not None:
                            runtime_image_id = status.get("imageID")
                        print(
                            f"{container_key}.{container_name}: "
                            f"image={container.get('image')} "
                            f"imageID={runtime_image_id}"
                        )
            print(f"==== describe pod {namespace}/{pod_name} ====")
            try:
                print(
                    kutil.describe_po(
                        namespace,
                        pod_name,
                        cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                    )
                )
            except Exception as exc:
                print(f"Failed to describe pod {namespace}/{pod_name}: {exc}")

            if not pod:
                print(f"Pod {namespace}/{pod_name} no longer exists")
                continue

            for container_name in self._pod_container_names(pod):
                print(
                    f"==== logs pod {namespace}/{pod_name} container {container_name} ===="
                )
                try:
                    print(
                        kutil.logs(
                            namespace,
                            [pod_name, container_name],
                            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                        )
                    )
                except Exception as exc:
                    print(
                        f"Failed to fetch current logs for "
                        f"{namespace}/{pod_name} container {container_name}: {exc}"
                    )

                if not self._pod_container_has_previous_logs(pod, container_name):
                    continue

                print(
                    f"==== previous logs pod {namespace}/{pod_name} "
                    f"container {container_name} ===="
                )
                try:
                    print(
                        kutil.logs(
                            namespace,
                            [pod_name, container_name],
                            prev=True,
                            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                        )
                    )
                except Exception as exc:
                    print(
                        f"Failed to fetch previous logs for "
                        f"{namespace}/{pod_name} container {container_name}: {exc}"
                    )

    @staticmethod
    def _merge_helm_values(
        base_values: Optional[dict],
        override_values: Optional[dict],
    ) -> dict:
        if base_values is None:
            merged_values = {}
        elif isinstance(base_values, dict):
            merged_values = copy.deepcopy(base_values)
        else:
            raise ValueError("base_values must be a dict")

        if not override_values:
            return merged_values
        if not isinstance(override_values, dict):
            raise ValueError("override_values must be a dict")

        for key, override_value in override_values.items():
            base_value = merged_values.get(key)
            if isinstance(base_value, dict) and isinstance(override_value, dict):
                merged_values[key] = (
                    OperatorSingleAndMultipleBaseTest._merge_helm_values(
                        base_value,
                        override_value,
                    )
                )
            else:
                merged_values[key] = copy.deepcopy(override_value)
        return merged_values

    def _build_helm_cluster_tracker(
        self,
        namespace: str,
        cluster_name: str,
        *,
        server_instances: Optional[int] = None,
        router_instances: Optional[int] = None,
        app_version: Optional[str] = None,
        cluster_values: Optional[dict] = None,
    ) -> _HelmClusterInstallTracker:
        target_app_version = app_version or g_ts_cfg.operator_version_tag
        target_server_instances = (
            self.cluster_size if server_instances is None else server_instances
        )
        target_router_instances = (
            self.routers_count if router_instances is None else router_instances
        )
        merged_cluster_values = self._merge_helm_values(
            {
                "serverInstances": target_server_instances,
                "router": {
                    "instances": target_router_instances,
                },
                "credentials": {
                    "root": {
                        "user": "root",
                        "password": "sakila",
                        "host": "%",
                    },
                },
                "tls": {
                    "useSelfSigned": True,
                },
                "podSpec": {
                    "terminationGracePeriodSeconds": 5,
                },
            },
            cluster_values,
        )

        cluster_options = HelmClusterInstallOptions(
            namespace=namespace,
            app_version=target_app_version,
            release_name=cluster_name,
            kube_context=g_ts_cfg.k8s_context,
            cluster_values=merged_cluster_values,
        )
        return _HelmClusterInstallTracker(
            namespace=namespace,
            cluster_name=cluster_name,
            options=cluster_options,
        )

    def _install_helm_cluster(
        self,
        cluster_install: _HelmClusterInstallTracker,
        *,
        create_namespace: bool = True,
    ) -> None:
        if create_namespace and cluster_install.namespace not in kutil.ls_ns_ex():
            kutil.create_ns(cluster_install.namespace, labels={})

        install_cluster_with_helm(
            cluster_install.namespace,
            resolved_options=cluster_install.options,
        )

    def _cleanup_helm_cluster(
        self,
        cluster_install: _HelmClusterInstallTracker,
        *,
        delete_namespace: bool = False,
        delete_pvcs: bool = True,
    ) -> None:
        cleanup_error = None
        helm_cleanup_error = None

        try:
            uninstall_with_helm(
                release_key=get_helm_release_target(cluster_install.options),
                delete_namespace=False,
            )
        except Exception as exc:
            helm_cleanup_error = exc

        namespace_exists = cluster_install.namespace in kutil.ls_ns_ex()
        if namespace_exists:
            try:
                cluster = kutil.get(
                    cluster_install.namespace,
                    "ic",
                    cluster_install.cluster_name,
                    check=False,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
                if cluster is not None:
                    finalizers = (
                        cluster.get("metadata", {}).get("finalizers") or []
                    )
                    if finalizers:
                        kutil.patch(
                            cluster_install.namespace,
                            "ic",
                            cluster_install.cluster_name,
                            [{"op": "remove", "path": "/metadata/finalizers"}],
                            type="json",
                            data_as_type="json",
                        )
                    kutil.delete_ic(
                        cluster_install.namespace,
                        cluster_install.cluster_name,
                        timeout=60,
                        wait=False,
                    )
                self.wait_pods_gone(
                    f"{cluster_install.cluster_name}.*",
                    ns=cluster_install.namespace,
                )
                self.wait_routers_gone(
                    f"{cluster_install.cluster_name}-router.*",
                    ns=cluster_install.namespace,
                )
                self.wait_ic_gone(
                    cluster_install.cluster_name,
                    ns=cluster_install.namespace,
                )
                if delete_pvcs:
                    kutil.delete_pvc(cluster_install.namespace, None)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc

        if helm_cleanup_error is not None and cleanup_error is None:
            cluster_still_exists = (
                cluster_install.namespace in kutil.ls_ns_ex()
                and kutil.get(
                    cluster_install.namespace,
                    "ic",
                    cluster_install.cluster_name,
                    check=False,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
                is not None
            )
            if cluster_still_exists:
                cleanup_error = helm_cleanup_error

        if delete_namespace and namespace_exists:
            try:
                kutil.delete_ns(cluster_install.namespace)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc

        if cleanup_error is not None:
            raise cleanup_error

    def _cleanup_raw_cluster(
        self,
        *,
        namespace: str,
        cluster_name: str,
        delete_namespace: bool = False,
        delete_pvcs: bool = True,
    ) -> None:
        cleanup_error = None

        namespace_exists = namespace in kutil.ls_ns_ex()
        if namespace_exists:
            try:
                cluster = kutil.get(
                    namespace,
                    "ic",
                    cluster_name,
                    check=False,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
                if cluster is not None:
                    finalizers = (
                        cluster.get("metadata", {}).get("finalizers") or []
                    )
                    if finalizers:
                        kutil.patch(
                            namespace,
                            "ic",
                            cluster_name,
                            [{"op": "remove", "path": "/metadata/finalizers"}],
                            type="json",
                            data_as_type="json",
                        )
                    kutil.delete_ic(
                        namespace,
                        cluster_name,
                        timeout=60,
                        wait=False,
                    )
                self.wait_pods_gone(
                    f"{cluster_name}.*",
                    ns=namespace,
                )
                self.wait_routers_gone(
                    f"{cluster_name}-router.*",
                    ns=namespace,
                )
                self.wait_ic_gone(
                    cluster_name,
                    ns=namespace,
                )
                if delete_pvcs:
                    kutil.delete_pvc(namespace, None)
            except Exception as exc:
                cleanup_error = exc

        if delete_namespace and namespace_exists:
            try:
                kutil.delete_ns(namespace)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc

        if cleanup_error is not None:
            raise cleanup_error

    def _wait_for_helm_cluster_ready(
        self,
        *,
        namespace: str,
        cluster_name: str,
        server_instances: int,
        router_instances: int,
    ) -> None:
        self.wait_ic(
            cluster_name,
            ["PENDING", "INITIALIZING", "ONLINE"],
            ns=namespace,
        )
        for instance in range(server_instances):
            self.wait_pod(
                f"{cluster_name}-{instance}",
                "Running",
                ns=namespace,
            )
        self.wait_ic(
            cluster_name,
            "ONLINE",
            num_online=server_instances,
            ns=namespace,
        )
        if router_instances:
            self.wait_routers(
                f"{cluster_name}-router.*",
                router_instances,
                timeout=max(server_instances * 120, 300),
                wait=10,
                ns=namespace,
            )

    def _assert_helm_cluster_cr_version(
        self,
        *,
        namespace: str,
        cluster_name: str,
        expected_version: str,
    ) -> None:
        actual_version = (
            kutil.get_ic(namespace, cluster_name)
            .get("spec", {})
            .get("version")
        )
        self.assertEqual(
            actual_version,
            expected_version,
            msg=(
                f"Expected InnoDBCluster {namespace}/{cluster_name} spec.version "
                f"to be {expected_version}, got {actual_version}"
            ),
        )

    @staticmethod
    def _get_helm_cluster_operator_release(
        *,
        namespace: str,
        cluster_name: str,
        fallback_release: str,
    ) -> str:
        cluster = kutil.get_ic(namespace, cluster_name)
        operator_release = (
            cluster.get("metadata", {})
            .get("annotations", {})
            .get("mysql.oracle.com/mysql-operator-version")
        )
        return operator_release or fallback_release

    @staticmethod
    def _mysql_version_from_release(release: str) -> str:
        return release.split("-", 1)[0]

    @staticmethod
    def _parse_mysql_version(version_text: str) -> tuple[int, ...]:
        return tuple(int(part) for part in str(version_text).split("."))

    @classmethod
    def _mysql_version_to_upgrade_digits(cls, version_text: str) -> str:
        parts = list(cls._parse_mysql_version(version_text))
        while len(parts) < 3:
            parts.append(0)
        major, minor, patch = parts[:3]
        return f"{major}{minor:02d}{patch:02d}"

    @classmethod
    def _release_creates_legacy_switchover_rbac(cls, release: str) -> bool:
        if release == g_ts_cfg.operator_version_tag:
            return False
        return cls._parse_mysql_version(
            cls._mysql_version_from_release(release)
        ) >= cls._parse_mysql_version(LEGACY_SWITCHOVER_RBAC_INTRODUCED_VERSION)

    @staticmethod
    def _release_uses_current_switchover_rbac(release: str) -> bool:
        return release == g_ts_cfg.operator_version_tag

    @classmethod
    def _expected_namespace_switchover_rbac_state(
        cls,
        *,
        operator_release: str,
        cluster_releases: list[str],
    ) -> tuple[bool, bool]:
        expect_legacy_present = (
            getattr(
                cls,
                "expect_legacy_switchover_objects_before_current",
                True,
            )
            and not cls._release_uses_current_switchover_rbac(operator_release)
            and any(
                cls._release_creates_legacy_switchover_rbac(release)
                for release in cluster_releases
            )
        )
        expect_current_present = cls._release_uses_current_switchover_rbac(
            operator_release
        )
        return expect_legacy_present, expect_current_present

    def _assert_helm_clusters_cr_version(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        expected_version: str,
    ) -> None:
        for cluster_name in cluster_names:
            self._assert_helm_cluster_cr_version(
                namespace=namespace,
                cluster_name=cluster_name,
                expected_version=expected_version,
            )

    def _get_cluster_current_switchover_rbac_state(
        self,
        *,
        namespace: str,
        cluster_name: str,
    ) -> dict[str, Optional[dict]]:
        return {
            "sa": kutil.get_sa(
                namespace,
                get_cluster_switchover_service_account_name(cluster_name),
                check=False,
            ),
            "rb": kutil.get(
                namespace,
                "rolebinding",
                get_cluster_switchover_role_binding_name(cluster_name),
                check=False,
            ),
        }

    def _get_namespace_switchover_rbac_state(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
    ) -> dict[str, object]:
        return {
            "legacy_sa": kutil.get_sa(
                namespace,
                LEGACY_SWITCHOVER_SERVICE_ACCOUNT_NAME,
                check=False,
            ),
            "legacy_rb": kutil.get(
                namespace,
                "rolebinding",
                LEGACY_SWITCHOVER_ROLE_BINDING_NAME,
                check=False,
            ),
            "clusters": {
                cluster_name: self._get_cluster_current_switchover_rbac_state(
                    namespace=namespace,
                    cluster_name=cluster_name,
                )
                for cluster_name in cluster_names
            },
        }

    def _wait_for_namespace_switchover_rbac_state(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        operator_release: str,
        cluster_releases: list[str],
        timeout: int = 300,
    ) -> dict[str, object]:
        expect_legacy_present, expect_current_present = (
            self._expected_namespace_switchover_rbac_state(
                operator_release=operator_release,
                cluster_releases=cluster_releases,
            )
        )

        def _get_matching_state() -> Optional[dict[str, object]]:
            state = self._get_namespace_switchover_rbac_state(
                namespace=namespace,
                cluster_names=cluster_names,
            )
            if (state["legacy_sa"] is not None) != expect_legacy_present:
                return None
            if (state["legacy_rb"] is not None) != expect_legacy_present:
                return None

            cluster_states = state["clusters"]
            assert isinstance(cluster_states, dict)
            for cluster_name, cluster_state in cluster_states.items():
                assert isinstance(cluster_state, dict)
                if (cluster_state["sa"] is not None) != expect_current_present:
                    return None
                if (cluster_state["rb"] is not None) != expect_current_present:
                    return None
                if not expect_current_present:
                    continue

                try:
                    self._assert_cluster_current_switchover_rbac_state(
                        namespace=namespace,
                        cluster_name=cluster_name,
                        cluster_state=cluster_state,
                    )
                except AssertionError:
                    return None
            return state

        return self.wait(
            _get_matching_state,
            timeout=timeout,
            delay=5,
        )

    def _wait_for_helm_clusters_ready(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        server_instances: int,
        router_instances: int,
    ) -> None:
        for cluster_name in cluster_names:
            self._wait_for_helm_cluster_ready(
                namespace=namespace,
                cluster_name=cluster_name,
                server_instances=server_instances,
                router_instances=router_instances,
            )

    def _assert_manifest_owned_by_cluster(
        self,
        *,
        manifest: dict,
        cluster_manifest: dict,
    ) -> None:
        owner_references = manifest.get("metadata", {}).get("ownerReferences", [])
        self.assertTrue(
            owner_references,
            msg=f"Expected ownerReferences for {manifest.get('metadata', {}).get('name')}, got {manifest}",
        )
        self.assertTrue(
            any(
                owner.get("apiVersion") == cluster_manifest.get("apiVersion")
                and owner.get("kind") == cluster_manifest.get("kind")
                and owner.get("name") == cluster_manifest.get("metadata", {}).get("name")
                and owner.get("uid") == cluster_manifest.get("metadata", {}).get("uid")
                for owner in owner_references
            ),
            msg=(
                f"Expected {manifest.get('metadata', {}).get('name')} to be owned by "
                f"{cluster_manifest.get('metadata', {}).get('name')}, got {owner_references}"
            ),
        )

    def _assert_cluster_current_switchover_rbac_state(
        self,
        *,
        namespace: str,
        cluster_name: str,
        cluster_state: Optional[dict[str, Optional[dict]]] = None,
        operator_ns: Optional[str] = None,
        operator_name: Optional[str] = None,
    ) -> None:
        state = cluster_state or self._get_cluster_current_switchover_rbac_state(
            namespace=namespace,
            cluster_name=cluster_name,
        )
        current_sa_name = get_cluster_switchover_service_account_name(cluster_name)
        current_rb_name = get_cluster_switchover_role_binding_name(cluster_name)

        current_sa = state["sa"]
        current_rb = state["rb"]
        assert current_sa is not None
        assert current_rb is not None

        self.assertEqual(current_sa.get("metadata", {}).get("name"), current_sa_name)
        self.assertEqual(current_rb.get("metadata", {}).get("name"), current_rb_name)

        subjects = current_rb.get("subjects", [])
        self.assertTrue(
            subjects,
            msg=f"Expected subjects on RoleBinding {namespace}/{current_rb_name}: {current_rb}",
        )
        self.assertEqual(subjects[0].get("kind"), "ServiceAccount")
        self.assertEqual(subjects[0].get("name"), current_sa_name)

        role_ref = current_rb.get("roleRef", {})
        self.assertEqual(role_ref.get("kind"), "ClusterRole")
        self.assertEqual(
            role_ref.get("name"),
            get_switchover_role_name(
                operator_ns or self.operator_ns,
                operator_name or self.operator_deploy_name,
            ),
        )
        self.assertEqual(role_ref.get("apiGroup"), "rbac.authorization.k8s.io")

        cluster_manifest = kutil.get_ic(namespace, cluster_name)
        self._assert_manifest_owned_by_cluster(
            manifest=current_sa,
            cluster_manifest=cluster_manifest,
        )
        self._assert_manifest_owned_by_cluster(
            manifest=current_rb,
            cluster_manifest=cluster_manifest,
        )

    @staticmethod
    def _get_image_pull_secret_names(manifest: Optional[dict]) -> list[str]:
        return [
            image_pull_secret["name"]
            for image_pull_secret in (manifest or {}).get("imagePullSecrets", [])
            if image_pull_secret.get("name")
        ]

    @staticmethod
    def _build_foreign_owner_references(owner_tag: str) -> list[dict]:
        return [
            {
                "apiVersion": "mysql.oracle.com/v2",
                "kind": "InnoDBCluster",
                "name": f"foreign-{owner_tag}",
                "uid": str(uuid.uuid5(uuid.NAMESPACE_DNS, owner_tag)),
            }
        ]

    def _build_drifted_cluster_current_switchover_service_account_manifest(
        self,
        *,
        namespace: str,
        cluster_name: str,
        image_pull_secret_names: list[str],
        owner_tag: str,
    ) -> dict:
        manifest = {
            "apiVersion": "v1",
            "kind": "ServiceAccount",
            "metadata": {
                "name": get_cluster_switchover_service_account_name(cluster_name),
                "namespace": namespace,
                "ownerReferences": self._build_foreign_owner_references(owner_tag),
            },
        }
        if image_pull_secret_names:
            manifest["imagePullSecrets"] = [
                {"name": image_pull_secret_name}
                for image_pull_secret_name in image_pull_secret_names
            ]
        return manifest

    def _build_drifted_cluster_current_switchover_role_binding_manifest(
        self,
        *,
        namespace: str,
        cluster_name: str,
        role_name: str,
        subject_name: str,
        owner_tag: str,
        annotations: Optional[dict[str, str]] = None,
    ) -> dict:
        metadata = {
            "name": get_cluster_switchover_role_binding_name(cluster_name),
            "namespace": namespace,
            "ownerReferences": self._build_foreign_owner_references(owner_tag),
        }
        if annotations:
            metadata["annotations"] = annotations

        return {
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": metadata,
            "subjects": [
                {
                    "kind": "ServiceAccount",
                    "name": subject_name,
                }
            ],
            "roleRef": {
                "apiGroup": "rbac.authorization.k8s.io",
                "kind": "ClusterRole",
                "name": role_name,
            },
        }

    def _apply_cluster_current_switchover_rbac_manifests(
        self,
        *,
        namespace: str,
        service_account_manifest: dict,
        role_binding_manifest: dict,
    ) -> None:
        role_binding_name = role_binding_manifest["metadata"]["name"]
        desired_role_ref = role_binding_manifest.get("roleRef", {})
        existing_role_binding = kutil.get(
            namespace,
            "rolebinding",
            role_binding_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        if (
            existing_role_binding is not None
            and existing_role_binding.get("roleRef", {}) != desired_role_ref
        ):
            kutil.delete_rolebinding(namespace, role_binding_name)

        kutil.apply(
            namespace,
            "---\n".join(
                [
                    yaml.safe_dump(service_account_manifest),
                    yaml.safe_dump(role_binding_manifest),
                ]
            ),
        )

    def _wait_for_cluster_current_switchover_rbac_repaired(
        self,
        *,
        namespace: str,
        cluster_name: str,
        expected_image_pull_secret_names: Optional[list[str]] = None,
        expected_service_account_uid: Optional[str] = None,
        expected_role_binding_uid: Optional[str] = None,
        replaced_role_binding_uid: Optional[str] = None,
        operator_ns: Optional[str] = None,
        operator_name: Optional[str] = None,
        timeout_diagnostics: Optional[Callable[[], None]] = None,
        timeout: int = 300,
    ) -> dict[str, Optional[dict]]:
        def _get_matching_state() -> Optional[dict[str, Optional[dict]]]:
            state = self._get_cluster_current_switchover_rbac_state(
                namespace=namespace,
                cluster_name=cluster_name,
            )
            try:
                self._assert_cluster_current_switchover_rbac_state(
                    namespace=namespace,
                    cluster_name=cluster_name,
                    cluster_state=state,
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                )
            except AssertionError:
                return None

            current_sa = state["sa"]
            current_rb = state["rb"]
            assert current_sa is not None
            assert current_rb is not None

            if expected_image_pull_secret_names is not None:
                if (
                    self._get_image_pull_secret_names(current_sa)
                    != expected_image_pull_secret_names
                ):
                    return None

            if expected_service_account_uid is not None:
                if (
                    current_sa.get("metadata", {}).get("uid")
                    != expected_service_account_uid
                ):
                    return None

            if expected_role_binding_uid is not None:
                if (
                    current_rb.get("metadata", {}).get("uid")
                    != expected_role_binding_uid
                ):
                    return None

            if replaced_role_binding_uid is not None:
                if (
                    current_rb.get("metadata", {}).get("uid")
                    == replaced_role_binding_uid
                ):
                    return None

            return state

        return self.wait(
            _get_matching_state,
            timeout=timeout,
            delay=5,
            timeout_diagnostics=timeout_diagnostics,
        )

    def _assert_namespace_switchover_rbac_state(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        operator_release: str,
        cluster_releases: list[str],
    ) -> None:
        expect_legacy_present, expect_current_present = (
            self._expected_namespace_switchover_rbac_state(
                operator_release=operator_release,
                cluster_releases=cluster_releases,
            )
        )
        state = self._get_namespace_switchover_rbac_state(
            namespace=namespace,
            cluster_names=cluster_names,
        )

        legacy_sa_count = int(state["legacy_sa"] is not None)
        legacy_rb_count = int(state["legacy_rb"] is not None)
        self.assertEqual(
            legacy_sa_count,
            int(expect_legacy_present),
            msg=(
                f"Expected {int(expect_legacy_present)} legacy switchover ServiceAccount "
                f"in {namespace}, got {legacy_sa_count}"
            ),
        )
        self.assertEqual(
            legacy_rb_count,
            int(expect_legacy_present),
            msg=(
                f"Expected {int(expect_legacy_present)} legacy switchover RoleBinding "
                f"in {namespace}, got {legacy_rb_count}"
            ),
        )

        cluster_states = state["clusters"]
        assert isinstance(cluster_states, dict)
        current_sa_count = 0
        current_rb_count = 0
        for cluster_name in cluster_names:
            cluster_state = cluster_states[cluster_name]
            assert isinstance(cluster_state, dict)
            current_sa = cluster_state["sa"]
            current_rb = cluster_state["rb"]
            current_sa_count += int(current_sa is not None)
            current_rb_count += int(current_rb is not None)

            if not expect_current_present:
                self.assertIsNone(
                    current_sa,
                    msg=(
                        f"Did not expect current switchover ServiceAccount "
                        f"{namespace}/{get_cluster_switchover_service_account_name(cluster_name)}"
                    ),
                )
                self.assertIsNone(
                    current_rb,
                    msg=(
                        f"Did not expect current switchover RoleBinding "
                        f"{namespace}/{get_cluster_switchover_role_binding_name(cluster_name)}"
                    ),
                )
                continue

            self._assert_cluster_current_switchover_rbac_state(
                namespace=namespace,
                cluster_name=cluster_name,
                cluster_state=cluster_state,
            )

        expected_current_count = len(cluster_names) if expect_current_present else 0
        self.assertEqual(
            current_sa_count,
            expected_current_count,
            msg=(
                f"Expected {expected_current_count} current switchover ServiceAccounts "
                f"in {namespace}, got {current_sa_count}"
            ),
        )
        self.assertEqual(
            current_rb_count,
            expected_current_count,
            msg=(
                f"Expected {expected_current_count} current switchover RoleBindings "
                f"in {namespace}, got {current_rb_count}"
            ),
        )

    def _run_helm_operator_upgrade_cycle(
        self,
        *,
        server_instances: int,
        router_instances: int,
        upgraded_operator_overrides: Optional[dict] = None,
        upgraded_deployment_name: str = "mysql-operator",
        expect_operator_upgrade_failure: bool = False,
        expected_operator_upgrade_error_fragment: Optional[str] = None,
    ) -> None:
        cluster_name = f"{self.cluster_name}-s{server_instances}-r{router_instances}"
        previous_release = get_previous_operator_chart_release(
            current_app_version=g_ts_cfg.operator_version_tag,
        )
        previous_mysql_version = previous_release.split("-", 1)[0]
        current_mysql_version = g_ts_cfg.operator_version_tag.split("-", 1)[0]

        resident_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        chart_source_overrides = self._resolve_resident_helm_chart_source_overrides(
            resident_operator_deployment=resident_operator_deployment,
            include_deployment_topology=(
                not self._helm_operator_overrides_change_topology(
                    upgraded_operator_overrides,
                )
            ),
        )
        upgrade_chart_source_overrides = self._merge_helm_values(
            chart_source_overrides,
            upgraded_operator_overrides,
        )
        cluster_values = {
            "credentials": {
                "root": {
                    "user": "root",
                    "password": "sakila",
                    "host": "%",
                },
            },
            "tls": {
                "useSelfSigned": True,
            },
            "podSpec": {
                "terminationGracePeriodSeconds": 5,
            },
        }
        install_options = HelmOperatorInstallOptions(
            namespace=self.operator_ns,
            app_version=previous_release,
            release_name=self.operator_deploy_name,
            kube_context=g_ts_cfg.k8s_context,
            helm_package="mysql-operator",
            operator_values=chart_source_overrides,
            use_chart_defaults=True,
        )
        upgrade_options = HelmOperatorInstallOptions(
            namespace=self.operator_ns,
            app_version=g_ts_cfg.operator_version_tag,
            release_name=self.operator_deploy_name,
            kube_context=g_ts_cfg.k8s_context,
            helm_package="mysql-operator",
            operator_values=upgrade_chart_source_overrides,
            use_chart_defaults=True,
        )
        self.assertEqual(
            upgrade_options.deployment_name,
            upgraded_deployment_name,
            msg=(
                "upgrade operator overrides must resolve to the expected "
                f"deployment.name {upgraded_deployment_name}"
            ),
        )

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        install_result = None
        upgrade_result = None
        active_cluster_install = None
        test_error = None

        try:
            install_result = self._apply_helm_operator_with_synced_default_clusterroles(
                install_options,
                install_if_missing=True,
            )
            previous_operator_pod = self._wait_for_helm_operator_pod_rollout(
                namespace=install_options.namespace,
                deployment_name=install_options.deployment_name,
            )
            self._assert_helm_operator_image_tag(
                previous_operator_pod,
                previous_release,
            )

            active_cluster_install = self._build_helm_cluster_tracker(
                self.ns,
                cluster_name,
                server_instances=server_instances,
                router_instances=router_instances,
                app_version=previous_release,
                cluster_values=cluster_values,
            )
            self.assertEqual(
                active_cluster_install.options.app_version,
                previous_release,
            )
            self._install_helm_cluster(active_cluster_install)
            self._wait_for_helm_cluster_ready(
                namespace=self.ns,
                cluster_name=cluster_name,
                server_instances=server_instances,
                router_instances=router_instances,
            )
            active_cluster_operator_release = self._get_helm_cluster_operator_release(
                namespace=self.ns,
                cluster_name=cluster_name,
                fallback_release=previous_release,
            )
            self._assert_helm_cluster_runtime_image_identities(
                namespace=self.ns,
                cluster_name=cluster_name,
                server_instances=server_instances,
                router_instances=router_instances,
                expected_release=previous_release,
                expected_operator_release=active_cluster_operator_release,
            )
            self._assert_helm_cluster_cr_version(
                namespace=self.ns,
                cluster_name=cluster_name,
                expected_version=previous_mysql_version,
            )

            old_operator_pod_name = previous_operator_pod["metadata"]["name"]
            if expect_operator_upgrade_failure:
                upgrade_error = None
                try:
                    upgrade_with_helm(
                        self.operator_ns,
                        resolved_options=upgrade_options,
                    )
                except Exception as exc:
                    upgrade_error = exc

                self.assertIsNotNone(
                    upgrade_error,
                    msg=(
                        "Helm operator upgrade with changed deployment.name "
                        "unexpectedly succeeded"
                    ),
                )
                upgrade_error_output = (
                    getattr(upgrade_error, "stdout", None)
                    or getattr(upgrade_error, "output", None)
                    or str(upgrade_error)
                )
                if expected_operator_upgrade_error_fragment is not None:
                    self.assertIn(
                        expected_operator_upgrade_error_fragment,
                        upgrade_error_output,
                    )
                assert_failed_helm_operator_upgrade_target_state(
                    self,
                    installed_namespace=install_options.namespace,
                    installed_deployment_name=install_options.deployment_name,
                    attempted_namespace=upgrade_options.namespace,
                    attempted_deployment_name=upgrade_options.deployment_name,
                )
                current_operator_pod = self._wait_for_helm_operator_pod_rollout(
                    namespace=install_options.namespace,
                    deployment_name=install_options.deployment_name,
                )
                self._assert_helm_operator_image_tag(
                    current_operator_pod,
                    previous_release,
                )
                self._assert_helm_cluster_runtime_image_identities(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                    expected_release=previous_release,
                    expected_operator_release=active_cluster_operator_release,
                )
                self._assert_helm_cluster_cr_version(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    expected_version=previous_mysql_version,
                )
                self.wait_ic(
                    cluster_name,
                    "ONLINE",
                    num_online=server_instances,
                    ns=self.ns,
                )
            else:
                upgrade_result = self._apply_helm_operator_with_synced_default_clusterroles(
                    upgrade_options,
                    install_if_missing=False,
                )
                current_operator_pod = self._wait_for_helm_operator_pod_rollout(
                    namespace=upgrade_options.namespace,
                    deployment_name=upgrade_options.deployment_name,
                    previous_pod_name=old_operator_pod_name,
                )
                self.assertNotEqual(
                    current_operator_pod["metadata"]["name"],
                    old_operator_pod_name,
                )
                if upgrade_options.deployment_name != install_options.deployment_name:
                    kutil.wait_deploy_gone(
                        upgrade_options.namespace,
                        install_options.deployment_name,
                        timeout=300,
                    )
                    self.assertIsNone(
                        kutil.get_deploy(
                            upgrade_options.namespace,
                            install_options.deployment_name,
                            check=False,
                        ),
                        msg=(
                            f"Deployment {upgrade_options.namespace}/"
                            f"{install_options.deployment_name} should be gone "
                            f"after upgrade to deployment.name="
                            f"{upgrade_options.deployment_name}"
                        ),
                    )
                self._assert_helm_operator_image_tag(
                    current_operator_pod,
                    g_ts_cfg.operator_version_tag,
                )
                self.wait_ic(
                    cluster_name,
                    "ONLINE",
                    num_online=server_instances,
                    ns=self.ns,
                )
                self._assert_helm_cluster_runtime_image_identities(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                    expected_release=previous_release,
                    expected_operator_release=active_cluster_operator_release,
                )

                server_rollover_waiter = tutil.get_sts_rollover_update_waiter(
                    self,
                    cluster_name,
                    timeout=900,
                    delay=50,
                )
                router_rollover_waiter = None
                if router_instances:
                    router_rollover_waiter = (
                        tutil.get_router_deploy_rollover_update_waiter(
                            self,
                            cluster_name,
                            timeout=server_instances * 200,
                            delay=10,
                        )
                    )

                active_cluster_install = self._build_helm_cluster_tracker(
                    self.ns,
                    cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                    app_version=g_ts_cfg.operator_version_tag,
                    cluster_values=cluster_values,
                )
                self.assertEqual(
                    active_cluster_install.options.app_version,
                    g_ts_cfg.operator_version_tag,
                )
                upgrade_cluster_with_helm(
                    self.ns,
                    resolved_options=active_cluster_install.options,
                )
                server_rollover_waiter()
                if router_rollover_waiter is not None:
                    router_rollover_waiter()
                self._wait_for_helm_cluster_ready(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                )
                self._assert_helm_cluster_runtime_image_identities(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                    expected_release=g_ts_cfg.operator_version_tag,
                )
                self._assert_helm_cluster_cr_version(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    expected_version=current_mysql_version,
                )
        except Exception as exc:
            test_error = exc
            if active_cluster_install is not None:
                self._print_helm_cluster_pod_diagnostics(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                )
            self._print_operator_log_from_candidates(
                namespace=self.operator_ns,
                deployment_names=[
                    upgrade_options.deployment_name,
                    install_options.deployment_name,
                ],
            )
            raise
        finally:
            cleanup_error = None
            active_release_key = (
                upgrade_result.release_key
                if upgrade_result is not None
                else (
                    install_result.release_key
                    if install_result is not None
                    else get_helm_release_target(install_options)
                )
            )

            try:
                if active_cluster_install is not None:
                    try:
                        self._cleanup_helm_cluster(active_cluster_install)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                try:
                    uninstall_with_helm(
                        release_key=active_release_key,
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


def assert_operator_status_quo(
    testcase: unittest.TestCase,
    artifacts: dict,
    operator_ns: str,
    operator_deploy_name: str,
) -> None:
    testcase.assertTrue("deployment" in artifacts and artifacts["deployment"]["metadata"]["name"] == operator_deploy_name)
    testcase.assertTrue("clusterroles" in artifacts and "mysql-operator" in artifacts["clusterroles"])
    testcase.assertTrue("clusterroles" in artifacts and "mysql-sidecar" in artifacts["clusterroles"])
    testcase.assertTrue("clusterroles" in artifacts and "mysql-switchover" in artifacts["clusterroles"])

    # --- top-level shape ---
    testcase.assertTrue(isinstance(artifacts, dict))
    testcase.assertEqual(
        set(artifacts.keys()),
        {
            "namespace_labels",
            "deployment",
            "clusterroles",
            "clusterrolebindings",
            "clusterkopfpeerings",
            "serviceaccounts",
            "secrets",
            "roles",
            "rolebindings",
            "kopfpeerings",
        },
    )

    # --- namespace labels ---
    testcase.assertTrue("namespace_labels" in artifacts)
    testcase.assertEqual(artifacts["namespace_labels"]["kubernetes.io/metadata.name"], operator_ns)

    # --- deployment checks (basic identity) ---
    testcase.assertTrue("deployment" in artifacts)
    testcase.assertEqual(artifacts["deployment"]["kind"], "Deployment")
    testcase.assertEqual(artifacts["deployment"]["apiVersion"], "apps/v1")
    testcase.assertEqual(artifacts["deployment"]["metadata"]["name"], operator_deploy_name)
    testcase.assertEqual(artifacts["deployment"]["metadata"]["namespace"], operator_ns)

    # ensure key labels exist (don’t over-assert full label set unless you want strictness)
    testcase.assertEqual(
        artifacts["deployment"]["metadata"]["labels"]["app.kubernetes.io/name"],
        "mysql-operator",
    )
    testcase.assertTrue(
        artifacts["deployment"]["metadata"]["labels"]["app.kubernetes.io/instance"] in ["myoperator", "mysql-operator"]
    )

    # --- deployment spec checks (pod spec) ---
    dep_spec = artifacts["deployment"]["spec"]
    testcase.assertEqual(dep_spec["replicas"], 1)

    pod_spec = dep_spec["template"]["spec"]
    testcase.assertEqual(pod_spec["serviceAccountName"], "mysql-operator-sa")

    testcase.assertTrue("containers" in pod_spec)
    testcase.assertEqual(len(pod_spec["containers"]), 1)

    c0 = pod_spec["containers"][0]
    testcase.assertEqual(c0["name"], "mysql-operator")
    testcase.assertEqual(c0["image"], g_ts_cfg.get_operator_image(None))
    testcase.assertEqual(c0["imagePullPolicy"], "Always")
    testcase.assertTrue("readinessProbe" in c0)
    testcase.assertEqual(c0["readinessProbe"]["exec"]["command"], ["cat", "/tmp/mysql-operator-ready"])

    # check container envs
    envs = {e["name"]:e.get("value") or e.get("valueFrom") for e in c0["env"]}
    testcase.assertTrue("MYSQLSH_USER_CONFIG_HOME" in envs)
    testcase.assertTrue("MYSQLSH_CREDENTIAL_STORE_SAVE_PASSWORDS" in envs)
    testcase.assertTrue("MYSQL_OPERATOR_DEFAULT_REPOSITORY" in envs)
    testcase.assertTrue("MYSQL_OPERATOR_IMAGE_PULL_POLICY" in envs)
    testcase.assertTrue("MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN" in envs)
    testcase.assertTrue("POD_NAME" in envs)
    testcase.assertTrue("POD_NAMESPACE" in envs)
    testcase.assertTrue("OPERATOR_DEPLOYMENT_NAME" in envs)
    testcase.assertTrue("OPERATOR_PEERING_NAME" in envs)
    testcase.assertTrue("OPERATOR_ROLE_NAME" in envs)
    testcase.assertTrue("SIDECAR_ROLE_NAME" in envs)
    testcase.assertTrue("SWITCHOVER_ROLE_NAME" in envs)
    testcase.assertTrue("OPERATOR_NAMESPACES" in envs)
    testcase.assertTrue("MYSQL_OPERATOR_DEBUG" in envs)

    # volumes/mounts (ensure tmp + mysqlsh are present, and only those two volumes exist)
    testcase.assertTrue("volumes" in pod_spec)
    testcase.assertEqual(len(pod_spec["volumes"]), 2)
    vol_names = {v["name"] for v in pod_spec["volumes"]}
    testcase.assertEqual(vol_names, {"mysqlsh-home", "tmpdir"})

    testcase.assertTrue("volumeMounts" in c0)
    testcase.assertEqual(len(c0["volumeMounts"]), 2)
    vm_by_name = {vm["name"]: vm["mountPath"] for vm in c0["volumeMounts"]}
    testcase.assertEqual(vm_by_name["mysqlsh-home"], "/mysqlsh")
    testcase.assertEqual(vm_by_name["tmpdir"], "/tmp")

    # --- clusterroles ---
    testcase.assertTrue("clusterroles" in artifacts)
    testcase.assertEqual(len(artifacts["clusterroles"]), 3)
    testcase.assertTrue("mysql-operator" in artifacts["clusterroles"])
    testcase.assertTrue("mysql-sidecar" in artifacts["clusterroles"])
    testcase.assertTrue("mysql-switchover" in artifacts["clusterroles"])

    # verify no unexpected clusterroles beyond the three above
    testcase.assertEqual(
        set(artifacts["clusterroles"].keys()),
        {"mysql-operator", "mysql-sidecar", "mysql-switchover"},
    )

    # --- mysql-operator rules (exact) ---
    testcase.assertEqual(
        artifacts["clusterroles"]["mysql-operator"]["rules"],
        [
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch", "patch"]},
            {"apiGroups": [""], "resources": ["pods/status"], "verbs": ["get", "patch", "update", "watch"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "create", "list", "watch", "patch"]},
            {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "create", "update", "list", "watch", "patch", "delete"]},
            {"apiGroups": [""], "resources": ["services"], "verbs": ["get", "create", "list", "update", "delete", "patch"]},
            {"apiGroups": [""], "resources": ["serviceaccounts"], "verbs": ["get", "create", "patch"]},
            {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "patch", "update"]},
            {"apiGroups": ["rbac.authorization.k8s.io"], "resources": ["rolebindings"], "verbs": ["get", "create", "patch", "delete"]},
            {"apiGroups": ["policy"], "resources": ["poddisruptionbudgets"], "verbs": ["get", "create"]},
            {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["create"]},
            {"apiGroups": ["batch"], "resources": ["cronjobs"], "verbs": ["get", "create", "update", "delete"]},
            {"apiGroups": ["apps"], "resources": ["deployments", "statefulsets"], "verbs": ["get", "list", "create", "patch", "update", "watch", "delete"]},
            {"apiGroups": ["mysql.oracle.com"], "resources": ["*"], "verbs": ["*"]},
            {"apiGroups": ["zalando.org"], "resources": ["*"], "verbs": ["get", "patch", "list", "watch"]},
            {"apiGroups": ["apiextensions.k8s.io"], "resources": ["customresourcedefinitions"], "verbs": ["list", "watch"]},
            {"apiGroups": [""], "resources": ["namespaces"], "verbs": ["list", "watch"]},
            {"apiGroups": ["monitoring.coreos.com"], "resources": ["servicemonitors"], "verbs": ["get", "create", "patch", "update", "delete"]},
        ],
    )

    # --- mysql-sidecar rules (exact) ---
    testcase.assertEqual(
        artifacts["clusterroles"]["mysql-sidecar"]["rules"],
        [
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list", "watch", "patch"]},
            {"apiGroups": [""], "resources": ["pods/status"], "verbs": ["get", "patch", "update", "watch"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get", "create", "list", "watch", "patch"]},
            {"apiGroups": [""], "resources": ["configmaps"], "verbs": ["get", "create", "list", "watch", "patch"]},
            {"apiGroups": [""], "resources": ["services"], "verbs": ["get", "create", "list", "update"]},
            {"apiGroups": [""], "resources": ["serviceaccounts"], "verbs": ["get", "create"]},
            {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "patch", "update"]},
            {"apiGroups": ["apps"], "resources": ["deployments"], "verbs": ["get", "patch"]},
            {"apiGroups": ["mysql.oracle.com"], "resources": ["innodbclusters"], "verbs": ["get", "watch", "list", "patch"]},
            {"apiGroups": ["mysql.oracle.com"], "resources": ["mysqlbackups"], "verbs": ["create", "get", "list", "patch", "update", "watch", "delete"]},
            {"apiGroups": ["mysql.oracle.com"], "resources": ["mysqlbackups/status"], "verbs": ["get", "patch", "update", "watch"]},
        ],
    )

    # --- mysql-switchover rules (exact) ---
    testcase.assertEqual(
        artifacts["clusterroles"]["mysql-switchover"]["rules"],
        [
            {"apiGroups": [""], "resources": ["events"], "verbs": ["create", "patch", "update"]},
            {"apiGroups": [""], "resources": ["pods"], "verbs": ["get", "list"]},
            {"apiGroups": [""], "resources": ["secrets"], "verbs": ["get"]},
            {"apiGroups": ["mysql.oracle.com"], "resources": ["innodbclusters"], "verbs": ["get"]},
            {"apiGroups": ["mysql.oracle.com"], "resources": ["innodbclusters/status"], "verbs": ["get", "patch"]},
        ],
    )

    # --- clusterrolebindings ---
    testcase.assertTrue("clusterrolebindings" in artifacts)
    testcase.assertEqual(len(artifacts["clusterrolebindings"]), 1)
    testcase.assertTrue("mysql-operator-rolebinding" in artifacts["clusterrolebindings"])

    crb = artifacts["clusterrolebindings"]["mysql-operator-rolebinding"]
    testcase.assertEqual(crb["kind"], "ClusterRoleBinding")
    testcase.assertEqual(crb["roleRef"]["kind"], "ClusterRole")
    testcase.assertEqual(crb["roleRef"]["name"], "mysql-operator")
    testcase.assertEqual(len(crb["subjects"]), 1)
    testcase.assertEqual(crb["subjects"][0]["kind"], "ServiceAccount")
    testcase.assertEqual(crb["subjects"][0]["name"], "mysql-operator-sa")
    testcase.assertEqual(crb["subjects"][0]["namespace"], operator_ns)

    # --- clusterkopfpeerings ---
    testcase.assertTrue("clusterkopfpeerings" in artifacts)
    testcase.assertEqual(len(artifacts["clusterkopfpeerings"]), 1)
    testcase.assertTrue("mysql-operator" in artifacts["clusterkopfpeerings"])
    testcase.assertEqual(
        set(artifacts["clusterkopfpeerings"].keys()),
        {"mysql-operator"},
    )
    testcase.assertEqual(
        artifacts["clusterkopfpeerings"]["mysql-operator"]["kind"],
        "ClusterKopfPeering",
    )

    # --- serviceaccounts ---
    testcase.assertTrue("serviceaccounts" in artifacts)
    testcase.assertEqual(len(artifacts["serviceaccounts"]), 1)
    testcase.assertEqual(set(artifacts["serviceaccounts"].keys()), {"mysql-operator-sa"})

    sa = artifacts["serviceaccounts"]["mysql-operator-sa"]
    testcase.assertEqual(sa["apiVersion"], "v1")
    testcase.assertEqual(sa["kind"], "ServiceAccount")

    testcase.assertTrue("metadata" in sa)
    testcase.assertEqual(sa["metadata"]["name"], "mysql-operator-sa")
    testcase.assertEqual(sa["metadata"]["namespace"], operator_ns)

    testcase.assertTrue("annotations" in sa["metadata"])
    testcase.assertEqual(
        sa["metadata"]["annotations"],
        {
            "meta.helm.sh/release-name": "myoperator",
            "meta.helm.sh/release-namespace": "mysql-operator",
        },
    )

    # --- referenced image pull secrets ---
    testcase.assertTrue("secrets" in artifacts)
    expected_pull_secret_names = set(
        get_referenced_image_pull_secret_names(
            artifacts["deployment"],
            artifacts["serviceaccounts"],
        )
    )
    testcase.assertEqual(set(artifacts["secrets"].keys()), expected_pull_secret_names)
    for secret_name, secret_manifest in artifacts["secrets"].items():
        testcase.assertEqual(secret_manifest["metadata"]["name"], secret_name)
        testcase.assertEqual(secret_manifest["metadata"]["namespace"], operator_ns)

    # --- namespaced RBAC / namespaced Kopf peerings should be empty
    testcase.assertTrue("roles" in artifacts)
    testcase.assertEqual(len(artifacts["roles"]), 0)
    testcase.assertTrue("rolebindings" in artifacts)
    testcase.assertEqual(len(artifacts["rolebindings"]), 0)
    testcase.assertTrue("kopfpeerings" in artifacts)
    testcase.assertEqual(len(artifacts["kopfpeerings"]), 0)


def assert_failed_helm_operator_upgrade_target_state(
    testcase: unittest.TestCase,
    *,
    installed_namespace: str,
    installed_deployment_name: str,
    attempted_namespace: str,
    attempted_deployment_name: str,
) -> None:
    attempted_deployment = kutil.get_deploy(
        attempted_namespace,
        attempted_deployment_name,
        check=False,
    )

    if (
        attempted_namespace == installed_namespace
        and attempted_deployment_name == installed_deployment_name
    ):
        testcase.assertIsNotNone(
            attempted_deployment,
            msg=(
                f"Deployment {installed_namespace}/"
                f"{installed_deployment_name} should remain present when "
                "Helm rejects an upgrade that keeps the existing deployment "
                "target"
            ),
        )
        return

    testcase.assertIsNone(
        attempted_deployment,
        msg=(
            f"Deployment {attempted_namespace}/{attempted_deployment_name} "
            "should not be created when Helm rejects the attempted upgrade"
        ),
    )


class OperatorStatusQuoTest(OperatorSingleAndMultipleBaseTest):
    def test_00_status_quo(self) -> None:
        assert_operator_status_quo(
            self,
            self.artifacts,
            self.operator_ns,
            self.operator_deploy_name,
        )

    def test_01_remove_and_recreate(self) -> None:
        operator_ns = self.operator_ns
        operator_deploy_name = self.operator_deploy_name
        crole_names_pattern = self.crole_names_pattern
        crole_binding_pattern = self.crole_binding_pattern
        ckopf_peerings_pattern = self.ckopf_peerings_pattern
        sa_names_pattern = self.sa_names_pattern
        role_names_pattern = self.role_names_pattern
        role_binding_pattern = self.role_binding_pattern
        kopf_peerings_pattern = self.kopf_peerings_pattern

        old_artifacts = remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_deploy_name,
            artifacts=self.artifacts,
            delete_namespace=True,
            check_namespace_empty=False,
        )
        self.assertEqual(self.artifacts, old_artifacts)

        new_artifacts = get_operator_artifacts(operator_ns=operator_ns,
                                                operator_deploy_name=operator_deploy_name,
                                                crole_names_pattern=crole_names_pattern,
                                                crole_binding_pattern=crole_binding_pattern,
                                                ckopf_peerings_pattern=ckopf_peerings_pattern,
                                                sa_names_pattern=sa_names_pattern,
                                                role_names_pattern=role_names_pattern,
                                                role_binding_pattern=role_binding_pattern,
                                                kopf_peerings_pattern=kopf_peerings_pattern
                                                )
        self.assertEqual(new_artifacts, None)
        restore_operator(self.artifacts, operator_ns)

        new_artifacts = get_operator_artifacts(operator_ns=operator_ns,
                                                operator_deploy_name=operator_deploy_name,
                                                crole_names_pattern=crole_names_pattern,
                                                crole_binding_pattern=crole_binding_pattern,
                                                ckopf_peerings_pattern=ckopf_peerings_pattern,
                                                sa_names_pattern=sa_names_pattern,
                                                role_names_pattern=role_names_pattern,
                                                role_binding_pattern=role_binding_pattern,
                                                kopf_peerings_pattern=kopf_peerings_pattern
                                                )
        self.assertEqual(new_artifacts.keys(), self.artifacts.keys())
        if new_artifacts == self.artifacts:
            self.assertTrue(new_artifacts == self.artifacts)
        else:
            for a_name in new_artifacts:
                self.assertTrue(new_artifacts[a_name] == self.artifacts[a_name], msg = f"{a_name} differs: {repr(new_artifacts[a_name])} != {repr(self.artifacts[a_name])}")


class LabelsAndAnnotationsOperatorTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_labels_annotations(self) -> None:
        operator_ns = f"op-lbl-ann-{self.random_suffix}"
        cluster_ns = f"db-lbl-ann-{self.random_suffix}"
        operator_name = f"myop-lbl-ann-{self.random_suffix}"
        release_name = f"myoper-lbl-ann-{self.random_suffix}"

        custom_meta = {
            "labels": {"test-deploy-label": "true"},
            "annotations": {"test-deploy-annotation": "checked"}
        }
        custom_spec = {
            "template": {
                "metadata": {
                    "labels": {"test-pod-label": "true"},
                    "annotations": {"test-pod-annotation": "checked"}
                }
            }
        }

        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
  namespace: {cluster_ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
        try:
            kutil.create_ns(cluster_ns, labels={})
            kutil.create_user_secrets(
                cluster_ns,
                self.cluster_secret_name,
                "root",
                "%",
                "sakila",
            )

            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                custom_meta=custom_meta,
                custom_spec=custom_spec,
                watch_namespaces=cluster_ns,
            )
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            try:
                kutil.apply(cluster_ns, yaml)
                self.wait_ic(
                    self.cluster_name,
                    ["PENDING", "INITIALIZING", "ONLINE"],
                    ns=cluster_ns,
                )

                for instance in range(0, self.cluster_size):
                    self.wait_pod(
                        f"{self.cluster_name}-{instance}",
                        "Running",
                        ns=cluster_ns,
                    )

                if self.routers_count:
                    self.wait_routers(
                        f"{self.cluster_name}-router.*",
                        self.routers_count,
                        timeout=self.cluster_size * 120,
                        wait=10,
                        ns=cluster_ns,
                    )

                self.wait_ic(
                    self.cluster_name,
                    "ONLINE",
                    num_online=self.cluster_size,
                    ns=cluster_ns,
                )
            except Exception:
                print_operator_log(operator_ns, operator_name)
                raise

            operator_deployment = kutil.get_deploy(operator_ns, operator_name)
            self.assertEqual(
                operator_deployment["metadata"]["labels"].get("test-deploy-label"),
                "true",
            )

            op_pods = kutil.ls_po(operator_ns, pattern=f"{operator_name}.*")
            self.assertEqual(len(op_pods), 1)

            pod_name = op_pods[0]["NAME"]
            operator_pod = kutil.get_po(operator_ns, pod_name)

            app_instance = operator_pod["metadata"]["labels"].get(
                "app.kubernetes.io/instance"
            )
            self.assertEqual(
                app_instance,
                get_global_instance_name(operator_ns, operator_name),
            )
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_cluster_namespace_if_present(
                    namespace=cluster_ns,
                    cluster_name=self.cluster_name,
                    secret_name=self.cluster_secret_name,
                    delete_namespace=False,
                ),
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: (
                    kutil.delete_ns(cluster_ns)
                    if cluster_ns in kutil.ls_ns_ex()
                    else None
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class NodeSelectorAffinityOperatorTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_node_selector_affinity(self) -> None:
        operator_ns = f"op-nod-sel-{self.random_suffix}"
        cluster_ns = f"db-nod-sel-{self.random_suffix}"
        operator_name = f"myop-nod-sel-{self.random_suffix}"
        release_name = f"myoper-nod-sel-{self.random_suffix}"
        node_label_key = "e2e.mysql.oracle.com/node-selector"
        node_label_value = f"operator-affinity-{self.random_suffix}"

        custom_spec = {
            "replicas": 1,
            "template": {
                "spec": {
                    "nodeSelector": {
                        node_label_key: node_label_value
                    },
                    "affinity": {
                        "nodeAffinity": {
                            "requiredDuringSchedulingIgnoredDuringExecution": {
                                "nodeSelectorTerms": [
                                    {
                                        "matchExpressions": [
                                            {"key": node_label_key, "operator": "In", "values": [node_label_value]}
                                        ]
                                    }
                                ]
                            }
                        }
                    }
                }
            }
        }

        # Label a worker node to satisfy the NodeSelector requirement
        agent_node_name = None
        for n in kutil.ls_nodes():
            if isinstance(n, dict):
                roles = str(n.get("ROLES", ""))
                if "control-plane" not in roles and "master" not in roles:
                    agent_node_name = n.get("NAME")
                    break

        if not agent_node_name:
            raise Exception(f"No worker node found to apply {node_label_key} label. This test requires at least one non-control-plane node.")

        kutil.label_node(agent_node_name, {node_label_key: node_label_value})

        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
  namespace: {cluster_ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
        try:
            kutil.create_ns(cluster_ns, labels={})
            kutil.create_user_secrets(
                cluster_ns,
                self.cluster_secret_name,
                "root",
                "%",
                "sakila",
            )

            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                custom_spec=custom_spec,
                watch_namespaces=cluster_ns,
            )
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            try:
                kutil.apply(cluster_ns, yaml)
                self.wait_ic(
                    self.cluster_name,
                    ["PENDING", "INITIALIZING", "ONLINE"],
                    ns=cluster_ns,
                )

                for instance in range(0, self.cluster_size):
                    self.wait_pod(
                        f"{self.cluster_name}-{instance}",
                        "Running",
                        ns=cluster_ns,
                    )

                if self.routers_count:
                    self.wait_routers(
                        f"{self.cluster_name}-router.*",
                        self.routers_count,
                        timeout=self.cluster_size * 120,
                        wait=10,
                        ns=cluster_ns,
                    )

                self.wait_ic(
                    self.cluster_name,
                    "ONLINE",
                    num_online=self.cluster_size,
                    ns=cluster_ns,
                )
            except Exception:
                print_operator_log(operator_ns, operator_name)
                raise

            op_pods = kutil.ls_po(operator_ns, pattern=f"{operator_name}.*")
            self.assertEqual(len(op_pods), 1)

            pod_name = op_pods[0]["NAME"]
            operator_pod = kutil.get_po(operator_ns, pod_name)
            node_name = operator_pod["spec"].get("nodeName")

            node_labels = kutil.get_node_labels(node_name)
            self.assertEqual(node_labels.get(node_label_key), node_label_value)
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_cluster_namespace_if_present(
                    namespace=cluster_ns,
                    cluster_name=self.cluster_name,
                    secret_name=self.cluster_secret_name,
                    delete_namespace=False,
                ),
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: (
                    kutil.delete_ns(cluster_ns)
                    if cluster_ns in kutil.ls_ns_ex()
                    else None
                ),
                lambda: kutil.label_node(agent_node_name, {node_label_key: None}),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class StandaloneGlobalOperatorNoPeeringTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_standalone_global_operator_has_no_peering(self) -> None:
        operator_ns = f"op-standalone-{self.random_suffix}"
        operator_name = f"myop-standalone-{self.random_suffix}"
        release_name = f"myoper-standalone-{self.random_suffix}"
        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        try:
            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                standalone=True,
            )
            self.assertEqual(patched["clusterkopfpeerings"], {})
            self.assertEqual(patched["kopfpeerings"], {})

            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            operator_deployment = kutil.get_deploy(operator_ns, operator_name)
            envs = get_operator_envs_from_deployment(operator_deployment)

            self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
            assert_no_peering(self, operator_ns, operator_name)
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class HelmDeploymentMetadataValuesTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _build_metadata_helm_options(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
        source_operator_deployment: Optional[dict] = None,
    ):
        if source_operator_deployment is None:
            source_operator_deployment = kutil.get_deploy(
                self.operator_ns,
                self.operator_deploy_name,
            )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        helm_options.operator_values["deployment"]["name"] = operator_name
        # These tests validate metadata rendering, not cluster-wide exclusivity.
        helm_options.operator_values["deployment"]["namespaces"] = [operator_ns]
        return helm_options

    def _assert_metadata_label_install_rejected(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
        value_key: str,
        labels: dict,
        error_fragment: str,
    ) -> None:
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = self._build_metadata_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.operator_values["deployment"][value_key] = labels

        install_error = None
        test_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    f"Helm install with reserved labels in deployment.{value_key} "
                    f"unexpectedly succeeded"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, operator_name, check=False),
                msg=(
                    f"Helm chart should reject reserved labels in deployment.{value_key} "
                    f"before creating deployment {operator_ns}/{operator_name}"
                ),
            )

            install_error_output = (
                getattr(install_error, "stdout", None)
                or getattr(install_error, "output", None)
                or str(install_error)
            )
            self.assertIn(error_fragment, install_error_output)
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            try:
                uninstall_with_helm(
                    release_key=get_helm_release_target(helm_options),
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_deployment_metadata_values(self) -> None:
        operator_ns = f"op-meta-helm-{self.random_suffix}"
        operator_name = f"myop-meta-helm-{self.random_suffix}"
        release_name = f"myoper-meta-helm-{self.random_suffix}"

        deployment_labels = {
            "e2e.mysql.oracle.com/deploy-label-1": "deploy-label-one",
            "e2e.mysql.oracle.com/deploy-label-2": "deploy-label-two",
        }
        deployment_annotations = {
            "e2e.mysql.oracle.com/deploy-annotation-1": "deploy-annotation-one",
            "e2e.mysql.oracle.com/deploy-annotation-2": "deploy-annotation-two",
        }
        pod_labels = {
            "e2e.mysql.oracle.com/pod-label-1": "pod-label-one",
            "e2e.mysql.oracle.com/pod-label-2": "pod-label-two",
        }
        pod_annotations = {
            "e2e.mysql.oracle.com/pod-annotation-1": "pod-annotation-one",
            "e2e.mysql.oracle.com/pod-annotation-2": "pod-annotation-two",
        }
        resources = {
            "limits": {
                "cpu": "500m",
                "memory": "1Gi",
            },
            "requests": {
                "cpu": "100m",
                "memory": "512Mi",
            },
        }

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = self._build_metadata_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
            source_operator_deployment=source_operator_deployment,
        )
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.deploymentLabels,
        # deployment.deploymentAnnotations, deployment.podLabels,
        # deployment.podAnnotations, deployment.resources
        helm_options.operator_values["deployment"]["deploymentLabels"] = deployment_labels
        helm_options.operator_values["deployment"]["deploymentAnnotations"] = deployment_annotations
        helm_options.operator_values["deployment"]["podLabels"] = pod_labels
        helm_options.operator_values["deployment"]["podAnnotations"] = pod_annotations
        helm_options.operator_values["deployment"]["resources"] = resources

        install_result = None
        test_error = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            assert_mapping_contains(
                self,
                operator_deployment.get("metadata", {}).get("labels"),
                deployment_labels,
                label="Deployment metadata.labels",
            )
            assert_mapping_contains(
                self,
                operator_deployment.get("metadata", {}).get("annotations"),
                deployment_annotations,
                label="Deployment metadata.annotations",
            )
            assert_mapping_contains(
                self,
                operator_deployment.get("spec", {}).get("template", {}).get("metadata", {}).get("labels"),
                pod_labels,
                label="Deployment spec.template.metadata.labels",
            )
            assert_mapping_contains(
                self,
                operator_deployment.get("spec", {}).get("template", {}).get("metadata", {}).get("annotations"),
                pod_annotations,
                label="Deployment spec.template.metadata.annotations",
            )
            deployment_container = get_named_container(
                operator_deployment.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers"),
                "mysql-operator",
            )
            self.assertIsNotNone(
                deployment_container,
                msg="Deployment spec is missing mysql-operator container",
            )
            assert_resource_requirements_contain(
                self,
                deployment_container.get("resources"),
                resources,
                label=(
                    "Deployment spec.template.spec.containers"
                    "[mysql-operator].resources"
                ),
            )

            operator_pods = kutil.ls_po(
                install_result.options.namespace,
                pattern=f"{install_result.options.deployment_name}.*",
            )
            self.assertEqual(len(operator_pods), 1)

            operator_pod = kutil.get_po(
                install_result.options.namespace,
                operator_pods[0]["NAME"],
            )
            assert_mapping_contains(
                self,
                operator_pod.get("metadata", {}).get("labels"),
                pod_labels,
                label="Operator pod metadata.labels",
            )
            assert_mapping_contains(
                self,
                operator_pod.get("metadata", {}).get("annotations"),
                pod_annotations,
                label="Operator pod metadata.annotations",
            )
            pod_container = get_named_container(
                operator_pod.get("spec", {}).get("containers"),
                "mysql-operator",
            )
            self.assertIsNotNone(
                pod_container,
                msg="Operator pod spec is missing mysql-operator container",
            )
            assert_resource_requirements_contain(
                self,
                pod_container.get("resources"),
                resources,
                label="Operator pod spec.containers[mysql-operator].resources",
            )
        except Exception as exc:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            test_error = exc
            raise
        finally:
            cleanup_error = None
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_deployment_metadata_values_reject_reserved_deployment_labels(self) -> None:
        self._assert_metadata_label_install_rejected(
            operator_ns=f"op-meta-dpl-helm-{self.random_suffix}",
            operator_name=f"myop-meta-dpl-helm-{self.random_suffix}",
            release_name=f"myoper-meta-dpl-helm-{self.random_suffix}",
            value_key="deploymentLabels",
            labels={"app.kubernetes.io/name": "mysql-operator"},
            error_fragment=(
                "Invalid values: deployment.deploymentLabels contains "
                "chart-managed label keys: app.kubernetes.io/name"
            ),
        )

    def test_deployment_metadata_values_reject_reserved_pod_labels(self) -> None:
        self._assert_metadata_label_install_rejected(
            operator_ns=f"op-meta-pdl-helm-{self.random_suffix}",
            operator_name=f"myop-meta-pdl-helm-{self.random_suffix}",
            release_name=f"myoper-meta-pdl-helm-{self.random_suffix}",
            value_key="podLabels",
            labels={"name": "mysql-operator"},
            error_fragment=(
                "Invalid values: deployment.podLabels contains "
                "chart-managed label keys: name"
            ),
        )

    def test_deployment_metadata_values_reject_reserved_label_overlaps(self) -> None:
        scenarios = [
            {
                "value_key": "deploymentLabels",
                "labels": {"app.kubernetes.io/name": "mysql-operator"},
                "error_fragment": (
                    "Invalid values: deployment.deploymentLabels contains "
                    "chart-managed label keys: app.kubernetes.io/name"
                ),
                "suffix": "dpl",
            },
            {
                "value_key": "podLabels",
                "labels": {"name": "mysql-operator"},
                "error_fragment": (
                    "Invalid values: deployment.podLabels contains "
                    "chart-managed label keys: name"
                ),
                "suffix": "pdl",
            },
        ]

        for scenario in scenarios:
            with self.subTest(value_key=scenario["value_key"]):
                self._assert_metadata_label_install_rejected(
                    operator_ns=(
                        f"op-meta-{scenario['suffix']}-combo-helm-{self.random_suffix}"
                    ),
                    operator_name=f"myop-meta-{scenario['suffix']}-c-{self.random_suffix}",
                    release_name=(
                        f"myoper-meta-{scenario['suffix']}-combo-helm-{self.random_suffix}"
                    ),
                    value_key=scenario["value_key"],
                    labels=scenario["labels"],
                    error_fragment=scenario["error_fragment"],
                )


class HelmDeploymentPlacementValuesTest(OperatorSingleAndMultipleBaseTest):
    affinity_warning_fragment = (
        "WARNING: affinity is deprecated; use deployment.affinity instead."
    )
    node_selector_warning_fragment = (
        "WARNING: nodeSelector is deprecated; use deployment.nodeSelector instead."
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _ensure_clean_operator_test_start(self) -> None:
        for operator_ns, operator_deploy_name in list_cluster_operator_deployments():
            if self._is_default_operator(operator_ns, operator_deploy_name):
                continue
            self._remove_unexpected_operator(
                operator_ns,
                operator_deploy_name,
            )

        default_operator = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        if default_operator is not None:
            remove_operator(
                operator_ns=self.operator_ns,
                operator_deploy_name=self.operator_deploy_name,
                artifacts=self.artifacts,
                return_manifests=False,
                delete_namespace=True,
                check_namespace_empty=False,
            )

    def _build_placement_helm_options(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
    ):
        source_operator_deployment = copy.deepcopy(
            (self.artifacts or {}).get("deployment")
        )
        if source_operator_deployment is None:
            source_operator_deployment = kutil.get_deploy(
                self.operator_ns,
                self.operator_deploy_name,
            )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["replicas"] = 1
        # Placement coverage exercises a second operator install, so keep the
        # watch scope local and run the class without the baseline global
        # raw-manifest operator present.
        helm_options.operator_values["deployment"]["namespaces"] = [operator_ns]
        return helm_options

    def _get_single_operator_pod(
        self,
        *,
        namespace: str,
        deployment_name: str,
    ) -> dict:
        operator_pods = kutil.ls_po(
            namespace,
            pattern=f"{deployment_name}.*",
        )
        self.assertEqual(
            len(operator_pods),
            1,
            msg=(
                f"Expected exactly 1 operator pod for deployment "
                f"{namespace}/{deployment_name}, got {len(operator_pods)}"
            ),
        )
        return kutil.get_po(namespace, operator_pods[0]["NAME"])

    def _wait_for_operator_pod_rollout(
        self,
        *,
        namespace: str,
        deployment_name: str,
        previous_pod_name: Optional[str] = None,
    ) -> dict:
        if previous_pod_name:
            kutil.wait_pod_gone(namespace, previous_pod_name, timeout=300)

        kutil.wait_deploy(namespace, deployment_name, timeout=300)
        operator_pod = self._get_single_operator_pod(
            namespace=namespace,
            deployment_name=deployment_name,
        )
        kutil.wait_pod(
            namespace,
            operator_pod["metadata"]["name"],
            "Running",
            timeout=300,
        )
        return kutil.get_po(namespace, operator_pod["metadata"]["name"])

    def _build_affinity_value(
        self,
        *,
        worker_nodes: list[dict[str, str]],
        blocker_label_key: str,
        blocker_label_value: str,
    ) -> dict:
        return {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "kubernetes.io/hostname",
                                    "operator": "In",
                                    "values": [
                                        worker_node["hostname"]
                                        for worker_node in worker_nodes
                                    ],
                                },
                            ],
                        },
                    ],
                },
            },
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "labelSelector": {
                            "matchLabels": {
                                blocker_label_key: blocker_label_value,
                            },
                        },
                        "topologyKey": "kubernetes.io/hostname",
                    },
                ],
            },
        }

    def _create_blocker_pods(
        self,
        *,
        namespace: str,
        node_names: list[str],
        labels: dict[str, str],
        pod_name_prefix: str,
    ) -> list[str]:
        pod_names = []
        for index, node_name in enumerate(node_names):
            pod_name = f"{pod_name_prefix}-{self.random_suffix}-{index}"
            create_blocker_pod(
                namespace,
                pod_name,
                node_name=node_name,
                labels=labels,
            )
            pod_names.append(pod_name)
        return pod_names

    def _assert_placement_value_install_rejected(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
        old_key: str,
        new_key: str,
        old_value: dict,
        new_value: dict,
        error_fragment: str,
    ) -> None:
        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values[old_key] = old_value
        helm_options.operator_values["deployment"][new_key] = new_value

        install_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    f"Helm install with both {old_key} and deployment.{new_key} "
                    "unexpectedly succeeded"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, operator_name, check=False),
                msg=(
                    f"Helm chart should reject simultaneous {old_key} and "
                    f"deployment.{new_key} before creating deployment "
                    f"{operator_ns}/{operator_name}"
                ),
            )

            install_error_output = (
                getattr(install_error, "stdout", None)
                or getattr(install_error, "output", None)
                or str(install_error)
            )
            self.assertIn(error_fragment, install_error_output)
        finally:
            uninstall_with_helm(
                release_key=get_helm_release_target(helm_options),
                delete_namespace=True,
            )

    def test_deployment_affinity_value(self) -> None:
        operator_ns = f"op-affinity-helm-{self.random_suffix}"
        operator_name = f"myop-affinity-helm-{self.random_suffix}"
        release_name = f"myoper-affinity-helm-{self.random_suffix}"
        worker_nodes = get_worker_nodes(self)[:3]
        worker_node_hostnames = [
            worker_node["hostname"] for worker_node in worker_nodes
        ]
        target_node_name = worker_nodes[2]["name"]
        blocker_node_names = [
            worker_node["name"] for worker_node in worker_nodes[:2]
        ]
        blocker_label_key = "e2e.mysql.oracle.com/affinity-blocker"
        blocker_label_value = f"affinity-blocker-{self.random_suffix}"
        blocker_pod_names = []
        affinity = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "kubernetes.io/hostname",
                                    "operator": "In",
                                    "values": worker_node_hostnames,
                                },
                            ],
                        },
                    ],
                },
            },
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "labelSelector": {
                            "matchLabels": {
                                blocker_label_key: blocker_label_value,
                            },
                        },
                        "topologyKey": "kubernetes.io/hostname",
                    },
                ],
            },
        }

        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values["deployment"]["affinity"] = affinity

        install_result = None

        try:
            kutil.create_ns(operator_ns, labels={})
            for index, node_name in enumerate(blocker_node_names):
                blocker_pod_name = f"affinity-blocker-{self.random_suffix}-{index}"
                create_blocker_pod(
                    operator_ns,
                    blocker_pod_name,
                    node_name=node_name,
                    labels={blocker_label_key: blocker_label_value},
                )
                blocker_pod_names.append(blocker_pod_name)

            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            self.assertEqual(
                operator_deployment.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("affinity"),
                affinity,
            )
            assert_helm_warning_absent(
                self,
                install_result.output_sections,
                self.affinity_warning_fragment,
            )

            operator_pods = kutil.ls_po(
                install_result.options.namespace,
                pattern=f"{install_result.options.deployment_name}.*",
            )
            self.assertEqual(len(operator_pods), 1)

            operator_pod = kutil.get_po(
                install_result.options.namespace,
                operator_pods[0]["NAME"],
            )
            self.assertEqual(
                operator_pod.get("spec", {}).get("nodeName"),
                target_node_name,
            )
        except Exception:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            delete_pods(operator_ns, blocker_pod_names)
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            uninstall_with_helm(
                release_key=active_release_key,
                delete_namespace=True,
            )

    def test_top_level_affinity_value_emits_warning(self) -> None:
        operator_ns = f"op-affinity-top-helm-{self.random_suffix}"
        operator_name = f"myop-affinity-top-helm-{self.random_suffix}"
        release_name = f"myoper-affinity-top-helm-{self.random_suffix}"
        worker_nodes = get_worker_nodes(self)[:3]
        worker_node_hostnames = [
            worker_node["hostname"] for worker_node in worker_nodes
        ]
        target_node_name = worker_nodes[2]["name"]
        blocker_node_names = [
            worker_node["name"] for worker_node in worker_nodes[:2]
        ]
        blocker_label_key = "e2e.mysql.oracle.com/affinity-blocker"
        blocker_label_value = f"affinity-blocker-{self.random_suffix}"
        blocker_pod_names = []
        affinity = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "kubernetes.io/hostname",
                                    "operator": "In",
                                    "values": worker_node_hostnames,
                                },
                            ],
                        },
                    ],
                },
            },
            "podAntiAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": [
                    {
                        "labelSelector": {
                            "matchLabels": {
                                blocker_label_key: blocker_label_value,
                            },
                        },
                        "topologyKey": "kubernetes.io/hostname",
                    },
                ],
            },
        }

        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values["affinity"] = affinity

        install_result = None

        try:
            kutil.create_ns(operator_ns, labels={})
            for index, node_name in enumerate(blocker_node_names):
                blocker_pod_name = f"affinity-top-blocker-{self.random_suffix}-{index}"
                create_blocker_pod(
                    operator_ns,
                    blocker_pod_name,
                    node_name=node_name,
                    labels={blocker_label_key: blocker_label_value},
                )
                blocker_pod_names.append(blocker_pod_name)

            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            self.assertEqual(
                operator_deployment.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("affinity"),
                affinity,
            )
            assert_helm_warning_present(
                self,
                install_result.output_sections,
                self.affinity_warning_fragment,
            )

            operator_pods = kutil.ls_po(
                install_result.options.namespace,
                pattern=f"{install_result.options.deployment_name}.*",
            )
            self.assertEqual(len(operator_pods), 1)

            operator_pod = kutil.get_po(
                install_result.options.namespace,
                operator_pods[0]["NAME"],
            )
            self.assertEqual(
                operator_pod.get("spec", {}).get("nodeName"),
                target_node_name,
            )
        except Exception:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            delete_pods(operator_ns, blocker_pod_names)
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            uninstall_with_helm(
                release_key=active_release_key,
                delete_namespace=True,
            )

    def test_deployment_affinity_rejects_top_level_affinity(self) -> None:
        affinity = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "kubernetes.io/hostname",
                                    "operator": "In",
                                    "values": ["worker-a", "worker-b", "worker-c"],
                                },
                            ],
                        },
                    ],
                },
            },
        }

        self._assert_placement_value_install_rejected(
            operator_ns=f"op-affinity-conflict-helm-{self.random_suffix}",
            operator_name=f"myop-aff-conf-{self.random_suffix}",
            release_name=f"myoper-affinity-conflict-helm-{self.random_suffix}",
            old_key="affinity",
            new_key="affinity",
            old_value=affinity,
            new_value=affinity,
            error_fragment=(
                "Invalid values: affinity and deployment.affinity cannot both be set"
            ),
        )

    def test_deployment_node_selector_value(self) -> None:
        operator_ns = f"op-nsel-helm-{self.random_suffix}"
        operator_name = f"myop-nsel-helm-{self.random_suffix}"
        release_name = f"myoper-nsel-helm-{self.random_suffix}"
        worker_nodes = get_worker_nodes(self)[:3]
        target_node_name = worker_nodes[0]["name"]
        node_selector = {
            "kubernetes.io/hostname": worker_nodes[0]["hostname"],
        }

        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values["deployment"]["nodeSelector"] = node_selector

        install_result = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            self.assertEqual(
                operator_deployment.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("nodeSelector"),
                node_selector,
            )
            assert_helm_warning_absent(
                self,
                install_result.output_sections,
                self.node_selector_warning_fragment,
            )

            operator_pods = kutil.ls_po(
                install_result.options.namespace,
                pattern=f"{install_result.options.deployment_name}.*",
            )
            self.assertEqual(len(operator_pods), 1)

            operator_pod = kutil.get_po(
                install_result.options.namespace,
                operator_pods[0]["NAME"],
            )
            self.assertEqual(
                operator_pod.get("spec", {}).get("nodeName"),
                target_node_name,
            )
        except Exception:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            uninstall_with_helm(
                release_key=active_release_key,
                delete_namespace=True,
            )

    def test_top_level_node_selector_value_emits_warning(self) -> None:
        operator_ns = f"op-nsel-top-helm-{self.random_suffix}"
        operator_name = f"myop-nsel-top-helm-{self.random_suffix}"
        release_name = f"myoper-nsel-top-helm-{self.random_suffix}"
        worker_nodes = get_worker_nodes(self)[:3]
        target_node_name = worker_nodes[0]["name"]
        node_selector = {
            "kubernetes.io/hostname": worker_nodes[0]["hostname"],
        }

        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values["nodeSelector"] = node_selector

        install_result = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            self.assertEqual(
                operator_deployment.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("nodeSelector"),
                node_selector,
            )
            assert_helm_warning_present(
                self,
                install_result.output_sections,
                self.node_selector_warning_fragment,
            )

            operator_pods = kutil.ls_po(
                install_result.options.namespace,
                pattern=f"{install_result.options.deployment_name}.*",
            )
            self.assertEqual(len(operator_pods), 1)

            operator_pod = kutil.get_po(
                install_result.options.namespace,
                operator_pods[0]["NAME"],
            )
            self.assertEqual(
                operator_pod.get("spec", {}).get("nodeName"),
                target_node_name,
            )
        except Exception:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            uninstall_with_helm(
                release_key=active_release_key,
                delete_namespace=True,
            )

    def test_deployment_node_selector_rejects_top_level_node_selector(self) -> None:
        node_selector = {
            "kubernetes.io/hostname": "worker-a",
        }

        self._assert_placement_value_install_rejected(
            operator_ns=f"op-nsel-mix-helm-{self.random_suffix}",
            operator_name=f"myop-nsel-mix-helm-{self.random_suffix}",
            release_name=f"myoper-nsel-mix-helm-{self.random_suffix}",
            old_key="nodeSelector",
            new_key="nodeSelector",
            old_value=node_selector,
            new_value=node_selector,
            error_fragment=(
                "Invalid values: nodeSelector and deployment.nodeSelector "
                "cannot both be set"
            ),
        )

    def test_deployment_node_selector_value_upgrade_moves_operator_pod(self) -> None:
        operator_ns = f"op-nsel-up-helm-{self.random_suffix}"
        operator_name = f"myop-nsel-up-helm-{self.random_suffix}"
        release_name = f"myoper-nsel-up-helm-{self.random_suffix}"
        worker_nodes = get_worker_nodes(self)[:3]
        initial_node = worker_nodes[0]
        upgraded_node = worker_nodes[1]
        initial_node_selector = {
            "kubernetes.io/hostname": initial_node["hostname"],
        }
        upgraded_node_selector = {
            "kubernetes.io/hostname": upgraded_node["hostname"],
        }

        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values["deployment"]["nodeSelector"] = (
            initial_node_selector
        )

        install_result = None
        upgrade_result = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            initial_operator_pod = self._wait_for_operator_pod_rollout(
                namespace=install_result.options.namespace,
                deployment_name=install_result.options.deployment_name,
            )
            self.assertEqual(
                initial_operator_pod.get("spec", {}).get("nodeName"),
                initial_node["name"],
            )
            self.assertEqual(
                kutil.get_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("nodeSelector"),
                initial_node_selector,
            )
            assert_helm_warning_absent(
                self,
                install_result.output_sections,
                self.node_selector_warning_fragment,
            )

            old_pod_name = initial_operator_pod["metadata"]["name"]
            helm_options.operator_values["deployment"]["nodeSelector"] = (
                upgraded_node_selector
            )

            upgrade_result = upgrade_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            upgraded_operator_pod = self._wait_for_operator_pod_rollout(
                namespace=upgrade_result.options.namespace,
                deployment_name=upgrade_result.options.deployment_name,
                previous_pod_name=old_pod_name,
            )
            self.assertNotEqual(
                upgraded_operator_pod["metadata"]["name"],
                old_pod_name,
            )
            self.assertEqual(
                upgraded_operator_pod.get("spec", {}).get("nodeName"),
                upgraded_node["name"],
            )
            self.assertEqual(
                kutil.get_deploy(
                    upgrade_result.options.namespace,
                    upgrade_result.options.deployment_name,
                )
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("nodeSelector"),
                upgraded_node_selector,
            )
            assert_helm_warning_absent(
                self,
                upgrade_result.output_sections,
                self.node_selector_warning_fragment,
            )
        except Exception:
            active_options = (
                upgrade_result.options
                if upgrade_result is not None
                else (
                    install_result.options
                    if install_result is not None
                    else helm_options
                )
            )
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            active_release_key = (
                upgrade_result.release_key
                if upgrade_result is not None
                else (
                    install_result.release_key
                    if install_result is not None
                    else get_helm_release_target(helm_options)
                )
            )
            uninstall_with_helm(
                release_key=active_release_key,
                delete_namespace=True,
            )

    def test_deployment_affinity_value_upgrade_moves_operator_pod(self) -> None:
        operator_ns = f"op-aff-up-helm-{self.random_suffix}"
        operator_name = f"myop-aff-up-helm-{self.random_suffix}"
        release_name = f"myoper-aff-up-helm-{self.random_suffix}"
        worker_nodes = get_worker_nodes(self)[:3]
        initial_blocker_label_key = "e2e.mysql.oracle.com/affinity-blocker"
        initial_blocker_label_value = f"affinity-init-{self.random_suffix}"
        upgraded_blocker_label_value = f"affinity-up-{self.random_suffix}"
        blocker_pod_names: list[str] = []

        initial_affinity = self._build_affinity_value(
            worker_nodes=worker_nodes,
            blocker_label_key=initial_blocker_label_key,
            blocker_label_value=initial_blocker_label_value,
        )
        upgraded_affinity = self._build_affinity_value(
            worker_nodes=worker_nodes,
            blocker_label_key=initial_blocker_label_key,
            blocker_label_value=upgraded_blocker_label_value,
        )

        helm_options = self._build_placement_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
        )
        helm_options.operator_values["deployment"]["affinity"] = initial_affinity

        install_result = None
        upgrade_result = None

        try:
            kutil.create_ns(operator_ns, labels={})
            blocker_pod_names.extend(
                self._create_blocker_pods(
                    namespace=operator_ns,
                    node_names=[
                        worker_nodes[0]["name"],
                        worker_nodes[1]["name"],
                    ],
                    labels={
                        initial_blocker_label_key: initial_blocker_label_value,
                    },
                    pod_name_prefix="affup-init",
                )
            )

            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            initial_operator_pod = self._wait_for_operator_pod_rollout(
                namespace=install_result.options.namespace,
                deployment_name=install_result.options.deployment_name,
            )
            self.assertEqual(
                initial_operator_pod.get("spec", {}).get("nodeName"),
                worker_nodes[2]["name"],
            )
            self.assertEqual(
                kutil.get_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("affinity"),
                initial_affinity,
            )
            assert_helm_warning_absent(
                self,
                install_result.output_sections,
                self.affinity_warning_fragment,
            )

            blocker_pod_names.extend(
                self._create_blocker_pods(
                    namespace=operator_ns,
                    node_names=[
                        worker_nodes[1]["name"],
                        worker_nodes[2]["name"],
                    ],
                    labels={
                        initial_blocker_label_key: upgraded_blocker_label_value,
                    },
                    pod_name_prefix="affup-next",
                )
            )

            old_pod_name = initial_operator_pod["metadata"]["name"]
            helm_options.operator_values["deployment"]["affinity"] = (
                upgraded_affinity
            )

            upgrade_result = upgrade_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            upgraded_operator_pod = self._wait_for_operator_pod_rollout(
                namespace=upgrade_result.options.namespace,
                deployment_name=upgrade_result.options.deployment_name,
                previous_pod_name=old_pod_name,
            )
            self.assertNotEqual(
                upgraded_operator_pod["metadata"]["name"],
                old_pod_name,
            )
            self.assertEqual(
                upgraded_operator_pod.get("spec", {}).get("nodeName"),
                worker_nodes[0]["name"],
            )
            self.assertEqual(
                kutil.get_deploy(
                    upgrade_result.options.namespace,
                    upgrade_result.options.deployment_name,
                )
                .get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("affinity"),
                upgraded_affinity,
            )
            assert_helm_warning_absent(
                self,
                upgrade_result.output_sections,
                self.affinity_warning_fragment,
            )
        except Exception:
            active_options = (
                upgrade_result.options
                if upgrade_result is not None
                else (
                    install_result.options
                    if install_result is not None
                    else helm_options
                )
            )
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            delete_pods(operator_ns, blocker_pod_names)
            active_release_key = (
                upgrade_result.release_key
                if upgrade_result is not None
                else (
                    install_result.release_key
                    if install_result is not None
                    else get_helm_release_target(helm_options)
                )
            )
            uninstall_with_helm(
                release_key=active_release_key,
                delete_namespace=True,
            )


class HelmStandaloneGlobalOperatorNoPeeringTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_standalone_global_operator_has_no_peering(self) -> None:
        operator_ns = f"op-standalone-helm-{self.random_suffix}"
        operator_name = f"myop-standalone-helm-{self.random_suffix}"
        release_name = f"myoper-helm-standalone-{self.random_suffix}"

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.standalone
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["standalone"] = True

        install_result = None
        test_error = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            envs = get_operator_envs_from_deployment(operator_deployment)

            self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
            assert_no_peering(
                self,
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
        except Exception as exc:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            test_error = exc
            raise
        finally:
            cleanup_error = None
            active_options = install_result.options if install_result is not None else helm_options
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class StandaloneReplicaGuardTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_standalone_operator_rejects_multiple_replicas(self) -> None:
        operator_ns = f"op-stand-repl-{self.random_suffix}"
        operator_name = f"myop-stand-repl-{self.random_suffix}"
        release_name = f"myoper-stand-repl-{self.random_suffix}"
        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        try:
            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                custom_spec={"replicas": 2},
                watch_namespaces=operator_ns,
                standalone=True,
            )

            install_operator(patched, operator_ns)

            deploy = kutil.get_deploy(operator_ns, operator_name)
            self.assertEqual(deploy["spec"]["replicas"], 2)

            pods = kutil.ls_po(operator_ns, pattern=f"{operator_name}.*")
            self.assertGreaterEqual(len(pods), 1)

            error_fragment = (
                "Standalone operator requires exactly one configured replica"
            )
            wait_for_operator_failure_log_fragment(
                operator_ns,
                operator_name,
                error_fragment,
            )
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class HelmStandaloneReplicaGuardTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_standalone_operator_rejects_multiple_replicas(self) -> None:
        operator_ns = f"op-stand-repl-helm-{self.random_suffix}"
        operator_name = f"myop-stand-repl-helm-{self.random_suffix}"
        release_name = f"myoper-helm-stand-repl-{self.random_suffix}"
        error_fragment = (
            "Invalid values: deployment.standalone=true requires replicas=1 "
            "(got 2)"
        )

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the chart values layout.
        # Keep these keys in sync if the chart changes:
        # replicas, deployment.name, deployment.namespaces, deployment.standalone
        helm_options.operator_values["replicas"] = 2
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = [operator_ns]
        helm_options.operator_values["deployment"]["standalone"] = True

        install_error = None
        test_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    "Helm install with deployment.standalone=true and replicas=2 "
                    "unexpectedly succeeded"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, operator_name, check=False),
                msg=(
                    f"Helm chart should reject standalone replicas>1 before "
                    f"creating deployment {operator_ns}/{operator_name}"
                ),
            )

            install_error_output = (
                getattr(install_error, "stdout", None)
                or getattr(install_error, "output", None)
                or str(install_error)
            )
            self.assertIn(error_fragment, install_error_output)
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            try:
                uninstall_with_helm(
                    release_key=get_helm_release_target(helm_options),
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class StandaloneDeploymentStrategyGuardTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_standalone_operator_uses_recreate_strategy(self) -> None:
        operator_ns = f"op-stand-strategy-{self.random_suffix}"
        operator_name = f"myop-stand-strategy-{self.random_suffix}"
        release_name = f"myoper-stand-strategy-{self.random_suffix}"

        artifacts = copy.deepcopy(self.artifacts)

        patched = get_patched_artifacts(
            artifacts,
            release_name,
            operator_ns,
            operator_name,
            watch_namespaces=operator_ns,
            standalone=True,
        )

        self.assertEqual(patched["deployment"]["spec"].get("strategy", {}).get("type"), "Recreate")

    def test_standalone_operator_rejects_unsafe_rolling_update_strategy(self) -> None:
        operator_ns = f"op-stand-unsafe-{self.random_suffix}"
        operator_name = f"myop-stand-unsafe-{self.random_suffix}"
        release_name = f"myoper-stand-unsafe-{self.random_suffix}"
        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        try:
            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                custom_spec={
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {
                            "maxSurge": 1,
                            "maxUnavailable": 0,
                        },
                    },
                },
                watch_namespaces=operator_ns,
                standalone=True,
            )

            install_operator(patched, operator_ns)
            error_fragment = (
                "Standalone operator requires a safe Deployment strategy"
            )
            wait_for_operator_failure_log_fragment(
                operator_ns,
                operator_name,
                error_fragment,
            )
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error

    def test_standalone_operator_allows_safe_rolling_update_strategy(self) -> None:
        operator_ns = f"op-stand-safe-{self.random_suffix}"
        operator_name = f"myop-stand-safe-{self.random_suffix}"
        release_name = f"myoper-stand-safe-{self.random_suffix}"
        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        try:
            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                custom_spec={
                    "strategy": {
                        "type": "RollingUpdate",
                        "rollingUpdate": {
                            "maxSurge": 0,
                            "maxUnavailable": 1,
                        },
                    },
                },
                watch_namespaces=operator_ns,
                standalone=True,
            )

            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            deploy = kutil.get_deploy(operator_ns, operator_name)
            strategy = deploy["spec"].get("strategy", {})
            self.assertEqual(strategy.get("type"), "RollingUpdate")
            rolling_update = strategy.get("rollingUpdate", {})
            self.assertEqual(rolling_update.get("maxSurge"), 0)
            self.assertEqual(rolling_update.get("maxUnavailable"), 1)
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class HelmStandaloneDeploymentStrategyGuardTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _build_standalone_helm_options(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
        deployment_strategy: Optional[dict] = None,
        helm_wait_timeout_seconds: Optional[int] = None,
        source_operator_deployment: Optional[dict] = None,
    ):
        if source_operator_deployment is None:
            source_operator_deployment = kutil.get_deploy(
                self.operator_ns,
                self.operator_deploy_name,
            )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the chart values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.namespaces, deployment.standalone,
        # deployment.strategy
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = [operator_ns]
        helm_options.operator_values["deployment"]["standalone"] = True
        if deployment_strategy is None:
            helm_options.operator_values["deployment"].pop("strategy", None)
        else:
            helm_options.operator_values["deployment"]["strategy"] = deployment_strategy
        helm_options.helm_wait_timeout_seconds = helm_wait_timeout_seconds
        return helm_options

    def test_standalone_operator_uses_recreate_strategy(self) -> None:
        # Keep these names short enough for the chart-derived
        # app.kubernetes.io/instance label value.
        operator_ns = f"op-stand-sgy-{self.random_suffix}"
        operator_name = f"myop-stand-sgy-{self.random_suffix}"
        release_name = f"myoper-helm-stand-sgy-{self.random_suffix}"
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = self._build_standalone_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
            source_operator_deployment=source_operator_deployment,
        )

        install_result = None
        test_error = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            # If deployment.strategy is not set in values.yaml, the standalone
            # chart template renders strategy.type=Recreate.
            deploy = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            strategy = deploy["spec"].get("strategy", {})
            self.assertEqual(strategy.get("type"), "Recreate")
            self.assertIsNone(strategy.get("rollingUpdate"))
        except Exception as exc:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            test_error = exc
            raise
        finally:
            cleanup_error = None
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_standalone_operator_rejects_unsafe_rolling_update_strategy(self) -> None:
        # Keep these names short enough for the chart-derived
        # app.kubernetes.io/instance label value.
        operator_ns = f"op-stand-uns-{self.random_suffix}"
        operator_name = f"myop-stand-uns-{self.random_suffix}"
        release_name = f"myoper-helm-stand-uns-{self.random_suffix}"
        error_fragment = "Standalone operator requires a safe Deployment strategy"
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = self._build_standalone_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
            deployment_strategy={
                "type": "RollingUpdate",
                "rollingUpdate": {
                    "maxSurge": 1,
                    "maxUnavailable": 0,
                },
            },
            helm_wait_timeout_seconds=30,
            source_operator_deployment=source_operator_deployment,
        )

        install_error = None
        test_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    "Helm install with unsafe standalone deployment.strategy "
                    "unexpectedly succeeded"
                ),
            )

            deploy = kutil.get_deploy(operator_ns, operator_name, check=False)
            self.assertIsNotNone(
                deploy,
                msg=(
                    f"Expected Deployment {operator_ns}/{operator_name} to be "
                    f"created before the runtime strategy guard failed"
                ),
            )
            strategy = deploy["spec"].get("strategy", {})
            self.assertEqual(strategy.get("type"), "RollingUpdate")
            rolling_update = strategy.get("rollingUpdate", {})
            self.assertEqual(rolling_update.get("maxSurge"), 1)
            self.assertEqual(rolling_update.get("maxUnavailable"), 0)

            wait_for_operator_failure_log_fragment(
                operator_ns,
                operator_name,
                error_fragment,
            )
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            try:
                uninstall_with_helm(
                    release_key=get_helm_release_target(helm_options),
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_standalone_operator_allows_safe_rolling_update_strategy(self) -> None:
        # Keep these names short enough for the chart-derived
        # app.kubernetes.io/instance label value.
        operator_ns = f"op-stand-saf-{self.random_suffix}"
        operator_name = f"myop-stand-saf-{self.random_suffix}"
        release_name = f"myoper-helm-stand-saf-{self.random_suffix}"
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = self._build_standalone_helm_options(
            operator_ns=operator_ns,
            operator_name=operator_name,
            release_name=release_name,
            deployment_strategy={
                "type": "RollingUpdate",
                "rollingUpdate": {
                    "maxSurge": 0,
                    "maxUnavailable": 1,
                },
            },
            source_operator_deployment=source_operator_deployment,
        )

        install_result = None
        test_error = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            deploy = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            strategy = deploy["spec"].get("strategy", {})
            self.assertEqual(strategy.get("type"), "RollingUpdate")
            rolling_update = strategy.get("rollingUpdate", {})
            self.assertEqual(rolling_update.get("maxSurge"), 0)
            self.assertEqual(rolling_update.get("maxUnavailable"), 1)
        except Exception as exc:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            test_error = exc
            raise
        finally:
            cleanup_error = None
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class MultiScopedStandaloneOpsNoPeeringTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_standalone_operators_have_no_peering(self) -> None:
        operators_to_clean = []
        artifacts = self.remove_operator()

        try:
            for i in (1, 2):
                operator_ns = f"op-stand-{i}-{self.random_suffix}"
                operator_name = f"myop-stand-{i}-{self.random_suffix}"
                release_name = f"myoper-stand-{i}-{self.random_suffix}"

                operators_to_clean.append((operator_ns, operator_name))

                patched = get_patched_artifacts(
                    artifacts,
                    release_name,
                    operator_ns,
                    operator_name,
                    watch_namespaces=operator_ns,
                    standalone=True,
                )
                self.assertEqual(patched["clusterkopfpeerings"], {})
                self.assertEqual(patched["kopfpeerings"], {})

                install_operator(patched, operator_ns)
                kutil.wait_deploy(operator_ns, operator_name, timeout=300)

                operator_deployment = kutil.get_deploy(operator_ns, operator_name)
                envs = get_operator_envs_from_deployment(operator_deployment)

                self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
                self.assertEqual(envs["OPERATOR_NAMESPACES"], operator_ns)
                assert_no_peering(self, operator_ns, operator_name)
        except Exception as exc:
            for operator_ns, operator_name in operators_to_clean:
                print_operator_log(operator_ns, operator_name)
            raise

        for operator_ns, operator_name in reversed(operators_to_clean):
            remove_operator(
                operator_ns=operator_ns,
                operator_deploy_name=operator_name,
                crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
                crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
                ckopf_peerings_pattern=get_ckopf_peerings_pattern(operator_ns, operator_name),
                sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
                role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
                role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
                kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
                return_manifests=False
            )


class HelmMultiScopedStandaloneOpsNoPeeringTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_standalone_operators_have_no_peering(self) -> None:
        operators_to_clean = []
        test_error = None

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        try:
            for i in (1, 2):
                operator_ns = f"op-stand-{i}-{self.random_suffix}"
                operator_name = f"myop-stand-{i}-{self.random_suffix}"
                release_name = f"myoper-helm-scoped-stand-{i}-{self.random_suffix}"

                helm_options = resolve_install_with_helm_options(
                    namespace=operator_ns,
                    source_operator_namespace=self.operator_ns,
                    source_operator_deployment_name=self.operator_deploy_name,
                    source_operator_deployment=source_operator_deployment,
                )
                helm_options.release_name = release_name
                # See helm/mysql-operator/values.yaml for the deployment values layout.
                # Keep these keys in sync if the chart changes:
                # deployment.name, deployment.namespaces, deployment.standalone
                helm_options.operator_values["deployment"]["name"] = operator_name
                helm_options.operator_values["deployment"]["namespaces"] = [operator_ns]
                helm_options.operator_values["deployment"]["standalone"] = True
                operators_to_clean.append(helm_options)

                install_result = install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
                kutil.wait_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                    timeout=300,
                )

                operator_deployment = kutil.get_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                envs = get_operator_envs_from_deployment(operator_deployment)

                self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
                self.assertEqual(envs["OPERATOR_NAMESPACES"], operator_ns)
                assert_no_peering(
                    self,
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
        except Exception as exc:
            test_error = exc
            for options in operators_to_clean:
                try:
                    print_operator_log(options.namespace, options.deployment_name)
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                for options in reversed(operators_to_clean):
                    try:
                        uninstall_with_helm(
                            release_key=get_helm_release_target(options),
                            delete_namespace=True,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class HelmOperatorStatusQuoTest(OperatorSingleAndMultipleBaseTest):
    def test_00_status_quo(self) -> None:
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        helm_options = resolve_install_with_helm_options(
            namespace=self.operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        install_result = None

        try:
            install_result = install_with_helm(
                self.operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )
            helm_artifacts = get_operator_artifacts(
                operator_ns=install_result.options.namespace,
                operator_deploy_name=install_result.options.deployment_name,
                crole_names_pattern=self.crole_names_pattern,
                crole_binding_pattern=self.crole_binding_pattern,
                ckopf_peerings_pattern=self.ckopf_peerings_pattern,
                sa_names_pattern=self.sa_names_pattern,
                role_names_pattern=self.role_names_pattern,
                role_binding_pattern=self.role_binding_pattern,
                kopf_peerings_pattern=self.kopf_peerings_pattern,
            )
            self.assertIsNotNone(helm_artifacts)
            assert_operator_status_quo(
                self,
                helm_artifacts,
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
        except Exception:
            if install_result is not None:
                try:
                    print_operator_log(
                        install_result.options.namespace,
                        install_result.options.deployment_name,
                    )
                except Exception:
                    pass
            raise
        finally:
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                )
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)


class HelmStandardOperatorUpgradeTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 1
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_install_previous_release_then_upgrade_current_release_single_server_single_router(
        self,
    ) -> None:
        self._run_helm_operator_upgrade_cycle(
            server_instances=1,
            router_instances=1,
        )

    def test_install_previous_release_then_upgrade_current_release_three_servers_single_router(
        self,
    ) -> None:
        self._run_helm_operator_upgrade_cycle(
            server_instances=3,
            router_instances=1,
        )


class HelmGlobalToScopedOperatorUpgradeTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 1
    routers_count = 1
    upgrade_error_fragment = (
        "Invalid values: operator topology is immutable after install."
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_install_previous_release_then_upgrade_global_operator_as_scoped_fails(
        self,
    ) -> None:
        self._run_helm_operator_upgrade_cycle(
            server_instances=1,
            router_instances=1,
            upgraded_operator_overrides={
                "deployment": {
                    "namespaces": [self.ns],
                },
            },
            expect_operator_upgrade_failure=True,
            expected_operator_upgrade_error_fragment=self.upgrade_error_fragment,
        )


class RawManifestOperatorSelectorUpgradeCompatibilityTest(
    OperatorSingleAndMultipleBaseTest
):
    def test_raw_manifest_operator_upgrade_preserves_legacy_selector(self) -> None:
        original_artifacts = self._remove_default_operator_or_fail()
        operator_pod = None
        test_error = None

        try:
            for release in get_raw_manifest_upgrade_release_chain():
                previous_pod_name = (
                    operator_pod["metadata"]["name"] if operator_pod else None
                )
                operator_pod = self._apply_raw_operator_release(
                    release,
                    previous_pod_name=previous_pod_name,
                )
        except Exception as exc:
            test_error = exc
            self._print_operator_log_from_candidates(
                namespace=self.operator_ns,
                deployment_names=[self.operator_deploy_name],
            )
            raise
        finally:
            cleanup_error = None
            try:
                self._remove_live_default_operator_if_present()
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class RawManifestOperatorAndClusterLtsBridgeUpgradeTest(
    OperatorSingleAndMultipleBaseTest
):
    cluster_size = 1
    routers_count = 1

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _apply_raw_cluster(
        self,
        *,
        cluster_name: str,
        release: str,
    ) -> None:
        if self.ns not in kutil.ls_ns_ex():
            kutil.create_ns(self.ns, labels={})
        kutil.create_user_secrets(
            self.ns,
            self.cluster_secret_name,
            root_user="root",
            root_host="%",
            root_pass="sakila",
        )
        kutil.apply(
            self.ns,
            f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  version: "{self._mysql_version_from_release(release)}"
  podSpec:
    terminationGracePeriodSeconds: 5
""",
        )

    def test_raw_manifest_operator_and_cluster_upgrade_lts_bridge(self) -> None:
        release_chain = get_raw_manifest_upgrade_release_chain()
        cluster_name = f"{self.cluster_name}-raw-upg"
        original_artifacts = self._remove_default_operator_or_fail()
        operator_pod = None
        current_cluster_release = release_chain[0]
        test_error = None

        try:
            operator_pod = self._apply_raw_operator_release(current_cluster_release)
            self._apply_raw_cluster(
                cluster_name=cluster_name,
                release=current_cluster_release,
            )
            self._wait_for_helm_cluster_ready(
                namespace=self.ns,
                cluster_name=cluster_name,
                server_instances=self.cluster_size,
                router_instances=self.routers_count,
            )
            self._assert_helm_cluster_runtime_image_identities(
                namespace=self.ns,
                cluster_name=cluster_name,
                server_instances=self.cluster_size,
                router_instances=self.routers_count,
                expected_release=current_cluster_release,
                check_operator_image_tag=(
                    self._should_check_raw_manifest_cluster_operator_image_tag(
                        current_cluster_release
                    )
                ),
            )
            self._assert_helm_cluster_cr_version(
                namespace=self.ns,
                cluster_name=cluster_name,
                expected_version=self._mysql_version_from_release(
                    current_cluster_release
                ),
            )

            for next_release in release_chain[1:]:
                previous_pod_name = operator_pod["metadata"]["name"]
                operator_pod = self._apply_raw_operator_release(
                    next_release,
                    previous_pod_name=previous_pod_name,
                )
                self._wait_for_helm_cluster_ready(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=self.cluster_size,
                    router_instances=self.routers_count,
                )
                self._assert_helm_cluster_runtime_image_identities(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=self.cluster_size,
                    router_instances=self.routers_count,
                    expected_release=current_cluster_release,
                    check_operator_image_tag=(
                        self._should_check_raw_manifest_cluster_operator_image_tag(
                            current_cluster_release
                        )
                    ),
                )

                server_rollover_waiter = tutil.get_sts_rollover_update_waiter(
                    self,
                    cluster_name,
                    timeout=900,
                    delay=10,
                )
                router_rollover_waiter = (
                    tutil.get_router_deploy_rollover_update_waiter(
                        self,
                        cluster_name,
                        timeout=200,
                        delay=10,
                    )
                    if self.routers_count
                    else None
                )

                kutil.patch_ic(
                    self.ns,
                    cluster_name,
                    {
                        "spec": {
                            "version": self._mysql_version_from_release(
                                next_release
                            )
                        }
                    },
                    type="merge",
                )
                server_rollover_waiter()
                if router_rollover_waiter is not None:
                    router_rollover_waiter()

                current_cluster_release = next_release
                self._wait_for_helm_cluster_ready(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=self.cluster_size,
                    router_instances=self.routers_count,
                )
                self._assert_helm_cluster_runtime_image_identities(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=self.cluster_size,
                    router_instances=self.routers_count,
                    expected_release=current_cluster_release,
                    check_operator_image_tag=(
                        self._should_check_raw_manifest_cluster_operator_image_tag(
                            current_cluster_release
                        )
                    ),
                )
                self._assert_helm_cluster_cr_version(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    expected_version=self._mysql_version_from_release(
                        current_cluster_release
                    ),
                )
        except Exception as exc:
            test_error = exc
            self._print_helm_cluster_pod_diagnostics(
                namespace=self.ns,
                cluster_name=cluster_name,
            )
            self._print_operator_log_from_candidates(
                namespace=self.operator_ns,
                deployment_names=[self.operator_deploy_name],
            )
            raise
        finally:
            cleanup_error = None
            try:
                self._cleanup_raw_cluster(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    delete_pvcs=True,
                )
            except Exception as exc:
                cleanup_error = exc

            try:
                self._remove_live_default_operator_if_present()
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class RawGlobalOperatorTopologyRestartGuardTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_bootstrapped_global_operator_cannot_restart_as_scoped(self) -> None:
        operator_ns = f"op-topology-restart-{self.random_suffix}"
        operator_name = f"myop-topology-restart-{self.random_suffix}"
        release_name = f"myoper-topology-restart-{self.random_suffix}"
        scoped_watch_namespace = f"db-topology-restart-{self.random_suffix}"
        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        patched = get_patched_artifacts(
            copy.deepcopy(original_artifacts),
            release_name,
            operator_ns,
            operator_name,
        )
        apply_initial_raw_manifest_operator_fingerprint(patched["deployment"])
        patched["deployment"].setdefault("metadata", {}).setdefault(
            "annotations",
            {},
        ).pop(TOPOLOGY_ANNOTATION_KEY, None)

        try:
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            deployed_operator = kutil.get_deploy(operator_ns, operator_name)
            self.assertEqual(
                deployed_operator.get("metadata", {})
                .get("annotations", {})
                .get(TOPOLOGY_ANNOTATION_KEY),
                get_topology_annotation_value("", False),
            )

            operator_container = get_named_container(
                deployed_operator.get("spec", {})
                .get("template", {})
                .get("spec", {})
                .get("containers", []),
                "mysql-operator",
            )
            self.assertIsNotNone(operator_container)
            updated_env = copy.deepcopy(operator_container.get("env", []))
            for env in updated_env:
                if env.get("name") == "OPERATOR_NAMESPACES":
                    env["value"] = scoped_watch_namespace
                if env.get("name") == "OPERATOR_STANDALONE":
                    env["value"] = "false"

            kutil.patch_dp(
                operator_ns,
                operator_name,
                {
                    "spec": {
                        "template": {
                            "spec": {
                                "containers": [
                                    {
                                        "name": "mysql-operator",
                                        "env": updated_env,
                                    }
                                ]
                            }
                        }
                    }
                },
            )

            wait_for_operator_failure_log_fragment(
                operator_ns,
                operator_name,
                "persisted mysql.oracle.com/operator-topology as global non-standalone",
            )
        except Exception as exc:
            test_error = exc
            try:
                print_operator_log(operator_ns, operator_name)
            except Exception:
                pass
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                    check_namespace_empty=False,
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as cleanup_exc:
                    if cleanup_error is None:
                        cleanup_error = cleanup_exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class HelmScopedTopologyFreezeUpgradeTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _run_scoped_topology_upgrade(
        self,
        *,
        initial_namespaces: list[str],
        upgraded_namespaces: list[str],
        initial_standalone: bool,
        upgraded_standalone: bool,
        expect_upgrade_failure: bool,
        expected_error_fragment: str | None = None,
    ) -> None:
        operator_ns = f"op-scoped-topo-{self.random_suffix}"
        operator_name = f"myop-scoped-topo-{self.random_suffix}"
        release_name = f"myoper-helm-scoped-topo-{self.random_suffix}"
        namespaces_to_clean = sorted(set(initial_namespaces) | set(upgraded_namespaces))
        test_error = None

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        install_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        install_options.release_name = release_name
        install_options.operator_values["deployment"]["name"] = operator_name
        install_options.operator_values["deployment"]["namespaces"] = initial_namespaces
        install_options.operator_values["deployment"]["standalone"] = initial_standalone

        upgrade_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        upgrade_options.release_name = release_name
        upgrade_options.operator_values["deployment"]["name"] = operator_name
        upgrade_options.operator_values["deployment"]["namespaces"] = upgraded_namespaces
        upgrade_options.operator_values["deployment"]["standalone"] = upgraded_standalone

        install_result = None
        upgrade_result = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=install_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            installed_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            installed_envs = get_operator_envs_from_deployment(installed_deployment)
            self.assertEqual(
                installed_envs["OPERATOR_NAMESPACES"],
                ",".join(initial_namespaces),
            )
            self.assertEqual(
                installed_envs["OPERATOR_STANDALONE"],
                str(initial_standalone).lower(),
            )

            if expect_upgrade_failure:
                upgrade_error = None
                try:
                    upgrade_with_helm(
                        operator_ns,
                        resolved_options=upgrade_options,
                    )
                except Exception as exc:
                    upgrade_error = exc

                self.assertIsNotNone(
                    upgrade_error,
                    msg="Scoped topology upgrade unexpectedly succeeded",
                )
                if expected_error_fragment is not None:
                    upgrade_error_output = (
                        getattr(upgrade_error, "stdout", None)
                        or getattr(upgrade_error, "output", None)
                        or str(upgrade_error)
                    )
                    self.assertIn(expected_error_fragment, upgrade_error_output)

                current_deployment = kutil.get_deploy(operator_ns, operator_name)
                current_envs = get_operator_envs_from_deployment(current_deployment)
                self.assertEqual(
                    current_envs["OPERATOR_NAMESPACES"],
                    ",".join(initial_namespaces),
                )
                self.assertEqual(
                    current_envs["OPERATOR_STANDALONE"],
                    str(initial_standalone).lower(),
                )
            else:
                upgrade_result = upgrade_with_helm(
                    operator_ns,
                    resolved_options=upgrade_options,
                )
                kutil.wait_deploy(
                    upgrade_result.options.namespace,
                    upgrade_result.options.deployment_name,
                    timeout=300,
                )
                current_deployment = kutil.get_deploy(operator_ns, operator_name)
                current_envs = get_operator_envs_from_deployment(current_deployment)
                self.assertEqual(
                    current_envs["OPERATOR_NAMESPACES"],
                    ",".join(upgraded_namespaces),
                )
                self.assertEqual(
                    current_envs["OPERATOR_STANDALONE"],
                    str(upgraded_standalone).lower(),
                )
        except Exception as exc:
            test_error = exc
            try:
                print_operator_log(operator_ns, operator_name)
            except Exception:
                pass
            raise
        finally:
            cleanup_error = None
            try:
                try:
                    uninstall_with_helm(
                        release_key=(
                            upgrade_result.release_key
                            if upgrade_result is not None
                            else (
                                install_result.release_key
                                if install_result is not None
                                else get_helm_release_target(install_options)
                            )
                        ),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                for namespace in reversed(namespaces_to_clean):
                    try:
                        if namespace in kutil.ls_ns_ex():
                            kutil.delete_ns(namespace)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_scoped_namespace_set_cannot_grow(self) -> None:
        ns_a = f"db-scoped-grow-a-{self.random_suffix}"
        ns_b = f"db-scoped-grow-b-{self.random_suffix}"
        self._run_scoped_topology_upgrade(
            initial_namespaces=[ns_a],
            upgraded_namespaces=[ns_a, ns_b],
            initial_standalone=False,
            upgraded_standalone=False,
            expect_upgrade_failure=True,
            expected_error_fragment=(
                "Invalid values: operator watched namespace set is immutable after install."
            ),
        )

    def test_scoped_namespace_set_cannot_shrink(self) -> None:
        ns_a = f"db-scoped-shrink-a-{self.random_suffix}"
        ns_b = f"db-scoped-shrink-b-{self.random_suffix}"
        self._run_scoped_topology_upgrade(
            initial_namespaces=[ns_a, ns_b],
            upgraded_namespaces=[ns_a],
            initial_standalone=False,
            upgraded_standalone=False,
            expect_upgrade_failure=True,
            expected_error_fragment=(
                "Invalid values: operator watched namespace set is immutable after install."
            ),
        )

    def test_scoped_operator_cannot_flip_standalone(self) -> None:
        ns_a = f"db-scoped-standalone-{self.random_suffix}"
        self._run_scoped_topology_upgrade(
            initial_namespaces=[ns_a],
            upgraded_namespaces=[ns_a],
            initial_standalone=False,
            upgraded_standalone=True,
            expect_upgrade_failure=True,
            expected_error_fragment=(
                "Invalid values: deployment.standalone is immutable after install."
            ),
        )

    def test_reordered_equivalent_namespace_set_is_allowed(self) -> None:
        ns_a = f"db-scoped-reorder-a-{self.random_suffix}"
        ns_b = f"db-scoped-reorder-b-{self.random_suffix}"
        self._run_scoped_topology_upgrade(
            initial_namespaces=[ns_b, ns_a],
            upgraded_namespaces=[ns_a, ns_b],
            initial_standalone=False,
            upgraded_standalone=False,
            expect_upgrade_failure=False,
        )


class _HelmLegacySwitchoverRbacUpgradeBase(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1
    expect_legacy_switchover_objects_before_current = True
    release_chain: list[str] = []

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def setUp(self):
        super().setUp()
        self._active_mysql_upgrade_abort_check: Optional[
            _MysqlUpgradeAbortCheck
        ] = None

    def check_pod_errors(self):
        super().check_pod_errors()
        self._check_active_mysql_upgrade_abort_condition()

    def _activate_mysql_upgrade_abort_checks(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        server_instances: int,
        previous_release: str,
        target_release: str,
    ) -> None:
        if self._mysql_version_from_release(
            previous_release
        ) == self._mysql_version_from_release(target_release):
            self._active_mysql_upgrade_abort_check = None
            return

        self._active_mysql_upgrade_abort_check = _MysqlUpgradeAbortCheck(
            namespace=namespace,
            cluster_names=tuple(cluster_names),
            server_instances=server_instances,
            previous_release=previous_release,
            target_release=target_release,
        )

    def _clear_mysql_upgrade_abort_checks(self) -> None:
        self._active_mysql_upgrade_abort_check = None

    @staticmethod
    def _get_fatal_mysql_upgrade_log_lines(log_contents: str) -> Optional[list[str]]:
        log_lines = log_contents.splitlines()
        abort_lines = [
            line
            for line in log_lines
            if "Aborting" in line
        ]

        invalid_upgrade_lines = [
            line
            for line in log_lines
            if (
                "Invalid MySQL server upgrade:" in line
                or any(code in line for code in INVALID_MYSQL_UPGRADE_ERROR_CODES)
            )
        ]
        if invalid_upgrade_lines:
            fatal_lines = invalid_upgrade_lines[:1]
            if abort_lines and abort_lines[0] != fatal_lines[0]:
                fatal_lines.append(abort_lines[0])
            return fatal_lines

        fatal_dd_error_lines = [
            line
            for line in log_lines
            if (
                any(code in line for code in FATAL_DD_UPGRADE_ERROR_CODES)
                or any(
                    fragment in line
                    for fragment in FATAL_DD_UPGRADE_ERROR_FRAGMENTS
                )
            )
        ]
        if fatal_dd_error_lines:
            fatal_lines = fatal_dd_error_lines[:1]
            if abort_lines and abort_lines[0] != fatal_lines[0]:
                fatal_lines.append(abort_lines[0])
            return fatal_lines

        dd_upgrade_started_lines = [
            line
            for line in log_lines
            if "Data dictionary upgrading from version" in line
        ]
        dd_upgrade_completed_lines = [
            line
            for line in log_lines
            if "Data dictionary upgrade from version" in line
            and "completed" in line
        ]
        if not dd_upgrade_started_lines or dd_upgrade_completed_lines or not abort_lines:
            return None

        fatal_lines = [dd_upgrade_started_lines[0]]
        if abort_lines[0] != fatal_lines[0]:
            fatal_lines.append(abort_lines[0])
        return fatal_lines

    def _check_active_mysql_upgrade_abort_condition(self) -> None:
        active_check = self._active_mysql_upgrade_abort_check
        if active_check is None:
            return

        previous_version = self._mysql_version_from_release(
            active_check.previous_release
        )
        target_version = self._mysql_version_from_release(
            active_check.target_release
        )

        for cluster_name in active_check.cluster_names:
            for instance in range(active_check.server_instances):
                pod_name = f"{cluster_name}-{instance}"
                pod = kutil.get_po(
                    active_check.namespace,
                    pod_name,
                    check=False,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
                if not pod:
                    continue

                mysql_status = get_named_container(
                    pod.get("status", {}).get("containerStatuses", []),
                    "mysql",
                )
                if mysql_status is None:
                    continue

                if "running" in mysql_status.get("state", {}):
                    continue

                try:
                    combined_logs = self._get_pod_container_combined_logs(
                        namespace=active_check.namespace,
                        pod_name=pod_name,
                        container_name="mysql",
                    )
                except Exception:
                    continue

                fatal_lines = self._get_fatal_mysql_upgrade_log_lines(
                    combined_logs
                )
                if not fatal_lines:
                    continue

                pod_phase = pod.get("status", {}).get("phase")
                container_state = mysql_status.get("state", {})
                matched_lines = "\n".join(fatal_lines)
                raise AssertionError(
                    f"Detected fatal MySQL upgrade failure while upgrading "
                    f"{active_check.namespace}/{pod_name} from {previous_version} "
                    f"to {target_version}. Pod phase={pod_phase}, "
                    f"mysql state={container_state}. Matched log lines:\n"
                    f"{matched_lines}\n"
                    f"Log tail:\n{_tail_text(combined_logs, lines=200)}"
                )

    def _build_legacy_switchover_cluster_names(self) -> list[str]:
        return [
            f"{self.cluster_name}-swra",
            f"{self.cluster_name}-swrb",
        ]

    def _build_legacy_switchover_cluster_values(self) -> dict:
        def resources(cpu: str, memory: str, ephemeral_storage: str) -> dict:
            return {
                "requests": {
                    "cpu": cpu,
                    "memory": memory,
                    "ephemeral-storage": ephemeral_storage,
                },
            }

        return {
            "credentials": {
                "root": {
                    "user": "root",
                    "password": "sakila",
                    "host": "%",
                },
            },
            "tls": {
                "useSelfSigned": True,
            },
            "router": {
                "podSpec": {
                    "containers": [
                        {
                            "name": "router",
                            "resources": resources("50m", "64Mi", "64Mi"),
                        },
                    ],
                },
            },
            "podSpec": {
                "terminationGracePeriodSeconds": 5,
                "initContainers": [
                    {
                        "name": "fixdatadir",
                        "resources": resources("50m", "64Mi", "64Mi"),
                    },
                    {
                        "name": "initconf",
                        "resources": resources("50m", "64Mi", "64Mi"),
                    },
                    {
                        "name": "initmysql",
                        "resources": resources("100m", "128Mi", "128Mi"),
                    },
                ],
                "containers": [
                    {
                        "name": "sidecar",
                        "resources": resources("50m", "64Mi", "64Mi"),
                    },
                    {
                        "name": "mysql",
                        "resources": resources("100m", "128Mi", "128Mi"),
                    },
                ],
            },
        }

    def _build_legacy_switchover_operator_options(
        self,
        *,
        app_version: str,
        chart_source_overrides: dict,
    ) -> HelmOperatorInstallOptions:
        return HelmOperatorInstallOptions(
            namespace=self.operator_ns,
            app_version=app_version,
            release_name=self.operator_deploy_name,
            kube_context=g_ts_cfg.k8s_context,
            helm_package="mysql-operator",
            operator_values=chart_source_overrides,
            use_chart_defaults=True,
        )

    @staticmethod
    def _pod_container_has_previous_logs(
        pod: dict,
        container_name: str,
    ) -> bool:
        pod_status = pod.get("status", {})
        for status_key in ("initContainerStatuses", "containerStatuses"):
            for status in pod_status.get(status_key, []):
                if status.get("name") != container_name:
                    continue
                if status.get("restartCount", 0) > 0:
                    return True
                if status.get("lastState", {}).get("terminated"):
                    return True
                return False
        return False

    @staticmethod
    def _pod_container_names(pod: dict) -> list[str]:
        container_names: list[str] = []
        for container_key in ("initContainers", "containers"):
            for container in pod.get("spec", {}).get(container_key, []):
                container_name = container.get("name")
                if container_name and container_name not in container_names:
                    container_names.append(container_name)
        return container_names

    def _print_helm_cluster_pod_diagnostics(
        self,
        *,
        namespace: str,
        cluster_name: str,
    ) -> None:
        print(f"==== Cluster pod diagnostics for {namespace}/{cluster_name} ====")

        try:
            pods = sorted(
                kutil.ls_po(namespace, pattern=f"{cluster_name}.*"),
                key=lambda pod: pod["NAME"],
            )
        except Exception as exc:
            print(f"Failed to list pods for {namespace}/{cluster_name}: {exc}")
            return

        if not pods:
            print(f"No pods found for {namespace}/{cluster_name}")
            return

        for pod_row in pods:
            pod_name = pod_row["NAME"]
            pod = kutil.get_po(
                namespace,
                pod_name,
                check=False,
                cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
            )
            if pod:
                print(f"==== image identities for {namespace}/{pod_name} ====")
                for container_key, status_key in (
                    ("initContainers", "initContainerStatuses"),
                    ("containers", "containerStatuses"),
                ):
                    for container in pod.get("spec", {}).get(container_key, []):
                        container_name = container.get("name")
                        status = get_named_container(
                            pod.get("status", {}).get(status_key, []),
                            container_name,
                        )
                        runtime_image_id = None
                        if status is not None:
                            runtime_image_id = status.get("imageID")
                        print(
                            f"{container_key}.{container_name}: "
                            f"image={container.get('image')} "
                            f"imageID={runtime_image_id}"
                        )
            print(f"==== describe pod {namespace}/{pod_name} ====")
            try:
                print(
                    kutil.describe_po(
                        namespace,
                        pod_name,
                        cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                    )
                )
            except Exception as exc:
                print(f"Failed to describe pod {namespace}/{pod_name}: {exc}")

            if not pod:
                print(f"Pod {namespace}/{pod_name} no longer exists")
                continue

            for container_name in self._pod_container_names(pod):
                print(
                    f"==== logs pod {namespace}/{pod_name} container {container_name} ===="
                )
                try:
                    print(
                        kutil.logs(
                            namespace,
                            [pod_name, container_name],
                            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                        )
                    )
                except Exception as exc:
                    print(
                        f"Failed to fetch current logs for "
                        f"{namespace}/{pod_name} container {container_name}: {exc}"
                    )

                if not self._pod_container_has_previous_logs(pod, container_name):
                    continue

                print(
                    f"==== previous logs pod {namespace}/{pod_name} "
                    f"container {container_name} ===="
                )
                try:
                    print(
                        kutil.logs(
                            namespace,
                            [pod_name, container_name],
                            prev=True,
                            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                        )
                    )
                except Exception as exc:
                    print(
                        f"Failed to fetch previous logs for "
                        f"{namespace}/{pod_name} container {container_name}: {exc}"
                    )

    def _get_pod_container_combined_logs(
        self,
        *,
        namespace: str,
        pod_name: str,
        container_name: str,
    ) -> str:
        pod = kutil.get_po(
            namespace,
            pod_name,
            check=False,
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        self.assertIsNotNone(
            pod,
            msg=f"Expected pod {namespace}/{pod_name} to exist when reading logs",
        )

        sections: list[str] = []
        if pod and self._pod_container_has_previous_logs(pod, container_name):
            try:
                previous = kutil.logs(
                    namespace,
                    [pod_name, container_name],
                    prev=True,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
            except Exception as exc:
                previous = f"Failed to fetch previous logs: {exc}"
            if previous:
                sections.append(previous)

        current = kutil.logs(
            namespace,
            [pod_name, container_name],
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
        )
        if current:
            sections.append(current)

        return "\n".join(sections)

    def _assert_mysql_server_upgrade_logged(
        self,
        *,
        namespace: str,
        cluster_name: str,
        server_instances: int,
        previous_release: str,
        target_release: str,
    ) -> None:
        previous_version = self._mysql_version_from_release(previous_release)
        target_version = self._mysql_version_from_release(target_release)

        if previous_version == target_version:
            return

        previous_digits = self._mysql_version_to_upgrade_digits(previous_version)
        target_digits = self._mysql_version_to_upgrade_digits(target_version)
        startup_pattern = re.compile(
            rf"mysqld \(mysqld {re.escape(target_version)}\) starting as process\b"
        )
        dd_upgrade_started_pattern = re.compile(
            r"Data dictionary upgrading from version '\d+' to '\d+'\."
        )
        dd_upgrade_completed_pattern = re.compile(
            r"Data dictionary upgrade from version '\d+' to '\d+' completed\."
        )
        dd_in_use_pattern = re.compile(
            r"Using data dictionary with version '\d+'\."
        )
        server_upgrade_preparing_pattern = re.compile(
            rf"MySQL server upgrading from version '{re.escape(previous_digits)}' "
            rf"to '{re.escape(target_digits)}'\.?"
        )
        server_upgrade_started_pattern = re.compile(
            rf"Server upgrade from '{re.escape(previous_digits)}' "
            rf"to '{re.escape(target_digits)}' started\.?"
        )
        server_upgrade_completed_pattern = re.compile(
            rf"Server upgrade from '{re.escape(previous_digits)}' "
            rf"to '{re.escape(target_digits)}' completed\.?"
        )

        for instance in range(server_instances):
            pod_name = f"{cluster_name}-{instance}"
            combined_logs = self._get_pod_container_combined_logs(
                namespace=namespace,
                pod_name=pod_name,
                container_name="mysql",
            )
            startup_match = startup_pattern.search(combined_logs)
            dd_upgrade_started_match = dd_upgrade_started_pattern.search(
                combined_logs
            )
            dd_upgrade_completed_match = dd_upgrade_completed_pattern.search(
                combined_logs
            )
            dd_in_use_match = dd_in_use_pattern.search(combined_logs)
            server_upgrade_preparing_match = (
                server_upgrade_preparing_pattern.search(combined_logs)
            )
            server_upgrade_started_match = (
                server_upgrade_started_pattern.search(combined_logs)
            )
            server_upgrade_completed_match = (
                server_upgrade_completed_pattern.search(combined_logs)
            )

            self.assertIsNotNone(
                startup_match,
                msg=(
                    f"Did not find mysql startup line for upgraded server "
                    f"{namespace}/{pod_name} at {target_version}. "
                    f"Log tail:\n{_tail_text(combined_logs, lines=200)}"
                ),
            )
            self.assertIsNotNone(
                dd_in_use_match,
                msg=(
                    f"Did not find post-upgrade data dictionary version line for "
                    f"{namespace}/{pod_name}. Log tail:\n"
                    f"{_tail_text(combined_logs, lines=200)}"
                ),
            )
            self.assertIsNotNone(
                server_upgrade_preparing_match,
                msg=(
                    f"Did not find mysql server preparing-upgrade line for "
                    f"{namespace}/{pod_name}: expected upgrade from "
                    f"{previous_digits} to {target_digits}. "
                    f"Log tail:\n{_tail_text(combined_logs, lines=200)}"
                ),
            )
            self.assertIsNotNone(
                server_upgrade_started_match,
                msg=(
                    f"Did not find mysql server upgrade start line for "
                    f"{namespace}/{pod_name}: expected upgrade from "
                    f"{previous_digits} to {target_digits}. "
                    f"Log tail:\n{_tail_text(combined_logs, lines=200)}"
                ),
            )
            self.assertIsNotNone(
                server_upgrade_completed_match,
                msg=(
                    f"Did not find mysql server upgrade completion line for "
                    f"{namespace}/{pod_name}: expected upgrade from "
                    f"{previous_digits} to {target_digits}. "
                    f"Log tail:\n{_tail_text(combined_logs, lines=200)}"
                ),
            )
            self.assertEqual(
                dd_upgrade_started_match is None,
                dd_upgrade_completed_match is None,
                msg=(
                    f"Incomplete data dictionary upgrade logging for "
                    f"{namespace}/{pod_name}. Expected either both DD start "
                    f"and completion lines or neither. Log tail:\n"
                    f"{_tail_text(combined_logs, lines=200)}"
                ),
            )

            assert startup_match is not None
            assert dd_in_use_match is not None
            assert server_upgrade_preparing_match is not None
            assert server_upgrade_started_match is not None
            assert server_upgrade_completed_match is not None
            print(
                f"MySQL startup line for {namespace}/{pod_name}: "
                f"{startup_match.group(0)}"
            )
            if dd_upgrade_started_match is not None:
                assert dd_upgrade_completed_match is not None
                print(
                    f"MySQL data dictionary upgrade start line for "
                    f"{namespace}/{pod_name}: {dd_upgrade_started_match.group(0)}"
                )
                print(
                    f"MySQL data dictionary upgrade completion line for "
                    f"{namespace}/{pod_name}: {dd_upgrade_completed_match.group(0)}"
                )
            else:
                print(
                    f"MySQL data dictionary version unchanged for "
                    f"{namespace}/{pod_name}; no DD migration lines logged."
                )
            print(
                f"MySQL data dictionary in-use line for "
                f"{namespace}/{pod_name}: {dd_in_use_match.group(0)}"
            )
            print(
                f"MySQL preparing-upgrade line for {namespace}/{pod_name}: "
                f"{server_upgrade_preparing_match.group(0)}"
            )
            print(
                f"MySQL upgrade start line for {namespace}/{pod_name}: "
                f"{server_upgrade_started_match.group(0)}"
            )
            print(
                f"MySQL upgrade completion line for {namespace}/{pod_name}: "
                f"{server_upgrade_completed_match.group(0)}"
            )

    def _before_operator_upgrade(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        current_release: str,
        next_release: str,
    ) -> None:
        return None

    def _after_operator_upgrade(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        operator_release: str,
    ) -> None:
        return None

    def _run_helm_legacy_switchover_rbac_upgrade_chain(self) -> None:
        cluster_names = self._build_legacy_switchover_cluster_names()
        cluster_values = self._build_legacy_switchover_cluster_values()
        current_cluster_release = self.release_chain[0]
        applied_cluster_releases = [current_cluster_release]
        server_instances = self.cluster_size
        router_instances = self.routers_count

        resident_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        chart_source_overrides = self._resolve_resident_helm_chart_source_overrides(
            resident_operator_deployment=resident_operator_deployment,
            include_deployment_topology=True,
        )
        install_options = self._build_legacy_switchover_operator_options(
            app_version=current_cluster_release,
            chart_source_overrides=chart_source_overrides,
        )
        active_operator_release_key = get_helm_release_target(install_options)

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        active_cluster_installs: list[_HelmClusterInstallTracker] = []
        cleanup_error = None
        operator_pod = None
        diagnostic_context = (
            f"installing operator {current_cluster_release} and bootstrap clusters"
        )

        try:
            self._apply_helm_operator_with_synced_default_clusterroles(
                install_options,
                install_if_missing=True,
            )
            operator_pod = self._wait_for_helm_operator_pod_rollout(
                namespace=install_options.namespace,
                deployment_name=install_options.deployment_name,
            )
            self._assert_helm_operator_image_tag(
                operator_pod,
                current_cluster_release,
            )

            for index, cluster_name in enumerate(cluster_names):
                cluster_install = self._build_helm_cluster_tracker(
                    self.ns,
                    cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                    app_version=current_cluster_release,
                    cluster_values=cluster_values,
                )
                active_cluster_installs.append(cluster_install)
                self._install_helm_cluster(
                    cluster_install,
                    create_namespace=index == 0,
                )
                if index < len(cluster_names) - 1:
                    time.sleep(CLUSTERSET_CREATION_GAP_SECONDS)

            self._wait_for_helm_clusters_ready(
                namespace=self.ns,
                cluster_names=cluster_names,
                server_instances=server_instances,
                router_instances=router_instances,
            )
            for cluster_name in cluster_names:
                self._assert_helm_cluster_runtime_image_identities(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                    server_instances=server_instances,
                    router_instances=router_instances,
                    expected_release=current_cluster_release,
                    expected_operator_release=self._get_helm_cluster_operator_release(
                        namespace=self.ns,
                        cluster_name=cluster_name,
                        fallback_release=current_cluster_release,
                    ),
                )
            self._assert_helm_clusters_cr_version(
                namespace=self.ns,
                cluster_names=cluster_names,
                expected_version=self._mysql_version_from_release(
                    current_cluster_release
                ),
            )
            active_cluster_operator_release = self._get_helm_cluster_operator_release(
                namespace=self.ns,
                cluster_name=cluster_names[0],
                fallback_release=current_cluster_release,
            )
            self._wait_for_namespace_switchover_rbac_state(
                namespace=self.ns,
                cluster_names=cluster_names,
                operator_release=active_cluster_operator_release,
                cluster_releases=applied_cluster_releases,
            )
            self._assert_namespace_switchover_rbac_state(
                namespace=self.ns,
                cluster_names=cluster_names,
                operator_release=active_cluster_operator_release,
                cluster_releases=applied_cluster_releases,
            )

            for next_release in self.release_chain[1:]:
                diagnostic_context = f"upgrading operator to {next_release}"
                self._before_operator_upgrade(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    current_release=current_cluster_release,
                    next_release=next_release,
                )
                upgrade_options = self._build_legacy_switchover_operator_options(
                    app_version=next_release,
                    chart_source_overrides=chart_source_overrides,
                )
                active_operator_release_key = get_helm_release_target(
                    upgrade_options
                )
                assert operator_pod is not None
                old_operator_pod_name = operator_pod["metadata"]["name"]
                self._apply_helm_operator_with_synced_default_clusterroles(
                    upgrade_options,
                    install_if_missing=False,
                )
                operator_pod = self._wait_for_helm_operator_pod_rollout(
                    namespace=upgrade_options.namespace,
                    deployment_name=upgrade_options.deployment_name,
                    previous_pod_name=old_operator_pod_name,
                )
                self._assert_helm_operator_image_tag(
                    operator_pod,
                    next_release,
                )
                self._wait_for_helm_clusters_ready(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    server_instances=server_instances,
                    router_instances=router_instances,
                )
                for cluster_name in cluster_names:
                    self._assert_helm_cluster_runtime_image_identities(
                        namespace=self.ns,
                        cluster_name=cluster_name,
                        server_instances=server_instances,
                        router_instances=router_instances,
                        expected_release=current_cluster_release,
                        expected_operator_release=next_release,
                    )
                self._wait_for_namespace_switchover_rbac_state(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    operator_release=next_release,
                    cluster_releases=applied_cluster_releases,
                )
                self._assert_helm_clusters_cr_version(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    expected_version=self._mysql_version_from_release(
                        current_cluster_release
                    ),
                )
                self._assert_namespace_switchover_rbac_state(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    operator_release=next_release,
                    cluster_releases=applied_cluster_releases,
                )
                self._after_operator_upgrade(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    operator_release=next_release,
                )

                new_cluster_installs = [
                    self._build_helm_cluster_tracker(
                        self.ns,
                        cluster_name,
                        server_instances=server_instances,
                        router_instances=router_instances,
                        app_version=next_release,
                        cluster_values=cluster_values,
                    )
                    for cluster_name in cluster_names
                ]
                active_cluster_installs = new_cluster_installs

                server_rollover_waiters = [
                    tutil.get_sts_rollover_update_waiter(
                        self,
                        cluster_install.cluster_name,
                        timeout=900,
                        delay=10,
                    )
                    for cluster_install in new_cluster_installs
                ]
                router_rollover_waiters = [
                    tutil.get_router_deploy_rollover_update_waiter(
                        self,
                        cluster_install.cluster_name,
                        timeout=200,
                        delay=10,
                    )
                    for cluster_install in new_cluster_installs
                ]

                diagnostic_context = (
                    f"upgrading clusters to {next_release} "
                    f"({', '.join(cluster_names)})"
                )
                self._activate_mysql_upgrade_abort_checks(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    server_instances=server_instances,
                    previous_release=current_cluster_release,
                    target_release=next_release,
                )
                try:
                    for cluster_install in new_cluster_installs:
                        upgrade_cluster_with_helm(
                            self.ns,
                            resolved_options=cluster_install.options,
                        )

                    for server_rollover_waiter in server_rollover_waiters:
                        server_rollover_waiter()
                    for router_rollover_waiter in router_rollover_waiters:
                        router_rollover_waiter()
                finally:
                    self._clear_mysql_upgrade_abort_checks()

                diagnostic_context = (
                    f"verifying mysql upgrade logs for {next_release} "
                    f"({', '.join(cluster_names)})"
                )
                current_cluster_release = next_release
                applied_cluster_releases.append(current_cluster_release)
                self._wait_for_helm_clusters_ready(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    server_instances=server_instances,
                    router_instances=router_instances,
                )
                for cluster_name in cluster_names:
                    self._assert_helm_cluster_runtime_image_identities(
                        namespace=self.ns,
                        cluster_name=cluster_name,
                        server_instances=server_instances,
                        router_instances=router_instances,
                        expected_release=current_cluster_release,
                        expected_operator_release=next_release,
                    )
                for cluster_name in cluster_names:
                    self._assert_mysql_server_upgrade_logged(
                        namespace=self.ns,
                        cluster_name=cluster_name,
                        server_instances=server_instances,
                        previous_release=applied_cluster_releases[-2],
                        target_release=current_cluster_release,
                    )
                self._wait_for_namespace_switchover_rbac_state(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    operator_release=next_release,
                    cluster_releases=applied_cluster_releases,
                )
                self._assert_helm_clusters_cr_version(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    expected_version=self._mysql_version_from_release(
                        current_cluster_release
                    ),
                )
                self._assert_namespace_switchover_rbac_state(
                    namespace=self.ns,
                    cluster_names=cluster_names,
                    operator_release=next_release,
                    cluster_releases=applied_cluster_releases,
                )
        except Exception:
            print(
                f"Failure during {diagnostic_context}. "
                f"Dumping cluster pod diagnostics before cleanup."
            )
            for cluster_name in cluster_names:
                self._print_helm_cluster_pod_diagnostics(
                    namespace=self.ns,
                    cluster_name=cluster_name,
                )
            self._print_operator_log_from_candidates(
                namespace=self.operator_ns,
                deployment_names=[self.operator_deploy_name],
            )
            raise
        finally:
            try:
                for cluster_install in reversed(active_cluster_installs):
                    try:
                        self._cleanup_helm_cluster(
                            cluster_install,
                            delete_pvcs=False,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                if active_cluster_installs and self.ns in kutil.ls_ns_ex():
                    try:
                        kutil.delete_pvc(self.ns, None)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                try:
                    uninstall_with_helm(
                        release_key=active_operator_release_key,
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None:
                raise cleanup_error


class HelmLegacySwitchoverRbacUpgradeTest(_HelmLegacySwitchoverRbacUpgradeBase):
    release_chain = [
        "9.6.0-2.2.7",
        g_ts_cfg.operator_version_tag,
    ]

    def testit(self) -> None:
        self._run_helm_legacy_switchover_rbac_upgrade_chain()


class HelmLegacySwitchoverRbacLtsBridgeUpgradeTest(_HelmLegacySwitchoverRbacUpgradeBase):
    cluster_size = 1
    expect_legacy_switchover_objects_before_current = False
    release_chain = [
        "8.4.5-2.1.7",
        "9.3.0-2.2.4",
        "9.6.0-2.2.7",
        g_ts_cfg.operator_version_tag,
    ]

    def testit(self) -> None:
        self._run_helm_legacy_switchover_rbac_upgrade_chain()


class HelmLegacySwitchoverRbacDeepHistoryUpgradeTest(_HelmLegacySwitchoverRbacUpgradeBase):
    cluster_size = 1
    expect_legacy_switchover_objects_before_current = False
    release_chain = [
        "8.0.44-2.0.20",
        "8.4.5-2.1.7",
        "9.3.0-2.2.4",
        "9.6.0-2.2.7",
        g_ts_cfg.operator_version_tag,
    ]

    def testit(self) -> None:
        self._run_helm_legacy_switchover_rbac_upgrade_chain()


class HelmLegacySwitchoverRbacDriftRepairUpgradeTest(_HelmLegacySwitchoverRbacUpgradeBase):
    release_chain = [
        "9.6.0-2.2.7",
        g_ts_cfg.operator_version_tag,
    ]

    def setUp(self):
        super().setUp()
        self._manual_current_switchover_rbac_injected = False

    def _before_operator_upgrade(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        current_release: str,
        next_release: str,
    ) -> None:
        if self._manual_current_switchover_rbac_injected:
            return
        if next_release != g_ts_cfg.operator_version_tag:
            return

        for index, cluster_name in enumerate(cluster_names, start=1):
            self._apply_cluster_current_switchover_rbac_manifests(
                namespace=namespace,
                service_account_manifest=(
                    self._build_drifted_cluster_current_switchover_service_account_manifest(
                        namespace=namespace,
                        cluster_name=cluster_name,
                        image_pull_secret_names=[f"manual-drift-pullsecret-{index}"],
                        owner_tag=f"{cluster_name}-upgrade-sa",
                    )
                ),
                role_binding_manifest=(
                    self._build_drifted_cluster_current_switchover_role_binding_manifest(
                        namespace=namespace,
                        cluster_name=cluster_name,
                        role_name=f"manual-drift-role-{index}",
                        subject_name=f"manual-drift-sa-{index}",
                        owner_tag=f"{cluster_name}-upgrade-rb",
                        annotations={
                            "rbac.oracle.com/manual-drift": "true",
                        },
                    )
                ),
            )

        self._manual_current_switchover_rbac_injected = True

    def _after_operator_upgrade(
        self,
        *,
        namespace: str,
        cluster_names: list[str],
        operator_release: str,
    ) -> None:
        if not self._manual_current_switchover_rbac_injected:
            return
        if operator_release != g_ts_cfg.operator_version_tag:
            return

        for cluster_name in cluster_names:
            repaired_state = self._wait_for_cluster_current_switchover_rbac_repaired(
                namespace=namespace,
                cluster_name=cluster_name,
                expected_image_pull_secret_names=[],
            )
            current_rb = repaired_state["rb"]
            assert current_rb is not None
            self.assertFalse(
                current_rb.get("metadata", {}).get("annotations", {}).get(
                    "rbac.oracle.com/manual-drift"
                ),
                msg=(
                    f"Expected repaired switchover RoleBinding {namespace}/"
                    f"{get_cluster_switchover_role_binding_name(cluster_name)} "
                    f"to drop drift-only annotations after recreate"
                ),
            )

    def testit(self) -> None:
        self._run_helm_legacy_switchover_rbac_upgrade_chain()


class SwitchoverRbacDriftRepairOnRestartTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _restart_operator_and_wait_for_rollout(
        self,
        *,
        operator_ns: str,
        operator_name: str,
    ) -> dict:
        previous_operator_pod = self._get_single_operator_pod(
            namespace=operator_ns,
            deployment_name=operator_name,
        )
        patch_payload = {
            "spec": {
                "template": {
                    "metadata": {
                        "annotations": {
                            "kubectl.kubernetes.io/restartedAt": (
                                datetime.now(timezone.utc)
                                .replace(microsecond=0)
                                .isoformat()
                                .replace("+00:00", "Z")
                            )
                        }
                    }
                }
            }
        }
        kutil.patch_dp(operator_ns, operator_name, patch_payload)
        return self._wait_for_operator_pod_rollout(
            namespace=operator_ns,
            deployment_name=operator_name,
            previous_pod_name=previous_operator_pod["metadata"]["name"],
        )

    def test_restart_repairs_current_switchover_rbac_drift(self) -> None:
        operator_ns = f"op-swrbac-restart-{self.random_suffix}"
        cluster_ns = f"db-swrbac-restart-{self.random_suffix}"
        operator_name = f"myop-swrbac-restart-{self.random_suffix}"
        release_name = f"myoper-swrbac-restart-{self.random_suffix}"

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        patched = get_patched_artifacts(
            original_artifacts,
            release_name,
            operator_ns,
            operator_name,
        )

        test_error = None
        cleanup_error = None

        try:
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            kutil.create_ns(cluster_ns, labels={})
            kutil.create_user_secrets(
                cluster_ns,
                self.cluster_secret_name,
                "root",
                "%",
                "sakila",
            )

            yaml_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
  namespace: {cluster_ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
            kutil.apply(cluster_ns, yaml_manifest)

            self.wait_ic(
                self.cluster_name,
                ["PENDING", "INITIALIZING", "ONLINE"],
                ns=cluster_ns,
            )
            for instance in range(0, self.cluster_size):
                self.wait_pod(f"{self.cluster_name}-{instance}", "Running", ns=cluster_ns)
            if self.routers_count:
                self.wait_routers(
                    f"{self.cluster_name}-router.*",
                    self.routers_count,
                    timeout=self.cluster_size * 120,
                    wait=10,
                    ns=cluster_ns,
                )
            self.wait_ic(
                self.cluster_name,
                "ONLINE",
                num_online=self.cluster_size,
                ns=cluster_ns,
            )

            baseline_state = self._wait_for_cluster_current_switchover_rbac_repaired(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                expected_image_pull_secret_names=[],
                operator_ns=operator_ns,
                operator_name=operator_name,
            )
            baseline_sa = baseline_state["sa"]
            baseline_rb = baseline_state["rb"]
            assert baseline_sa is not None
            assert baseline_rb is not None

            def timeout_diagnostics() -> None:
                kutil.store_ns_diagnostics(cluster_ns)
                kutil.store_deploy_diagnostics(operator_ns, operator_name)

            sa_name = get_cluster_switchover_service_account_name(self.cluster_name)
            rb_name = get_cluster_switchover_role_binding_name(self.cluster_name)

            kutil.patch(
                cluster_ns,
                "sa",
                sa_name,
                {
                    "metadata": {
                        "ownerReferences": self._build_foreign_owner_references(
                            f"{self.cluster_name}-restart-sa"
                        )
                    },
                    "imagePullSecrets": [
                        {"name": "manual-drift-restart-pullsecret"}
                    ],
                },
                type="merge",
                data_as_type="json",
            )
            kutil.patch(
                cluster_ns,
                "rolebinding",
                rb_name,
                {
                    "metadata": {
                        "ownerReferences": self._build_foreign_owner_references(
                            f"{self.cluster_name}-restart-rb"
                        )
                    },
                    "subjects": [
                        {
                            "kind": "ServiceAccount",
                            "name": "manual-drift-restart-sa",
                        }
                    ],
                },
                type="merge",
                data_as_type="json",
            )

            self._restart_operator_and_wait_for_rollout(
                operator_ns=operator_ns,
                operator_name=operator_name,
            )

            self._wait_for_cluster_current_switchover_rbac_repaired(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                expected_image_pull_secret_names=[],
                operator_ns=operator_ns,
                operator_name=operator_name,
                timeout_diagnostics=timeout_diagnostics,
            )

            kutil.delete_rolebinding(cluster_ns, rb_name)
            drifted_role_binding_manifest = (
                self._build_drifted_cluster_current_switchover_role_binding_manifest(
                    namespace=cluster_ns,
                    cluster_name=self.cluster_name,
                    role_name="manual-drift-immutable-role",
                    subject_name="manual-drift-recreated-sa",
                    owner_tag=f"{self.cluster_name}-restart-rb-recreate",
                    annotations={
                        "rbac.oracle.com/manual-drift": "immutable-role-ref",
                    },
                )
            )
            # Keep the recreated RoleBinding owned by the real cluster so it
            # persists while the operator is intentionally scaled to zero.
            drifted_role_binding_manifest["metadata"]["ownerReferences"] = (
                copy.deepcopy(
                    baseline_rb.get("metadata", {}).get("ownerReferences", [])
                )
            )
            kutil.patch_dp(
                operator_ns,
                operator_name,
                {"spec": {"replicas": 0}},
                type="merge",
                data_as_type="json",
            )
            self.wait_pods_gone(f"{operator_name}.*", ns=operator_ns)
            kutil.apply(cluster_ns, yaml.safe_dump(drifted_role_binding_manifest))

            def _get_drifted_role_binding() -> Optional[dict]:
                role_binding = kutil.get(
                    cluster_ns,
                    "rolebinding",
                    rb_name,
                    check=False,
                    cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
                )
                if role_binding is None:
                    return None
                if (
                    role_binding.get("metadata", {})
                    .get("annotations", {})
                    .get("rbac.oracle.com/manual-drift")
                    != "immutable-role-ref"
                ):
                    return None
                if (
                    role_binding.get("roleRef", {}).get("name")
                    != "manual-drift-immutable-role"
                ):
                    return None
                return role_binding

            drifted_role_binding = self.wait(
                _get_drifted_role_binding,
                timeout=60,
                delay=1,
                timeout_diagnostics=timeout_diagnostics,
            )
            self.assertIsNotNone(
                drifted_role_binding,
                msg=f"Expected drifted switchover RoleBinding {cluster_ns}/{rb_name}",
            )
            drifted_role_binding_uid = drifted_role_binding["metadata"]["uid"]

            kutil.patch_dp(
                operator_ns,
                operator_name,
                {"spec": {"replicas": 1}},
                type="merge",
                data_as_type="json",
            )
            self._wait_for_operator_pod_rollout(
                namespace=operator_ns,
                deployment_name=operator_name,
            )

            repaired_state = self._wait_for_cluster_current_switchover_rbac_repaired(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                expected_image_pull_secret_names=[],
                replaced_role_binding_uid=drifted_role_binding_uid,
                operator_ns=operator_ns,
                operator_name=operator_name,
                timeout_diagnostics=timeout_diagnostics,
            )
            repaired_role_binding = repaired_state["rb"]
            assert repaired_role_binding is not None
            self.assertFalse(
                repaired_role_binding.get("metadata", {}).get("annotations", {}).get(
                    "rbac.oracle.com/manual-drift"
                ),
                msg=(
                    f"Expected repaired switchover RoleBinding {cluster_ns}/{rb_name} "
                    f"to drop drift-only annotations after recreate"
                ),
            )
        except Exception as exc:
            test_error = exc
            print_operator_log(operator_ns, operator_name)
            raise
        finally:
            try:
                try:
                    kutil.delete_ic(cluster_ns, self.cluster_name)
                    self.wait_pods_gone(f"{self.cluster_name}.*", ns=cluster_ns)
                    self.wait_routers_gone(
                        f"{self.cluster_name}-router.*",
                        ns=cluster_ns,
                    )
                    self.wait_ic_gone(self.cluster_name, ns=cluster_ns)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                for cleanup_fn, args in [
                    (kutil.delete_pvc, (cluster_ns, None)),
                    (kutil.delete_secret, (cluster_ns, self.cluster_secret_name)),
                    (kutil.delete_ns, (cluster_ns,)),
                ]:
                    try:
                        cleanup_fn(*args)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                try:
                    remove_operator(
                        operator_ns=operator_ns,
                        operator_deploy_name=operator_name,
                        crole_names_pattern=get_crole_names_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        crole_binding_pattern=get_crole_binding_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        ckopf_peerings_pattern=get_ckopf_peerings_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        sa_names_pattern=get_sa_names_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        role_names_pattern=get_role_names_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        role_binding_pattern=get_role_binding_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        kopf_peerings_pattern="",
                        return_manifests=False,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class SwitchoverRbacStartupRetryOnRestartTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _restart_operator_and_wait_for_replacement_pod(
        self,
        *,
        operator_ns: str,
        operator_name: str,
    ) -> dict:
        previous_operator_pod = self._get_single_operator_pod(
            namespace=operator_ns,
            deployment_name=operator_name,
        )
        # Force a strict single-pod restart for this scenario so the broken
        # replacement cannot overlap with the previous ready operator pod.
        kutil.patch_dp(
            operator_ns,
            operator_name,
            {"spec": {"replicas": 0}},
            type="merge",
            data_as_type="json",
        )
        kutil.wait_pod_gone(
            operator_ns,
            previous_operator_pod["metadata"]["name"],
            timeout=300,
        )
        kutil.patch_dp(
            operator_ns,
            operator_name,
            {"spec": {"replicas": 1}},
            type="merge",
            data_as_type="json",
        )
        return self._wait_for_replacement_operator_pod(
            namespace=operator_ns,
            deployment_name=operator_name,
            previous_pod_name=previous_operator_pod["metadata"]["name"],
        )

    def _wait_for_operator_deployment_unready(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        timeout: int = 120,
    ) -> dict:
        last_status = None
        for _ in range(timeout):
            deployment = kutil.get_deploy(operator_ns, operator_name)
            last_status = deployment.get("status", {})
            ready_replicas = last_status.get("readyReplicas") or 0
            available_replicas = last_status.get("availableReplicas") or 0
            if ready_replicas == 0 and available_replicas == 0:
                return deployment
            time.sleep(1)

        raise AssertionError(
            f"Expected Deployment {operator_ns}/{operator_name} to stay unready, "
            f"last status was {last_status!r}"
        )

    def _wait_for_cluster_event(
        self,
        *,
        namespace: str,
        cluster_name: str,
        after: datetime,
        action: str,
        reason: str,
        message_fragment: str,
        timeout: int = 120,
    ) -> list[dict]:
        last_events = []
        for _ in range(timeout):
            events = kutil.get_ic_ev(
                namespace,
                cluster_name,
                after=after,
                fields=["message", "reason", "type", "action"],
            ) or []
            matching_events = [
                event
                for event in events
                if event.get("reason") == reason
                and event.get("action") == action
                and message_fragment in event.get("message", "")
            ]
            if matching_events:
                return matching_events
            last_events = events
            time.sleep(1)

        raise AssertionError(
            f"Expected event {action}/{reason} for {namespace}/{cluster_name}, "
            f"got {last_events!r}"
        )

    @staticmethod
    def _build_service_account_write_denied_rules(
        cluster_role_manifest: dict,
    ) -> list[dict]:
        rules = copy.deepcopy(cluster_role_manifest.get("rules", []))
        changed = False

        for rule in rules:
            if "serviceaccounts" not in (rule.get("resources") or []):
                continue

            verbs = rule.get("verbs") or []
            filtered_verbs = [
                verb for verb in verbs if verb not in {"create", "patch"}
            ]
            if filtered_verbs != verbs:
                rule["verbs"] = filtered_verbs
                changed = True

        if not changed:
            raise AssertionError(
                "Expected operator ClusterRole to grant serviceaccounts create/patch "
                "so the restart-retry test can remove them temporarily."
            )

        return rules

    def test_restart_blocks_until_switchover_rbac_startup_repair_succeeds(self) -> None:
        operator_ns = f"op-swrbac-retry-{self.random_suffix}"
        cluster_ns = f"db-swrbac-retry-{self.random_suffix}"
        operator_name = f"myop-swrbac-retry-{self.random_suffix}"
        release_name = f"myoper-swrbac-retry-{self.random_suffix}"
        operator_role_name = get_operator_role_name(operator_ns, operator_name)

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        patched = get_patched_artifacts(
            original_artifacts,
            release_name,
            operator_ns,
            operator_name,
            custom_spec={
                "strategy": {
                    "type": "RollingUpdate",
                    "rollingUpdate": {
                        "maxSurge": 0,
                        "maxUnavailable": 1,
                    },
                },
            },
        )

        original_operator_cluster_role = None
        operator_cluster_role_restored = False
        test_error = None
        cleanup_error = None

        try:
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            kutil.create_ns(cluster_ns, labels={})
            kutil.create_user_secrets(
                cluster_ns,
                self.cluster_secret_name,
                "root",
                "%",
                "sakila",
            )

            yaml_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
  namespace: {cluster_ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
            kutil.apply(cluster_ns, yaml_manifest)

            self.wait_ic(
                self.cluster_name,
                ["PENDING", "INITIALIZING", "ONLINE"],
                ns=cluster_ns,
            )
            for instance in range(0, self.cluster_size):
                self.wait_pod(f"{self.cluster_name}-{instance}", "Running", ns=cluster_ns)
            if self.routers_count:
                self.wait_routers(
                    f"{self.cluster_name}-router.*",
                    self.routers_count,
                    timeout=self.cluster_size * 120,
                    wait=10,
                    ns=cluster_ns,
                )
            self.wait_ic(
                self.cluster_name,
                "ONLINE",
                num_online=self.cluster_size,
                ns=cluster_ns,
            )

            self._wait_for_cluster_current_switchover_rbac_repaired(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                expected_image_pull_secret_names=[],
                operator_ns=operator_ns,
                operator_name=operator_name,
            )

            service_account_name = get_cluster_switchover_service_account_name(
                self.cluster_name
            )
            kutil.patch(
                cluster_ns,
                "sa",
                service_account_name,
                {
                    "metadata": {
                        "ownerReferences": self._build_foreign_owner_references(
                            f"{self.cluster_name}-startup-retry-sa"
                        )
                    },
                    "imagePullSecrets": [
                        {"name": "manual-drift-startup-retry-pullsecret"}
                    ],
                },
                type="merge",
                data_as_type="json",
            )

            original_operator_cluster_role = copy.deepcopy(
                kutil.get_clusterrole(operator_role_name)
            )
            broken_rules = self._build_service_account_write_denied_rules(
                original_operator_cluster_role
            )
            kutil.patch(
                None,
                "clusterrole",
                operator_role_name,
                {"rules": broken_rules},
                type="merge",
                data_as_type="json",
                w_ns=False,
            )

            restart_after = datetime.now(timezone.utc).replace(microsecond=0)
            self._restart_operator_and_wait_for_replacement_pod(
                operator_ns=operator_ns,
                operator_name=operator_name,
            )
            self._wait_for_operator_deployment_unready(
                operator_ns=operator_ns,
                operator_name=operator_name,
            )

            repair_failed_events = self._wait_for_cluster_event(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                after=restart_after,
                action="ReconcileSwitchoverRbac",
                reason="RepairFailed",
                message_fragment=(
                    f"Failed to repair switchover RBAC for cluster "
                    f"{cluster_ns}/{self.cluster_name}"
                ),
            )
            self.assertTrue(
                repair_failed_events,
                msg=(
                    f"Expected RepairFailed event for {cluster_ns}/{self.cluster_name}"
                ),
            )

            restart_events_while_broken = [
                event
                for event in (
                    kutil.get_ic_ev(
                        cluster_ns,
                        self.cluster_name,
                        after=restart_after,
                        fields=["message", "reason", "type", "action"],
                    )
                    or []
                )
                if event.get("reason") == "OperatorRestarted"
                and event.get("action") == "HandlingOperatorRestart"
            ]
            self.assertEqual(
                restart_events_while_broken,
                [],
                msg=(
                    f"Unexpected OperatorRestarted event while startup repair was "
                    f"still failing: {restart_events_while_broken}"
                ),
            )

            restore_after = datetime.now(timezone.utc).replace(microsecond=0)
            kutil.patch(
                None,
                "clusterrole",
                operator_role_name,
                {"rules": original_operator_cluster_role["rules"]},
                type="merge",
                data_as_type="json",
                w_ns=False,
            )
            operator_cluster_role_restored = True
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            # Kopf logs the failed startup attempts as errors with tracebacks.
            # Those are expected before the RBAC restore, so clear them before
            # reusing wait helpers that treat operator exceptions as fatal.
            self.op_fatal_errors = []
            self.op_logged_errors = []
            self.op_exception = []

            self._wait_for_cluster_current_switchover_rbac_repaired(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                expected_image_pull_secret_names=[],
                operator_ns=operator_ns,
                operator_name=operator_name,
            )

            restarted_events_after_restore = self._wait_for_cluster_event(
                namespace=cluster_ns,
                cluster_name=self.cluster_name,
                after=restore_after,
                action="HandlingOperatorRestart",
                reason="OperatorRestarted",
                message_fragment=(
                    f"Handling operator restarted for cluster "
                    f"{cluster_ns}/{self.cluster_name}"
                ),
            )
            self.assertTrue(
                restarted_events_after_restore,
                msg=(
                    f"Expected OperatorRestarted event after restoring RBAC for "
                    f"{cluster_ns}/{self.cluster_name}"
                ),
            )
        except Exception as exc:
            test_error = exc
            print_operator_log(operator_ns, operator_name)
            raise
        finally:
            try:
                if (
                    original_operator_cluster_role is not None
                    and not operator_cluster_role_restored
                    and kutil.get_deploy(operator_ns, operator_name, check=False)
                ):
                    try:
                        kutil.patch(
                            None,
                            "clusterrole",
                            operator_role_name,
                            {"rules": original_operator_cluster_role["rules"]},
                            type="merge",
                            data_as_type="json",
                            w_ns=False,
                        )
                        kutil.wait_deploy(operator_ns, operator_name, timeout=300)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                self.op_fatal_errors = []
                self.op_logged_errors = []
                self.op_exception = []

                try:
                    kutil.delete_ic(cluster_ns, self.cluster_name)
                    self.wait_pods_gone(f"{self.cluster_name}.*", ns=cluster_ns)
                    self.wait_routers_gone(
                        f"{self.cluster_name}-router.*",
                        ns=cluster_ns,
                    )
                    self.wait_ic_gone(self.cluster_name, ns=cluster_ns)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                for cleanup_fn, args in [
                    (kutil.delete_pvc, (cluster_ns, None)),
                    (kutil.delete_secret, (cluster_ns, self.cluster_secret_name)),
                    (kutil.delete_ns, (cluster_ns,)),
                ]:
                    try:
                        cleanup_fn(*args)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                try:
                    remove_operator(
                        operator_ns=operator_ns,
                        operator_deploy_name=operator_name,
                        crole_names_pattern=get_crole_names_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        crole_binding_pattern=get_crole_binding_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        ckopf_peerings_pattern=get_ckopf_peerings_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        sa_names_pattern=get_sa_names_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        role_names_pattern=get_role_names_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        role_binding_pattern=get_role_binding_pattern(
                            operator_ns,
                            operator_name,
                        ),
                        kopf_peerings_pattern="",
                        return_manifests=False,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class HelmOperatorDeploymentNameUpgradeTest(OperatorSingleAndMultipleBaseTest):
    upgraded_deployment_name = "my-upgraded-operator"
    upgrade_error_fragment = (
        "Invalid values: deployment.name is immutable after install "
        "because it determines persistent RBAC and peering identity."
    )

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_install_previous_release_then_upgrade_with_custom_deployment_name_single_server_single_router(
        self,
    ) -> None:
        self._run_helm_operator_upgrade_cycle(
            server_instances=1,
            router_instances=1,
            upgraded_operator_overrides={
                "deployment": {
                    "name": self.upgraded_deployment_name,
                },
            },
            upgraded_deployment_name=self.upgraded_deployment_name,
            expect_operator_upgrade_failure=True,
            expected_operator_upgrade_error_fragment=self.upgrade_error_fragment,
        )

    def test_install_previous_release_then_upgrade_with_custom_deployment_name_three_servers_single_router(
        self,
    ) -> None:
        self._run_helm_operator_upgrade_cycle(
            server_instances=3,
            router_instances=1,
            upgraded_operator_overrides={
                "deployment": {
                    "name": self.upgraded_deployment_name,
                },
            },
            upgraded_deployment_name=self.upgraded_deployment_name,
            expect_operator_upgrade_failure=True,
            expected_operator_upgrade_error_fragment=self.upgrade_error_fragment,
        )


class HelmOperatorSelectorUpgradeCompatibilityTest(
    OperatorSingleAndMultipleBaseTest
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_upgrade_preserves_legacy_selector_and_services_stay_isolated(
        self,
    ) -> None:
        operator_ns = self.operator_ns
        requested_legacy_operator_name = f"sel-old-{self.random_suffix}"
        legacy_release_name = self.operator_deploy_name
        legacy_deployment_name = "mysql-operator"
        previous_release = get_previous_operator_chart_release(
            current_app_version=g_ts_cfg.operator_version_tag,
        )

        resident_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        chart_source_overrides = self._resolve_resident_helm_chart_source_overrides(
            resident_operator_deployment=resident_operator_deployment,
        )
        previous_install_values = self._merge_helm_values(
            chart_source_overrides,
            {
                "deployment": {
                    "name": requested_legacy_operator_name,
                },
            },
        )

        previous_install_options = HelmOperatorInstallOptions(
            namespace=operator_ns,
            app_version=previous_release,
            release_name=legacy_release_name,
            kube_context=g_ts_cfg.k8s_context,
            helm_package="mysql-operator",
            operator_values=previous_install_values,
            use_chart_defaults=True,
        )
        legacy_upgrade_options = HelmOperatorInstallOptions(
            namespace=operator_ns,
            app_version=g_ts_cfg.operator_version_tag,
            release_name=legacy_release_name,
            kube_context=g_ts_cfg.k8s_context,
            helm_package="mysql-operator",
            operator_values=chart_source_overrides,
            use_chart_defaults=True,
        )

        original_artifacts = self._remove_default_operator_or_fail()

        legacy_install_result = None
        legacy_upgrade_result = None
        test_error = None

        try:
            legacy_install_result = self._apply_helm_operator_with_synced_default_clusterroles(
                previous_install_options,
                install_if_missing=True,
                wait_for_deployment_name=legacy_deployment_name,
            )
            self.assertIsNone(
                kutil.get_deploy(
                    operator_ns,
                    requested_legacy_operator_name,
                    check=False,
                ),
                msg=(
                    "Legacy chart unexpectedly honored deployment.name; the "
                    "compatibility path should still create mysql-operator"
                ),
            )
            self._assert_helm_operator_selector_isolation(
                namespace=operator_ns,
                deployment_name=legacy_deployment_name,
                expected_deployment_selector_labels=(
                    get_legacy_deployment_selector_labels()
                ),
                expect_service_selector_labels=False,
                check_service_isolation=False,
            )
            self.assertIsNotNone(
                kutil.get(
                    None,
                    "clusterkopfpeering",
                    legacy_deployment_name,
                    check=False,
                ),
            )

            legacy_upgrade_result = self._apply_helm_operator_with_synced_default_clusterroles(
                legacy_upgrade_options,
                install_if_missing=False,
            )
            self._assert_helm_operator_selector_isolation(
                namespace=operator_ns,
                deployment_name=legacy_deployment_name,
                expected_deployment_selector_labels=(
                    get_legacy_deployment_selector_labels()
                ),
            )
            self.assertIsNotNone(
                kutil.get(
                    None,
                    "clusterkopfpeering",
                    legacy_deployment_name,
                    check=False,
                ),
            )
        except Exception as exc:
            test_error = exc
            self._print_operator_log_from_candidates(
                namespace=operator_ns,
                deployment_names=[
                    requested_legacy_operator_name,
                    legacy_deployment_name,
                ],
            )
            raise
        finally:
            cleanup_error = None
            try:
                try:
                    uninstall_with_helm(
                        release_key=(
                            legacy_upgrade_result.release_key
                            if legacy_upgrade_result is not None
                            else (
                                legacy_install_result.release_key
                                if legacy_install_result is not None
                                else get_helm_release_target(
                                    previous_install_options,
                                )
                            )
                        ),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class HelmStandaloneOperatorsTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_operators(self) -> None:
        namespaces_to_clean = []
        watched_ics = []
        operators_to_clean = []
        cluster_helm_installs = []
        operator_count = 1
        op_watched_ns = []
        test_error = None

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        operator_ns = f"op-stand-{self.random_suffix}"
        db_ns = f"db-stand-op-{self.random_suffix}"
        op_watched_ns.append(db_ns)
        watched_ics.append((db_ns, "ic-standalone-op"))

        try:
            for db_ns in op_watched_ns:
                kutil.create_ns(db_ns, labels={})
                namespaces_to_clean.append(db_ns)

            for operator_nr in range(1, operator_count + 1):
                operator_name = f"myop-stand-op{operator_nr}-{self.random_suffix}"
                release_name = (
                    f"myoper-helm-standalone-op{operator_nr}-{self.random_suffix}"
                )
                watched_ns_str = ",".join(op_watched_ns)

                helm_options = resolve_install_with_helm_options(
                    namespace=operator_ns,
                    source_operator_namespace=self.operator_ns,
                    source_operator_deployment_name=self.operator_deploy_name,
                    source_operator_deployment=source_operator_deployment,
                )
                helm_options.release_name = release_name
                # See helm/mysql-operator/values.yaml for the deployment values layout.
                # Keep these keys in sync if the chart changes:
                # deployment.name, deployment.namespaces, deployment.standalone
                helm_options.operator_values["deployment"]["name"] = operator_name
                helm_options.operator_values["deployment"]["namespaces"] = op_watched_ns
                helm_options.operator_values["deployment"]["standalone"] = True
                operators_to_clean.append(helm_options)

                install_result = install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )

                kutil.wait_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                    timeout=300,
                )

                operator_deployment = kutil.get_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                envs = get_operator_envs_from_deployment(operator_deployment)

                self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
                self.assertEqual(envs["OPERATOR_NAMESPACES"], watched_ns_str)
                assert_no_peering(
                    self,
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )

            for ns, cluster_name in watched_ics:
                cluster_install = self._build_helm_cluster_tracker(
                    ns,
                    cluster_name,
                )
                cluster_helm_installs.append(cluster_install)
                self._install_helm_cluster(cluster_install)

            for ns, cluster_name in watched_ics:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)

                if self.routers_count:
                    self.wait_routers(
                        f"{cluster_name}-router.*",
                        self.routers_count,
                        timeout=self.cluster_size * 120,
                        wait=10,
                        ns=ns,
                    )
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)
        except Exception as exc:
            test_error = exc
            for options in operators_to_clean:
                try:
                    print_operator_log(
                        options.namespace,
                        options.deployment_name,
                    )
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            for cluster_install in reversed(cluster_helm_installs):
                try:
                    self._cleanup_helm_cluster(cluster_install)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            for options in reversed(operators_to_clean):
                try:
                    uninstall_with_helm(
                        release_key=get_helm_release_target(options),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            for ns in reversed(namespaces_to_clean):
                try:
                    if ns in kutil.ls_ns_ex():
                        kutil.delete_ns(ns)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            restore_operator(original_artifacts, self.operator_ns)
            kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class HelmInstallWithDefaultsOperatorTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 1
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_install_with_helm_defaults_and_run_cluster(self) -> None:
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        source_operator_envs = get_operator_envs_from_deployment(source_operator_deployment)
        source_operator_container = source_operator_deployment["spec"]["template"]["spec"]["containers"][0]

        helm_options = resolve_install_with_helm_options(
            namespace=self.operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = (
            f"myoper-helm-install-defaults-{self.random_suffix}"
        )

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        install_result = None
        cluster_helm_installs = []
        test_error = None

        try:
            install_result = install_with_helm(
                self.operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )
            installed_operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            installed_operator_envs = get_operator_envs_from_deployment(installed_operator_deployment)
            installed_operator_container = installed_operator_deployment["spec"]["template"]["spec"]["containers"][0]

            self.assertEqual(installed_operator_container["image"], source_operator_container["image"])
            self.assertEqual(
                installed_operator_container["imagePullPolicy"],
                source_operator_container["imagePullPolicy"],
            )
            self.assertEqual(
                installed_operator_envs["MYSQL_OPERATOR_IMAGE_PULL_POLICY"],
                source_operator_envs["MYSQL_OPERATOR_IMAGE_PULL_POLICY"],
            )
            self.assertEqual(
                installed_operator_envs.get("MYSQL_OPERATOR_DEFAULT_REPOSITORY"),
                source_operator_envs.get("MYSQL_OPERATOR_DEFAULT_REPOSITORY"),
            )
            self.assertEqual(
                installed_operator_deployment["spec"]["template"]["spec"].get("imagePullSecrets", []),
                [],
            )

            cluster_install = self._build_helm_cluster_tracker(
                self.ns,
                self.cluster_name,
            )
            cluster_helm_installs.append(cluster_install)
            self._install_helm_cluster(cluster_install)

            try:
                self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=self.ns)

                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{self.cluster_name}-{instance}", "Running", ns=self.ns)

                if self.routers_count:
                    self.wait_routers(
                        f"{self.cluster_name}-router.*",
                        self.routers_count,
                        timeout=self.cluster_size * 120,
                        wait=10,
                        ns=self.ns,
                    )

                self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, ns=self.ns)
            except Exception:
                print_operator_log(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                raise
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None

            try:
                for cluster_install in reversed(cluster_helm_installs):
                    try:
                        self._cleanup_helm_cluster(cluster_install)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                uninstall_with_helm(
                    release_key=(
                        install_result.release_key
                        if install_result is not None
                        else get_helm_release_target(helm_options)
                    ),
                    delete_namespace=True,
                )
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class InvalidNamespaceValuesTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_invalid_pattern_entries_fail_fast_in_standalone_mode(self) -> None:
        operator_ns = f"op-invalid-ns-{self.random_suffix}"
        operator_name = f"myop-invalid-ns-{self.random_suffix}"
        release_name = f"myoper-invalid-ns-{self.random_suffix}"
        watch_namespaces = (
            f"team-*{self.random_suffix},"
            f"!kube-*{self.random_suffix},"
            f"qa-?{self.random_suffix}"
        )

        artifacts = copy.deepcopy(self.artifacts)

        patched = get_patched_artifacts(
            artifacts,
            release_name,
            operator_ns,
            operator_name,
            watch_namespaces=watch_namespaces,
            standalone=True,
        )
        self.assertEqual(patched["clusterkopfpeerings"], {})
        self.assertEqual(patched["kopfpeerings"], {})

        install_operator(patched, operator_ns)
        self.add_operator_cleanup(operator_ns, operator_name)
        error_fragment = (
            "Invalid values: deployment.namespaces / OPERATOR_NAMESPACES supports "
            "explicit namespace names only."
        )
        wait_for_operator_failure_log_fragment(
            operator_ns,
            operator_name,
            error_fragment,
        )


class HelmInvalidNamespaceValuesTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_invalid_pattern_entries_fail_fast_in_standalone_mode(self) -> None:
        operator_ns = f"op-invalid-ns-helm-{self.random_suffix}"
        operator_name = f"myop-invalid-ns-helm-{self.random_suffix}"
        release_name = f"myoper-helm-invalid-ns-{self.random_suffix}"
        watch_namespaces = [
            f"team-*{self.random_suffix}",
            f"!kube-*{self.random_suffix}",
            f"qa-?{self.random_suffix}",
        ]
        error_fragment = (
            "Invalid values: deployment.namespaces / OPERATOR_NAMESPACES supports "
            "explicit namespace names only."
        )

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.namespaces, deployment.standalone
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = watch_namespaces
        helm_options.operator_values["deployment"]["standalone"] = True

        install_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    "Helm install with invalid deployment.namespaces unexpectedly "
                    "succeeded"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, operator_name, check=False),
                msg=(
                    f"Helm chart should reject invalid deployment.namespaces before "
                    f"creating deployment {operator_ns}/{operator_name}"
                ),
            )

            install_error_output = (
                getattr(install_error, "stdout", None)
                or getattr(install_error, "output", None)
                or str(install_error)
            )
            self.assertIn(error_fragment, install_error_output)
        finally:
            uninstall_with_helm(
                release_key=get_helm_release_target(helm_options),
                delete_namespace=True,
            )


class HelmDeploymentNameLengthValidationTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _build_helm_options(
        self,
        *,
        namespace: str,
        release_name: str,
        deployment_name: str,
        source_operator_deployment: Optional[dict] = None,
    ):
        if source_operator_deployment is None:
            source_operator_deployment = kutil.get_deploy(
                self.operator_ns,
                self.operator_deploy_name,
            )
        helm_options = resolve_install_with_helm_options(
            namespace=namespace,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep this key in sync if the chart changes:
        # deployment.name
        helm_options.operator_values["deployment"]["name"] = deployment_name
        # This suite validates name budgeting only, so scope the operator and
        # avoid colliding with the resident global operator in the test harness.
        helm_options.operator_values["deployment"]["namespaces"] = [namespace]
        return helm_options

    def test_custom_deployment_name_at_maximum_length_succeeds(self) -> None:
        operator_ns = f"op-name-len-helm-{self.random_suffix}"
        max_len = get_max_custom_deployment_name_length(operator_ns)
        self.assertGreater(max_len, 0)

        operator_name = "a" * max_len
        release_name = f"myoper-helm-name-len-max-{self.random_suffix}"
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self._remove_default_operator_or_fail()
        helm_options = self._build_helm_options(
            namespace=operator_ns,
            release_name=release_name,
            deployment_name=operator_name,
            source_operator_deployment=source_operator_deployment,
        )

        install_result = None
        test_error = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            self.assertEqual(len(operator_name), max_len)
            self.assertLessEqual(
                len(get_namespaced_peering_name(operator_name)),
                K8S_NAME_LABEL_LIMIT,
            )
            self.assertEqual(
                len(get_cluster_peering_name(operator_ns, operator_name)),
                K8S_NAME_LABEL_LIMIT,
            )
            self.assertEqual(
                len(get_operator_role_name(operator_ns, operator_name)),
                K8S_NAME_LABEL_LIMIT,
            )
            self.assertEqual(
                len(get_global_instance_name(operator_ns, operator_name)),
                K8S_NAME_LABEL_LIMIT,
            )

            operator_pods = kutil.ls_po(
                operator_ns,
                pattern=f"{operator_name}.*",
            )
            self.assertGreaterEqual(len(operator_pods), 1)
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            try:
                uninstall_with_helm(
                    release_key=(
                        install_result.release_key
                        if install_result is not None
                        else get_helm_release_target(helm_options)
                    ),
                    delete_namespace=True,
                )
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    self._restore_default_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_custom_deployment_name_above_maximum_length_fails_fast(self) -> None:
        operator_ns = f"op-name-len-helm-{self.random_suffix}"
        max_len = get_max_custom_deployment_name_length(operator_ns)
        self.assertGreater(max_len, 0)

        operator_name = "a" * (max_len + 1)
        release_name = f"myoper-helm-name-len-over-{self.random_suffix}"
        error_fragment = (
            "Invalid values: deployment.name "
            f"\"{operator_name}\" is {max_len + 1} characters long, but at most "
            f"{max_len} are allowed in namespace \"{operator_ns}\""
        )
        helm_options = self._build_helm_options(
            namespace=operator_ns,
            release_name=release_name,
            deployment_name=operator_name,
        )

        install_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    "Helm install with deployment.name longer than the derived "
                    "name budget unexpectedly succeeded"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, operator_name, check=False),
                msg=(
                    f"Helm chart should reject deployment.name before creating "
                    f"deployment {operator_ns}/{operator_name}"
                ),
            )

            install_error_output = (
                getattr(install_error, "stdout", None)
                or getattr(install_error, "output", None)
                or str(install_error)
            )
            self.assertIn(error_fragment, install_error_output)
        finally:
            uninstall_with_helm(
                release_key=get_helm_release_target(helm_options),
                delete_namespace=True,
            )


class HelmClusterScopedIdentityConflictValidationTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_default_identity_conflict_fails_fast_against_existing_raw_operator(self) -> None:
        operator_ns = f"op-default-ident-collision-{self.random_suffix}"
        release_name = f"myoper-helm-default-ident-collision-{self.random_suffix}"
        error_fragment = (
            'Invalid install: ClusterRole "mysql-operator" already exists and '
            'conflicts with the cluster-scoped identity derived from '
            'deployment.name "mysql-operator"'
        )

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name

        install_error = None

        try:
            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
            except Exception as exc:
                install_error = exc

            self.assertIsNotNone(
                install_error,
                msg=(
                    "Helm install with the default cluster-scoped identity "
                    "unexpectedly succeeded while another default operator "
                    "already existed"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, helm_options.deployment_name, check=False),
                msg=(
                    "Helm chart should reject the default cluster-scoped identity "
                    f"before creating deployment {operator_ns}/{helm_options.deployment_name}"
                ),
            )

            install_error_output = (
                getattr(install_error, "stdout", None)
                or getattr(install_error, "output", None)
                or str(install_error)
            )
            self.assertIn(error_fragment, install_error_output)
        finally:
            uninstall_with_helm(
                release_key=get_helm_release_target(helm_options),
                delete_namespace=True,
            )


class HelmSingleGlobalOperatorInstallGuardTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def _build_clusterwide_helm_options(
        self,
        *,
        namespace: str,
        release_name: str,
        deployment_name: str,
        source_operator_deployment: dict,
    ) -> HelmOperatorInstallOptions:
        helm_options = resolve_install_with_helm_options(
            namespace=namespace,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        helm_options.operator_values["deployment"]["name"] = deployment_name
        helm_options.operator_values["deployment"]["standalone"] = False
        helm_options.operator_values["deployment"]["namespaces"] = []
        return helm_options

    def test_second_global_operator_fails_fast_even_with_unique_identity(self) -> None:
        first_operator_ns = f"op-global-first-helm-{self.random_suffix}"
        second_operator_ns = f"op-global-second-helm-{self.random_suffix}"
        first_operator_name = f"myop-global-first-{self.random_suffix}"
        second_operator_name = f"myop-global-second-{self.random_suffix}"
        first_release_name = f"myoper-helm-global-first-{self.random_suffix}"
        second_release_name = f"myoper-helm-global-second-{self.random_suffix}"
        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        first_helm_options = self._build_clusterwide_helm_options(
            namespace=first_operator_ns,
            release_name=first_release_name,
            deployment_name=first_operator_name,
            source_operator_deployment=source_operator_deployment,
        )
        second_helm_options = self._build_clusterwide_helm_options(
            namespace=second_operator_ns,
            release_name=second_release_name,
            deployment_name=second_operator_name,
            source_operator_deployment=source_operator_deployment,
        )

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        first_install_result = None
        second_install_error = None
        test_error = None

        try:
            first_install_result = install_with_helm(
                first_operator_ns,
                resolved_options=first_helm_options,
            )
            kutil.wait_deploy(
                first_install_result.options.namespace,
                first_install_result.options.deployment_name,
                timeout=300,
            )

            try:
                install_with_helm(
                    second_operator_ns,
                    resolved_options=second_helm_options,
                )
            except Exception as exc:
                second_install_error = exc

            self.assertIsNotNone(
                second_install_error,
                msg=(
                    "Helm install of a second cluster-wide non-standalone "
                    "operator unexpectedly succeeded"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(
                    second_operator_ns,
                    second_helm_options.deployment_name,
                    check=False,
                ),
                msg=(
                    "Helm chart should reject a second global operator before "
                    f"creating deployment {second_operator_ns}/{second_helm_options.deployment_name}"
                ),
            )

            install_error_output = (
                getattr(second_install_error, "stdout", None)
                or getattr(second_install_error, "output", None)
                or str(second_install_error)
            )
            self.assertIn(
                f"overlaps existing operator {first_operator_ns}/{first_operator_name}",
                install_error_output,
            )
            self.assertIn(
                "a global operator overlaps every namespace",
                install_error_output,
            )
        except Exception as exc:
            test_error = exc
            if first_install_result is not None:
                try:
                    print_operator_log(
                        first_install_result.options.namespace,
                        first_install_result.options.deployment_name,
                    )
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                try:
                    uninstall_with_helm(
                        release_key=(
                            first_install_result.release_key
                            if first_install_result is not None
                            else get_helm_release_target(first_helm_options)
                        ),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                try:
                    uninstall_with_helm(
                        release_key=get_helm_release_target(second_helm_options),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_scoped_helm_install_fails_when_global_operator_exists(self) -> None:
        first_operator_ns = f"op-global-first-helm-{self.random_suffix}"
        second_operator_ns = f"op-scope-after-g-{self.random_suffix}"
        first_operator_name = f"myop-global-first-{self.random_suffix}"
        second_operator_name = f"myop-scope-after-g-{self.random_suffix}"
        first_release_name = f"myoper-helm-global-first-{self.random_suffix}"
        second_release_name = f"myoper-helm-scoped-after-global-{self.random_suffix}"
        second_watch_namespace = f"db-scope-after-g-{self.random_suffix}"

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        first_helm_options = self._build_clusterwide_helm_options(
            namespace=first_operator_ns,
            release_name=first_release_name,
            deployment_name=first_operator_name,
            source_operator_deployment=source_operator_deployment,
        )
        second_helm_options = resolve_install_with_helm_options(
            namespace=second_operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        second_helm_options.release_name = second_release_name
        second_helm_options.operator_values["deployment"]["name"] = second_operator_name
        second_helm_options.operator_values["deployment"]["standalone"] = False
        second_helm_options.operator_values["deployment"]["namespaces"] = [
            second_watch_namespace,
        ]

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        first_install_result = None
        second_install_error = None
        test_error = None

        try:
            first_install_result = install_with_helm(
                first_operator_ns,
                resolved_options=first_helm_options,
            )
            kutil.wait_deploy(
                first_install_result.options.namespace,
                first_install_result.options.deployment_name,
                timeout=300,
            )

            try:
                install_with_helm(
                    second_operator_ns,
                    resolved_options=second_helm_options,
                )
            except Exception as exc:
                second_install_error = exc

            self.assertIsNotNone(
                second_install_error,
                msg=(
                    "Helm install unexpectedly allowed a scoped operator "
                    "to coexist with a global operator"
                ),
            )
            install_error_output = (
                getattr(second_install_error, "stdout", None)
                or getattr(second_install_error, "output", None)
                or str(second_install_error)
            )
            self.assertIn(
                f"overlaps existing operator {first_operator_ns}/{first_operator_name}",
                install_error_output,
            )
            self.assertIn(
                "a global operator overlaps every namespace",
                install_error_output,
            )
            self.assertIsNone(
                kutil.get_deploy(
                    second_operator_ns,
                    second_operator_name,
                    check=False,
                ),
                msg=(
                    "Helm chart should reject a scoped operator when a global "
                    f"operator already exists before creating {second_operator_ns}/{second_operator_name}"
                ),
            )
        except Exception as exc:
            test_error = exc
            if first_install_result is not None:
                try:
                    print_operator_log(
                        first_install_result.options.namespace,
                        first_install_result.options.deployment_name,
                    )
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                try:
                    uninstall_with_helm(
                        release_key=(
                            first_install_result.release_key
                            if first_install_result is not None
                            else get_helm_release_target(first_helm_options)
                        ),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                try:
                    uninstall_with_helm(
                        release_key=get_helm_release_target(second_helm_options),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error

    def test_global_helm_install_rejects_unlabeled_legacy_raw_operator(self) -> None:
        legacy_operator_ns = f"op-global-legacy-raw-{self.random_suffix}"
        second_operator_ns = f"op-gl-after-leg-{self.random_suffix}"
        second_operator_name = f"myop-gl-after-leg-{self.random_suffix}"
        legacy_release_name = f"myoper-legacy-raw-{self.random_suffix}"
        second_release_name = f"myoper-helm-after-legacy-{self.random_suffix}"

        legacy_artifacts = get_patched_artifacts(
            copy.deepcopy(self.artifacts),
            legacy_release_name,
            legacy_operator_ns,
            self.operator_deploy_name,
        )
        apply_legacy_global_operator_fingerprint(legacy_artifacts["deployment"])

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        legacy_install_result = None
        second_helm_options = None
        second_install_error = None
        test_error = None

        try:
            legacy_install_result = install_operator(
                legacy_artifacts,
                legacy_operator_ns,
            )
            kutil.wait_deploy(
                legacy_operator_ns,
                self.operator_deploy_name,
                timeout=300,
            )

            source_operator_deployment = kutil.get_deploy(
                legacy_operator_ns,
                self.operator_deploy_name,
            )
            second_helm_options = self._build_clusterwide_helm_options(
                namespace=second_operator_ns,
                release_name=second_release_name,
                deployment_name=second_operator_name,
                source_operator_deployment=source_operator_deployment,
            )

            try:
                install_with_helm(
                    second_operator_ns,
                    resolved_options=second_helm_options,
                )
            except Exception as exc:
                second_install_error = exc

            self.assertIsNotNone(
                second_install_error,
                msg=(
                    "Helm install of a cluster-wide non-standalone operator "
                    "unexpectedly succeeded alongside an unlabeled legacy raw install"
                ),
            )
            self.assertIsNone(
                kutil.get_deploy(
                    second_operator_ns,
                    second_operator_name,
                    check=False,
                ),
                msg=(
                    "Helm chart should reject a second global operator before "
                    f"creating deployment {second_operator_ns}/{second_operator_name}"
                ),
            )

            install_error_output = (
                getattr(second_install_error, "stdout", None)
                or getattr(second_install_error, "output", None)
                or str(second_install_error)
            )
            self.assertIn(
                f"overlaps existing operator {legacy_operator_ns}/{self.operator_deploy_name}",
                install_error_output,
            )
            self.assertIn(
                "a global operator overlaps every namespace",
                install_error_output,
            )
        except Exception as exc:
            test_error = exc
            if legacy_install_result is not None:
                try:
                    print_operator_log(
                        legacy_operator_ns,
                        self.operator_deploy_name,
                    )
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                if legacy_install_result is not None:
                    try:
                        remove_operator(
                            operator_ns=legacy_operator_ns,
                            operator_deploy_name=self.operator_deploy_name,
                            install_result=legacy_install_result,
                            return_manifests=False,
                            delete_namespace=True,
                            check_namespace_empty=False,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                if second_helm_options is not None:
                    try:
                        uninstall_with_helm(
                            release_key=get_helm_release_target(second_helm_options),
                            delete_namespace=True,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class NamespaceNormalizationAndValidationTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_whitespace_namespaces_are_trimmed_for_peerings_and_env(self) -> None:
        operator_ns = f"op-space-ns-{self.random_suffix}"
        watched_ns1 = f"db-space-watch1-{self.random_suffix}"
        watched_ns2 = f"db-space-watch2-{self.random_suffix}"
        operator_name = f"myop-space-ns-{self.random_suffix}"
        release_name = f"myoper-space-ns-{self.random_suffix}"
        raw_watch_namespaces = f"  {watched_ns1} ,   {watched_ns2}  "
        normalized_watch_namespaces = normalize_watch_namespaces(raw_watch_namespaces)

        artifacts = self.remove_operator()

        kutil.create_ns(watched_ns1, labels={})
        kutil.create_ns(watched_ns2, labels={})

        patched = get_patched_artifacts(
            artifacts,
            release_name,
            operator_ns,
            operator_name,
            watch_namespaces=normalized_watch_namespaces,
        )

        peering_name = get_env_var_peering_name(operator_ns, operator_name, normalized_watch_namespaces)
        self.assertEqual(set(patched["kopfpeerings"].keys()), {
            f"{watched_ns1}-{peering_name}",
            f"{watched_ns2}-{peering_name}",
        })

        install_operator(patched, operator_ns)
        kutil.wait_deploy(operator_ns, operator_name, timeout=300)

        operator_deployment = kutil.get_deploy(operator_ns, operator_name)
        envs = get_operator_envs_from_deployment(operator_deployment)

        self.assertEqual(envs["OPERATOR_NAMESPACES"], normalized_watch_namespaces)

        peerings_ns1 = kutil.ls_kopfpeering(watched_ns1, get_kopf_peerings_pattern(operator_name))
        peerings_ns2 = kutil.ls_kopfpeering(watched_ns2, get_kopf_peerings_pattern(operator_name))
        self.assertEqual(len(peerings_ns1), 1)
        self.assertEqual(len(peerings_ns2), 1)

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
            crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
            ckopf_peerings_pattern="",
            sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
            role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
            role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
            kopf_peerings_pattern="",
            return_manifests=False
        )

        kutil.delete_kopfpeering(watched_ns2, get_namespaced_peering_name(operator_name))
        kutil.delete_kopfpeering(watched_ns1, get_namespaced_peering_name(operator_name))
        kutil.delete_ns(watched_ns2)
        kutil.delete_ns(watched_ns1)

    def test_watch_namespaces_are_trimmed_and_deduped(self) -> None:
        explicit_ns = f"db-runtime-explicit-{self.random_suffix}"
        second_ns = f"db-runtime-second-{self.random_suffix}"
        raw_watch_namespaces = f"  {explicit_ns} , {second_ns} , {explicit_ns} ,  {second_ns}  "

        self.assertEqual(
            get_explicit_watch_namespaces(raw_watch_namespaces),
            [explicit_ns, second_ns],
        )
        self.assertEqual(
            normalize_watch_namespaces(raw_watch_namespaces),
            f"{explicit_ns},{second_ns}",
        )


class HelmNamespaceNormalizationAndValidationTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_whitespace_namespaces_are_trimmed_for_peerings_and_env(self) -> None:
        operator_ns = f"op-space-ns-helm-{self.random_suffix}"
        watched_ns1 = f"db-space-watch1-helm-{self.random_suffix}"
        watched_ns2 = f"db-space-watch2-helm-{self.random_suffix}"
        operator_name = f"myop-space-ns-helm-{self.random_suffix}"
        release_name = f"myoper-helm-space-ns-{self.random_suffix}"
        raw_watch_namespaces = [
            f"  {watched_ns1} ",
            f"   {watched_ns2}  ",
            f" {watched_ns1} ",
        ]
        normalized_watch_namespaces = f"{watched_ns1},{watched_ns2}"

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.namespaces
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = raw_watch_namespaces

        install_result = None

        try:
            kutil.create_ns(watched_ns1, labels={})
            kutil.create_ns(watched_ns2, labels={})

            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            envs = get_operator_envs_from_deployment(operator_deployment)
            peering_name = get_env_var_peering_name(
                install_result.options.namespace,
                install_result.options.deployment_name,
                normalized_watch_namespaces,
            )

            self.assertEqual(envs["OPERATOR_NAMESPACES"], normalized_watch_namespaces)

            peerings_ns1 = kutil.ls_kopfpeering(
                watched_ns1,
                get_kopf_peerings_pattern(install_result.options.deployment_name),
            )
            peerings_ns2 = kutil.ls_kopfpeering(
                watched_ns2,
                get_kopf_peerings_pattern(install_result.options.deployment_name),
            )
            self.assertEqual({peering["NAME"] for peering in peerings_ns1}, {peering_name})
            self.assertEqual({peering["NAME"] for peering in peerings_ns2}, {peering_name})
        finally:
            active_options = install_result.options if install_result is not None else helm_options
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                    check_namespace_empty=True,
                )
                self.assertEqual(
                    kutil.ls_kopfpeering(
                        watched_ns1,
                        get_kopf_peerings_pattern(active_options.deployment_name),
                    ),
                    [],
                )
                self.assertEqual(
                    kutil.ls_kopfpeering(
                        watched_ns2,
                        get_kopf_peerings_pattern(active_options.deployment_name),
                    ),
                    [],
                )
                assert_no_peering(
                    self,
                    active_options.namespace,
                    active_options.deployment_name,
                )
            finally:
                kutil.delete_ns(watched_ns2)
                kutil.delete_ns(watched_ns1)
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)

    def test_watch_namespaces_are_trimmed_and_deduped(self) -> None:
        explicit_ns = f"db-runtime-explicit-helm-{self.random_suffix}"
        second_ns = f"db-runtime-second-helm-{self.random_suffix}"
        raw_watch_namespaces = [
            f"  {explicit_ns} ",
            f" {second_ns} ",
            explicit_ns,
            f"  {second_ns}  ",
        ]

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        helm_options = resolve_install_with_helm_options(
            namespace=self.operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = (
            f"myoper-helm-space-ns-normalize-{self.random_suffix}"
        )
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.namespaces
        helm_options.operator_values["deployment"]["namespaces"] = raw_watch_namespaces

        self.assertEqual(
            helm_options.operator_namespaces_list,
            [explicit_ns, second_ns],
        )
        self.assertEqual(
            helm_options.operator_namespaces,
            f"{explicit_ns},{second_ns}",
        )


class StandaloneMultiNamespaceOperatorInstallTest(OperatorSingleAndMultipleBaseTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multi_namespace_watch_list_is_preserved_in_standalone_config(self) -> None:
        operator_ns = f"op-stand-multi-{self.random_suffix}"
        watched_ns1 = f"db-stand-multi-a-{self.random_suffix}"
        watched_ns2 = f"db-stand-multi-b-{self.random_suffix}"
        operator_name = f"myop-stand-multi-{self.random_suffix}"
        release_name = f"myoper-stand-multi-{self.random_suffix}"
        watch_namespaces = f"{watched_ns1},{watched_ns2}"

        artifacts = self.remove_operator()

        kutil.create_ns(watched_ns1, labels={})
        kutil.create_ns(watched_ns2, labels={})

        patched = get_patched_artifacts(
            artifacts,
            release_name,
            operator_ns,
            operator_name,
            watch_namespaces=watch_namespaces,
            standalone=True,
        )
        self.assertEqual(patched["clusterkopfpeerings"], {})
        self.assertEqual(patched["kopfpeerings"], {})

        install_operator(patched, operator_ns)
        kutil.wait_deploy(operator_ns, operator_name, timeout=300)

        operator_deployment = kutil.get_deploy(operator_ns, operator_name)
        envs = get_operator_envs_from_deployment(operator_deployment)

        self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
        self.assertEqual(envs["OPERATOR_NAMESPACES"], watch_namespaces)
        assert_no_peering(self, operator_ns, operator_name)

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
            crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
            ckopf_peerings_pattern=get_ckopf_peerings_pattern(operator_ns, operator_name),
            sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
            role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
            role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
            kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
            return_manifests=False
        )

        kutil.delete_ns(watched_ns2)
        kutil.delete_ns(watched_ns1)


class HelmStandaloneMultiNamespaceOperatorInstallTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multi_namespace_watch_list_is_preserved_in_standalone_config(self) -> None:
        operator_ns = f"op-stand-multi-helm-{self.random_suffix}"
        watched_ns1 = f"db-stand-multi-a-helm-{self.random_suffix}"
        watched_ns2 = f"db-stand-multi-b-helm-{self.random_suffix}"
        operator_name = f"myop-stand-multi-helm-{self.random_suffix}"
        release_name = f"myoper-helm-stand-multi-{self.random_suffix}"
        watch_namespaces = [watched_ns1, watched_ns2]
        watch_namespaces_env = ",".join(watch_namespaces)

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.namespaces, deployment.standalone
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = watch_namespaces
        helm_options.operator_values["deployment"]["standalone"] = True

        install_result = None

        try:
            kutil.create_ns(watched_ns1, labels={})
            kutil.create_ns(watched_ns2, labels={})

            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            envs = get_operator_envs_from_deployment(operator_deployment)

            self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
            self.assertEqual(envs["OPERATOR_NAMESPACES"], watch_namespaces_env)
            assert_no_peering(
                self,
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
        except Exception:
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            active_release_key = (
                install_result.release_key
                if install_result is not None
                else get_helm_release_target(helm_options)
            )
            try:
                uninstall_with_helm(
                    release_key=active_release_key,
                    delete_namespace=True,
                )
            finally:
                try:
                    kutil.delete_ns(watched_ns2)
                except Exception:
                    pass
                try:
                    kutil.delete_ns(watched_ns1)
                except Exception:
                    pass
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)


class StandaloneOperatorsTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_operators(self) -> None:
        namespaces_to_clean = []
        all_ics = []
        operators_to_clean = []
        watched_ics = []
        # bump operator_count to two to see HTTP 409 in the log because one operator is faster
        # then the other(s) in creating resources
        operator_count = 1
        op_watched_ns = []
        standalone = True

        artifacts = self.remove_operator()
        operator_ns = f"op-stand-{self.random_suffix}"
        db_ns = f"db-stand-op-{self.random_suffix}"
        op_watched_ns.append(db_ns)
        watched_ics.extend([(db_ns, f"ic-standalone-op")])
        for db_ns in op_watched_ns:
            kutil.create_ns(db_ns, labels={})
            namespaces_to_clean.extend([db_ns])

        for operator_nr in range(1, operator_count + 1):
            operator_name = f"myop-stand-op{operator_nr}-{self.random_suffix}"
            release_name = f"myoper-stand-op{operator_nr}-{self.random_suffix}"

            operators_to_clean.append((operator_ns, operator_name))

            watched_ns_str = ",".join(op_watched_ns)
            patched = get_patched_artifacts(artifacts, release_name, operator_ns, operator_name, watch_namespaces=watched_ns_str, standalone=standalone, operator_debug=False)
            install_operator(patched, operator_ns, check_ns=False)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

        print(watched_ics)

        all_ics = watched_ics

        for ns, cluster_name in all_ics:
            kutil.create_user_secrets(ns, self.cluster_secret_name, "root", "%", "sakila")
            yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
  namespace: {ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
            kutil.apply(ns, yaml)

        try:
            for ns, cluster_name in watched_ics:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)

                if self.routers_count:
                    self.wait_routers(f"{cluster_name}-router.*", self.routers_count, timeout=self.cluster_size*120, wait=10, ns=ns)
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)
        except Exception as exc:
            for operator_ns, operator_name in operators_to_clean:
                print_operator_log(operator_ns, operator_name)
            raise

        # Cleanup
        for ns, cluster_name in all_ics:
            print(f"{ns=} {cluster_name=}")
            kutil.delete_ic(ns, cluster_name)
            self.wait_pods_gone(f"{cluster_name}.*", ns=ns)
            self.wait_routers_gone(f"{cluster_name}-router.*", ns=ns)
            self.wait_ic_gone(cluster_name, ns=ns)
            kutil.delete_pvc(ns, None)
            kutil.delete_secret(ns, self.cluster_secret_name)

        for ns in reversed(namespaces_to_clean):
            print(f"Removing {ns} namespace")
            kutil.delete_ns(ns)

        for operator_ns, operator_name in reversed(operators_to_clean):
            remove_operator(
                operator_ns=operator_ns,
                operator_deploy_name=operator_name,
                crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
                crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
                ckopf_peerings_pattern="", # Not generated when watch_namespaces is present
                sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
                role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
                role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
                kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
                return_manifests=False
            )


class OverlappingStandaloneOpsInSameNsRejectedTest(OperatorSingleAndMultipleBaseTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_second_standalone_operator_in_same_namespace_fails_fast(self) -> None:
        operator_ns = f"op-stand-samens-{self.random_suffix}"
        cluster_ns = operator_ns
        first_operator_name = f"myop-stand-samens-1-{self.random_suffix}"
        second_operator_name = f"myop-stand-samens-2-{self.random_suffix}"
        operators_to_clean = [first_operator_name, second_operator_name]
        artifacts = self.remove_operator()

        kutil.create_ns(cluster_ns, labels={})

        try:
            first_patched = get_patched_artifacts(
                artifacts,
                f"myoper-stand-samens-1-{self.random_suffix}",
                operator_ns,
                first_operator_name,
                watch_namespaces=cluster_ns,
                standalone=True,
            )
            self.assertEqual(first_patched["clusterkopfpeerings"], {})
            self.assertEqual(first_patched["kopfpeerings"], {})
            install_operator(first_patched, operator_ns, check_ns=False)
            kutil.wait_deploy(operator_ns, first_operator_name, timeout=300)

            first_operator_deployment = kutil.get_deploy(operator_ns, first_operator_name)
            first_envs = get_operator_envs_from_deployment(first_operator_deployment)
            self.assertEqual(first_envs["OPERATOR_STANDALONE"], "true")
            self.assertEqual(first_envs["OPERATOR_NAMESPACES"], cluster_ns)
            assert_no_peering(self, operator_ns, first_operator_name)

            second_patched = get_patched_artifacts(
                artifacts,
                f"myoper-stand-samens-2-{self.random_suffix}",
                operator_ns,
                second_operator_name,
                watch_namespaces=cluster_ns,
                standalone=True,
            )
            self.assertEqual(second_patched["clusterkopfpeerings"], {})
            self.assertEqual(second_patched["kopfpeerings"], {})
            install_operator(second_patched, operator_ns, check_ns=False)

            error_fragment = (
                f"Operator topology conflicts with existing operator "
                f"{operator_ns}/{first_operator_name}; overlapping namespaces: "
                f"{cluster_ns}."
            )
            wait_for_operator_failure_log_fragment(
                operator_ns,
                second_operator_name,
                error_fragment,
            )
        except Exception:
            for operator_name in operators_to_clean:
                try:
                    print_operator_log(operator_ns, operator_name)
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                for operator_name in reversed(operators_to_clean):
                    try:
                        remove_operator(
                            operator_ns=operator_ns,
                            operator_deploy_name=operator_name,
                            return_manifests=False,
                            delete_namespace=False,
                            check_namespace_empty=False,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                if cluster_ns in kutil.ls_ns_ex():
                    try:
                        try:
                            wait_for_namespace_empty(cluster_ns, timeout=60)
                        except RuntimeError:
                            remaining_objects = kutil.ls_all_raw(cluster_ns)
                            if remaining_objects:
                                raise AssertionError(
                                    f"Namespace {cluster_ns} still contains objects after "
                                    f"removing overlapping standalone operators:\n"
                                    f"{remaining_objects}"
                                )
                        kutil.delete_ns(cluster_ns)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                if cleanup_error is not None:
                    raise cleanup_error


class KubectlCleanupOwnershipRegressionTest(OperatorSingleAndMultipleBaseTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_removing_operator_keeps_similarly_named_operator_resources(self) -> None:
        operator_ns = f"op-clean-samens-{self.random_suffix}"
        first_watch_namespace = f"db-clean-1-{self.random_suffix}"
        second_watch_namespace = f"db-clean-10-{self.random_suffix}"
        first_operator_name = f"myop-clean-1-{self.random_suffix}"
        second_operator_name = f"myop-clean-10-{self.random_suffix}"
        operators_to_clean: list[str] = []
        namespaces_to_clean = [
            operator_ns,
            first_watch_namespace,
            second_watch_namespace,
        ]
        test_error = None

        original_artifacts = self._remove_default_operator_or_fail()

        try:
            for namespace in namespaces_to_clean:
                kutil.create_ns(namespace, labels={})

            for operator_name, release_name, watched_namespace in (
                (
                    first_operator_name,
                    f"myoper-clean-1-{self.random_suffix}",
                    first_watch_namespace,
                ),
                (
                    second_operator_name,
                    f"myoper-clean-10-{self.random_suffix}",
                    second_watch_namespace,
                ),
            ):
                patched = get_patched_artifacts(
                    copy.deepcopy(original_artifacts),
                    release_name,
                    operator_ns,
                    operator_name,
                    watch_namespaces=watched_namespace,
                )
                install_operator(patched, operator_ns, check_ns=False)
                operators_to_clean.append(operator_name)
                kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            self.wait(
                lambda: kutil.get(
                    second_watch_namespace,
                    "kopfpeering",
                    get_namespaced_peering_name(second_operator_name),
                    check=False,
                )
                is not None,
                timeout=60,
                delay=2,
            )

            remove_operator(
                operator_ns=operator_ns,
                operator_deploy_name=first_operator_name,
                return_manifests=False,
                delete_namespace=False,
                check_namespace_empty=False,
            )
            operators_to_clean = [second_operator_name]

            self.assertIsNone(
                kutil.get_deploy(operator_ns, first_operator_name, check=False)
            )
            self.assertIsNotNone(
                kutil.get_deploy(operator_ns, second_operator_name, check=False)
            )
            self.assertIsNotNone(
                kutil.get_sa(
                    operator_ns,
                    get_service_account_name(operator_ns, second_operator_name),
                    check=False,
                )
            )
            self.assertIsNotNone(
                kutil.get(
                    None,
                    "clusterrole",
                    get_operator_role_name(operator_ns, second_operator_name),
                    check=False,
                )
            )
            self.assertIsNotNone(
                kutil.get(
                    None,
                    "clusterrolebinding",
                    get_operator_rolebinding_name(
                        operator_ns,
                        second_operator_name,
                    ),
                    check=False,
                )
            )
            self.assertIsNotNone(
                kutil.get(
                    second_watch_namespace,
                    "kopfpeering",
                    get_namespaced_peering_name(second_operator_name),
                    check=False,
                )
            )
        except Exception as exc:
            test_error = exc
            for operator_name in operators_to_clean:
                try:
                    print_operator_log(operator_ns, operator_name)
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            for operator_name in reversed(operators_to_clean):
                try:
                    self._cleanup_operator_if_present(
                        operator_ns=operator_ns,
                        operator_name=operator_name,
                        delete_namespace=False,
                        check_namespace_empty=False,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            for namespace in reversed(namespaces_to_clean):
                if namespace not in kutil.ls_ns_ex():
                    continue
                try:
                    wait_for_namespace_empty(namespace, timeout=60)
                except RuntimeError:
                    remaining_objects = kutil.ls_all_raw(namespace)
                    if remaining_objects:
                        if cleanup_error is None:
                            cleanup_error = AssertionError(
                                f"Namespace {namespace} still contains objects after "
                                f"removing similarly named kubectl operators:\n"
                                f"{remaining_objects}"
                            )
                        continue

                try:
                    kutil.delete_ns(namespace)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            try:
                self._restore_default_operator(original_artifacts)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class HelmOverlappingStandaloneOpsInSameNsRejectedTest(OperatorSingleAndMultipleBaseTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_second_standalone_operator_in_same_namespace_is_rejected_by_helm(self) -> None:
        # Keep these names short enough for the chart-derived
        # app.kubernetes.io/instance label value.
        operator_ns = f"op-stand-samens-{self.random_suffix}"
        cluster_ns = operator_ns
        first_operator_name = f"myop-stand-samens-1-{self.random_suffix}"
        second_operator_name = f"myop-stand-samens-2-{self.random_suffix}"
        first_release_name = f"myoper-helm-stand-samens-1-{self.random_suffix}"
        second_release_name = f"myoper-helm-stand-samens-2-{self.random_suffix}"
        operators_to_clean = []
        test_error = None

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        first_install_result = None
        second_helm_options = None
        second_install_error = None

        try:
            kutil.create_ns(cluster_ns, labels={})
            first_helm_options = resolve_install_with_helm_options(
                namespace=operator_ns,
                source_operator_namespace=self.operator_ns,
                source_operator_deployment_name=self.operator_deploy_name,
                source_operator_deployment=source_operator_deployment,
            )
            first_helm_options.release_name = first_release_name
            first_helm_options.operator_values["deployment"]["name"] = first_operator_name
            first_helm_options.operator_values["deployment"]["namespaces"] = [cluster_ns]
            first_helm_options.operator_values["deployment"]["standalone"] = True
            operators_to_clean.append(first_helm_options)

            first_install_result = install_with_helm(
                operator_ns,
                resolved_options=first_helm_options,
            )
            kutil.wait_deploy(
                first_install_result.options.namespace,
                first_install_result.options.deployment_name,
                timeout=300,
            )

            first_operator_deployment = kutil.get_deploy(
                first_install_result.options.namespace,
                first_install_result.options.deployment_name,
            )
            first_envs = get_operator_envs_from_deployment(first_operator_deployment)

            self.assertEqual(first_envs["OPERATOR_STANDALONE"], "true")
            self.assertEqual(first_envs["OPERATOR_NAMESPACES"], cluster_ns)
            self._assert_helm_operator_selector_isolation(
                namespace=first_install_result.options.namespace,
                deployment_name=first_install_result.options.deployment_name,
                expected_deployment_selector_labels=(
                    get_deployment_selector_labels(
                        first_install_result.options.namespace,
                        first_install_result.options.deployment_name,
                    )
                ),
            )
            assert_no_peering(
                self,
                first_install_result.options.namespace,
                first_install_result.options.deployment_name,
            )

            second_helm_options = resolve_install_with_helm_options(
                namespace=operator_ns,
                source_operator_namespace=self.operator_ns,
                source_operator_deployment_name=self.operator_deploy_name,
                source_operator_deployment=source_operator_deployment,
            )
            second_helm_options.release_name = second_release_name
            second_helm_options.operator_values["deployment"]["name"] = second_operator_name
            second_helm_options.operator_values["deployment"]["namespaces"] = [cluster_ns]
            second_helm_options.operator_values["deployment"]["standalone"] = True

            try:
                install_with_helm(
                    operator_ns,
                    resolved_options=second_helm_options,
                )
            except Exception as exc:
                second_install_error = exc

            self.assertIsNotNone(
                second_install_error,
                msg=(
                    "Helm install unexpectedly allowed a second standalone "
                    "operator to watch the same namespace"
                ),
            )
            second_install_error_output = (
                getattr(second_install_error, "stdout", None)
                or getattr(second_install_error, "output", None)
                or str(second_install_error)
            )
            self.assertIn(
                f"overlaps existing operator {operator_ns}/{first_operator_name}",
                second_install_error_output,
            )
            self.assertIn(
                f"overlapping namespaces: {cluster_ns}",
                second_install_error_output,
            )
            self.assertIsNone(
                kutil.get_deploy(operator_ns, second_operator_name, check=False),
                msg=(
                    "Helm chart should reject overlapping standalone topology "
                    f"before creating deployment {operator_ns}/{second_operator_name}"
                ),
            )
        except Exception as exc:
            test_error = exc
            for options in operators_to_clean:
                try:
                    print_operator_log(options.namespace, options.deployment_name)
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                for options in reversed(operators_to_clean):
                    try:
                        uninstall_with_helm(
                            release_key=get_helm_release_target(options),
                            delete_namespace=False,
                            check_namespace_empty=False,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                if second_helm_options is not None:
                    try:
                        uninstall_with_helm(
                            release_key=get_helm_release_target(second_helm_options),
                            delete_namespace=False,
                            check_namespace_empty=False,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                if cluster_ns in kutil.ls_ns_ex():
                    try:
                        try:
                            wait_for_namespace_empty(cluster_ns, timeout=60)
                        except RuntimeError:
                            remaining_objects = kutil.ls_all_raw(cluster_ns)
                            if remaining_objects:
                                print(
                                    f"Remaining objects in namespace {cluster_ns} after "
                                    f"uninstalling shared-namespace Helm releases:\n"
                                    f"{remaining_objects}"
                                )
                                raise AssertionError(
                                    f"Namespace {cluster_ns} still contains objects after "
                                    f"uninstalling shared-namespace Helm releases:\n"
                                    f"{remaining_objects}"
                                )
                        kutil.delete_ns(cluster_ns)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class MultiScopedStandaloneOpsInMultiNsNoPeeringTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_scoped_standalone_multi_namespace_operators_have_no_peering(self) -> None:
        operators_to_clean = []
        namespaces_to_clean = []
        artifacts = self.remove_operator()

        nonwatched_ns = f"db-standalone-nonwatched-{self.random_suffix}"
        kutil.create_ns(nonwatched_ns, labels={})
        namespaces_to_clean.append(nonwatched_ns)

        try:
            for i in (1, 2):
                watched_ns1 = f"db-stand-multins-{i}-watch1-{self.random_suffix}"
                watched_ns2 = f"db-stand-multins-{i}-watch2-{self.random_suffix}"
                operator_ns = f"op-stand-multins-{i}-{self.random_suffix}"
                operator_name = f"myop-stand-multins-{i}-{self.random_suffix}"
                release_name = f"myoper-stand-multins-{i}-{self.random_suffix}"
                watched_namespaces = f"{watched_ns1},{watched_ns2}"

                kutil.create_ns(watched_ns1, labels={})
                kutil.create_ns(watched_ns2, labels={})
                namespaces_to_clean.extend([watched_ns1, watched_ns2])
                operators_to_clean.append((operator_ns, operator_name))

                patched = get_patched_artifacts(
                    artifacts,
                    release_name,
                    operator_ns,
                    operator_name,
                    watch_namespaces=watched_namespaces,
                    standalone=True,
                )
                self.assertEqual(patched["clusterkopfpeerings"], {})
                self.assertEqual(patched["kopfpeerings"], {})

                install_operator(patched, operator_ns)
                kutil.wait_deploy(operator_ns, operator_name, timeout=300)

                operator_deployment = kutil.get_deploy(operator_ns, operator_name)
                envs = get_operator_envs_from_deployment(operator_deployment)

                self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
                self.assertEqual(envs["OPERATOR_NAMESPACES"], watched_namespaces)
                assert_no_peering(self, operator_ns, operator_name)
        except Exception as exc:
            for operator_ns, operator_name in operators_to_clean:
                print_operator_log(operator_ns, operator_name)
            raise

        for operator_ns, operator_name in reversed(operators_to_clean):
            remove_operator(
                operator_ns=operator_ns,
                operator_deploy_name=operator_name,
                crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
                crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
                ckopf_peerings_pattern=get_ckopf_peerings_pattern(operator_ns, operator_name),
                sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
                role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
                role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
                kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
                return_manifests=False
            )

        for ns in reversed(namespaces_to_clean):
            kutil.delete_ns(ns)


class HelmMultiScopedStandaloneOpsInMultiNsNoPeeringTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_scoped_standalone_multi_namespace_operators_have_no_peering(self) -> None:
        operators_to_clean = []
        operator_namespaces_to_clean = []
        namespaces_to_clean = []
        test_error = None

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        nonwatched_ns = f"db-standalone-nonwatched-helm-{self.random_suffix}"

        try:
            kutil.create_ns(nonwatched_ns, labels={})
            namespaces_to_clean.append(nonwatched_ns)

            for i in (1, 2):
                watched_ns1 = f"db-stand-multins-helm-{i}-watch1-{self.random_suffix}"
                watched_ns2 = f"db-stand-multins-helm-{i}-watch2-{self.random_suffix}"
                # Keep these names short enough for the chart-derived
                # app.kubernetes.io/instance label value.
                operator_ns = f"op-stand-multins-{i}-{self.random_suffix}"
                operator_name = f"myop-stand-multins-{i}-{self.random_suffix}"
                release_name = f"myoper-helm-stand-multins-{i}-{self.random_suffix}"
                watched_namespaces = [watched_ns1, watched_ns2]
                watched_namespaces_env = ",".join(watched_namespaces)

                kutil.create_ns(watched_ns1, labels={})
                kutil.create_ns(watched_ns2, labels={})
                namespaces_to_clean.extend([watched_ns1, watched_ns2])
                operator_namespaces_to_clean.append(operator_ns)

                helm_options = resolve_install_with_helm_options(
                    namespace=operator_ns,
                    source_operator_namespace=self.operator_ns,
                    source_operator_deployment_name=self.operator_deploy_name,
                    source_operator_deployment=source_operator_deployment,
                )
                helm_options.release_name = release_name
                # See helm/mysql-operator/values.yaml for the deployment values layout.
                # Keep these keys in sync if the chart changes:
                # deployment.name, deployment.namespaces, deployment.standalone
                helm_options.operator_values["deployment"]["name"] = operator_name
                helm_options.operator_values["deployment"]["namespaces"] = watched_namespaces
                helm_options.operator_values["deployment"]["standalone"] = True
                operators_to_clean.append(helm_options)

                install_result = install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
                kutil.wait_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                    timeout=300,
                )

                operator_deployment = kutil.get_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                envs = get_operator_envs_from_deployment(operator_deployment)

                self.assertEqual(envs["OPERATOR_STANDALONE"], "true")
                self.assertEqual(envs["OPERATOR_NAMESPACES"], watched_namespaces_env)
                assert_no_peering(
                    self,
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
        except Exception as exc:
            test_error = exc
            for options in operators_to_clean:
                try:
                    print_operator_log(options.namespace, options.deployment_name)
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                for options in reversed(operators_to_clean):
                    try:
                        uninstall_with_helm(
                            release_key=get_helm_release_target(options),
                            delete_namespace=False,
                            check_namespace_empty=False,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                for namespace in reversed(operator_namespaces_to_clean):
                    if namespace in kutil.ls_ns_ex():
                        try:
                            try:
                                wait_for_namespace_empty(namespace, timeout=60)
                            except RuntimeError:
                                remaining_objects = kutil.ls_all_raw(namespace)
                                if remaining_objects:
                                    print(
                                        f"Remaining objects in namespace {namespace} after "
                                        f"uninstalling multi-namespace standalone Helm releases:\n"
                                        f"{remaining_objects}"
                                    )
                                    raise AssertionError(
                                        f"Namespace {namespace} still contains objects after "
                                        f"uninstalling multi-namespace standalone Helm releases:\n"
                                        f"{remaining_objects}"
                                    )
                            kutil.delete_ns(namespace)
                        except Exception as exc:
                            if cleanup_error is None:
                                cleanup_error = exc

                for namespace in reversed(namespaces_to_clean):
                    try:
                        if namespace in kutil.ls_ns_ex():
                            kutil.delete_ns(namespace)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class SingleOperatorRollingUpdateTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_rolling_update(self) -> None:
        operator_ns = f"op-roll-upd-{self.random_suffix}"
        cluster_ns = f"db-roll-upd-{self.random_suffix}"
        operator_name = f"myop-roll-upd-{self.random_suffix}"
        release_name = f"myoper-roll-upd-{self.random_suffix}"

        custom_meta = {"labels": {"v": "v1"}}

        artifacts = self.remove_operator()

        patched = get_patched_artifacts(artifacts, release_name, operator_ns, operator_name, custom_meta=custom_meta)
        install_operator(patched, operator_ns)
        kutil.wait_deploy(operator_ns, operator_name, timeout=300)

        kutil.create_ns(cluster_ns, labels={})
        kutil.create_user_secrets(cluster_ns, self.cluster_secret_name, "root", "%", "sakila")

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
  namespace: {cluster_ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
        kutil.apply(cluster_ns, yaml)

        try:
            self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=cluster_ns)

            for instance in range(0, self.cluster_size):
                self.wait_pod(f"{self.cluster_name}-{instance}", "Running", ns=cluster_ns)

            if self.routers_count:
                self.wait_routers(f"{self.cluster_name}-router.*", self.routers_count, timeout=self.cluster_size*120, wait=10, ns=cluster_ns)

            self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, ns=cluster_ns)
        except Exception as exc:
            print_operator_log(operator_ns, operator_name)
            raise

        previous_operator_pod = self._get_single_operator_pod(
            namespace=operator_ns,
            deployment_name=operator_name,
        )
        patch_payload = {"spec": {"template": {"metadata": {"labels": {"check": "up"}}}}}
        kutil.patch_dp(operator_ns, operator_name, patch_payload)
        self._wait_for_operator_pod_rollout(
            namespace=operator_ns,
            deployment_name=operator_name,
            previous_pod_name=previous_operator_pod["metadata"]["name"],
        )

        # Cleanup
        kutil.delete_ic(cluster_ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}.*", ns=cluster_ns)
        self.wait_routers_gone(f"{self.cluster_name}-router.*", ns=cluster_ns)
        self.wait_ic_gone(self.cluster_name, ns=cluster_ns)
        kutil.delete_pvc(cluster_ns, None)
        kutil.delete_secret(cluster_ns, self.cluster_secret_name)
        kutil.delete_ns(cluster_ns)

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
            crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
            ckopf_peerings_pattern=get_ckopf_peerings_pattern(operator_ns, operator_name),
            sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
            role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
            role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
            kopf_peerings_pattern="",
            return_manifests=False
        )


class HelmSingleOperatorRollingUpdateTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_rolling_update(self) -> None:
        operator_ns = f"op-roll-upd-helm-{self.random_suffix}"
        cluster_ns = f"db-roll-upd-helm-{self.random_suffix}"
        operator_name = f"myop-roll-upd-helm-{self.random_suffix}"
        release_name = f"myoper-helm-roll-upd-{self.random_suffix}"

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep this key in sync if the chart changes:
        # deployment.name
        helm_options.operator_values["deployment"]["name"] = operator_name

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        install_result = None
        cluster_helm_installs = []
        test_error = None

        try:
            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            cluster_install = self._build_helm_cluster_tracker(
                cluster_ns,
                self.cluster_name,
            )
            cluster_helm_installs.append(cluster_install)
            self._install_helm_cluster(cluster_install)

            self.wait_ic(
                self.cluster_name,
                ["PENDING", "INITIALIZING", "ONLINE"],
                ns=cluster_ns,
            )

            for instance in range(0, self.cluster_size):
                self.wait_pod(
                    f"{self.cluster_name}-{instance}",
                    "Running",
                    ns=cluster_ns,
                )

            if self.routers_count:
                self.wait_routers(
                    f"{self.cluster_name}-router.*",
                    self.routers_count,
                    timeout=self.cluster_size * 120,
                    wait=10,
                    ns=cluster_ns,
                )

            self.wait_ic(
                self.cluster_name,
                "ONLINE",
                num_online=self.cluster_size,
                ns=cluster_ns,
            )

            previous_operator_pod = self._get_single_helm_operator_pod(
                namespace=install_result.options.namespace,
                deployment_name=install_result.options.deployment_name,
            )
            patch_payload = {
                "spec": {
                    "template": {
                        "metadata": {
                            "labels": {
                                "check": "up",
                            }
                        }
                    }
                }
            }
            kutil.patch_dp(
                install_result.options.namespace,
                install_result.options.deployment_name,
                patch_payload,
            )
            self._wait_for_helm_operator_pod_rollout(
                namespace=install_result.options.namespace,
                deployment_name=install_result.options.deployment_name,
                previous_pod_name=previous_operator_pod["metadata"]["name"],
            )
        except Exception as exc:
            test_error = exc
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            cleanup_error = None
            active_options = install_result.options if install_result else helm_options
            try:
                for cluster_install in reversed(cluster_helm_installs):
                    try:
                        self._cleanup_helm_cluster(cluster_install)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            except Exception as exc:
                cleanup_error = exc
            finally:
                try:
                    uninstall_with_helm(
                        release_key=(
                            install_result.release_key
                            if install_result is not None
                            else get_helm_release_target(helm_options)
                        ),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                try:
                    if cluster_ns in kutil.ls_ns_ex():
                        kutil.delete_ns(cluster_ns)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                try:
                    restore_operator(original_artifacts, self.operator_ns)
                    kutil.wait_deploy(
                        self.operator_ns,
                        self.operator_deploy_name,
                        timeout=300,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class SingleOperatorRestartReconcileTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1
    group_monitor_wait_timeout = 100
    restart_repetitions = 3
    post_restart_reconcile_wait = group_monitor_wait_timeout + 30

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_restart_reconciles_existing_clusters(self) -> None:
        operator_ns = f"op-restart-rec-{self.random_suffix}"
        cluster_ns = f"db-restart-rec-{self.random_suffix}"
        operator_name = f"myop-restart-rec-{self.random_suffix}"
        release_name = f"myoper-restart-rec-{self.random_suffix}"
        original_artifacts = self._remove_default_operator_or_fail()
        test_error = None

        custom_spec = {
            "template": {
                "spec": {
                    "containers": [{
                        "name": "mysql-operator",
                        "env": [{
                            "name": "OPERATOR_GROUP_MONITOR_POLL_TIMEOUT",
                            "value": str(self.group_monitor_wait_timeout),
                        }],
                    }],
                },
            },
        }

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
  namespace: {cluster_ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
        try:
            patched = get_patched_artifacts(
                copy.deepcopy(original_artifacts),
                release_name,
                operator_ns,
                operator_name,
                custom_spec=custom_spec,
            )
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            kutil.create_ns(cluster_ns, labels={})
            kutil.create_user_secrets(
                cluster_ns,
                self.cluster_secret_name,
                "root",
                "%",
                "sakila",
            )
            kutil.apply(cluster_ns, yaml)

            try:
                self.wait_ic(
                    self.cluster_name,
                    ["PENDING", "INITIALIZING", "ONLINE"],
                    ns=cluster_ns,
                )

                for instance in range(0, self.cluster_size):
                    self.wait_pod(
                        f"{self.cluster_name}-{instance}",
                        "Running",
                        ns=cluster_ns,
                    )

                if self.routers_count:
                    self.wait_routers(
                        f"{self.cluster_name}-router.*",
                        self.routers_count,
                        timeout=self.cluster_size * 120,
                        wait=10,
                        ns=cluster_ns,
                    )

                self.wait_ic(
                    self.cluster_name,
                    "ONLINE",
                    num_online=self.cluster_size,
                    ns=cluster_ns,
                )

                probe_time = kutil.get_ic(cluster_ns, self.cluster_name)["status"][
                    "cluster"
                ].get("lastProbeTime")
                self.assertIsNotNone(probe_time)

                for restart_iteration in range(self.restart_repetitions):
                    previous_operator_pod = self._get_single_operator_pod(
                        namespace=operator_ns,
                        deployment_name=operator_name,
                    )
                    patch_payload = {
                        "spec": {
                            "template": {
                                "metadata": {
                                    "annotations": {
                                        "kubectl.kubernetes.io/restartedAt": (
                                            datetime.now(timezone.utc)
                                            .replace(microsecond=0)
                                            .isoformat()
                                            .replace("+00:00", "Z")
                                        )
                                    }
                                }
                            }
                        }
                    }
                    kutil.patch_dp(operator_ns, operator_name, patch_payload)
                    self._wait_for_operator_pod_rollout(
                        namespace=operator_ns,
                        deployment_name=operator_name,
                        previous_pod_name=previous_operator_pod["metadata"]["name"],
                    )

                    self.logger.info(
                        f"Waiting {self.post_restart_reconcile_wait}s after restart "
                        f"{restart_iteration + 1}/{self.restart_repetitions} to span "
                        f"the group monitor loop timeout"
                    )
                    time.sleep(self.post_restart_reconcile_wait)

                    self.wait_ic(
                        self.cluster_name,
                        "ONLINE",
                        num_online=self.cluster_size,
                        ns=cluster_ns,
                        probe_time=probe_time,
                    )

                    ic = kutil.get_ic(cluster_ns, self.cluster_name)
                    self.assertEqual(ic["status"]["cluster"]["status"], "ONLINE")
                    self.assertGreater(
                        ic["status"]["cluster"]["lastProbeTime"],
                        probe_time,
                    )
                    probe_time = ic["status"]["cluster"]["lastProbeTime"]
            except Exception:
                print_operator_log(operator_ns, operator_name)
                raise
        except Exception as exc:
            test_error = exc
            raise
        finally:
            cleanup_error = None
            for cleanup_fn in (
                lambda: self._cleanup_cluster_namespace_if_present(
                    namespace=cluster_ns,
                    cluster_name=self.cluster_name,
                    secret_name=self.cluster_secret_name,
                ),
                lambda: self._cleanup_operator_if_present(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                ),
                lambda: self._restore_default_operator(original_artifacts),
            ):
                try:
                    cleanup_fn()
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

            if cleanup_error is not None and test_error is None:
                raise cleanup_error


class StandaloneExplicitRestartEventsTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1
    post_restart_reconcile_wait = 30

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_restart_emits_operator_restarted_event_for_all_watched_clusters(self) -> None:
        operator_ns = f"op-stand-restart-evt-{self.random_suffix}"
        operator_name = f"myop-stand-restart-evt-{self.random_suffix}"
        release_name = f"myoper-stand-restart-evt-{self.random_suffix}"
        watched_namespaces_list = [
            f"db-stand-restart-a-{self.random_suffix}",
            f"db-stand-restart-b-{self.random_suffix}",
            f"db-stand-restart-c-{self.random_suffix}",
        ]
        watched_namespaces = ",".join(watched_namespaces_list)

        artifacts = self.remove_operator()

        for ns in watched_namespaces_list:
            kutil.create_ns(ns, labels={})
            kutil.create_user_secrets(ns, self.cluster_secret_name, "root", "%", "sakila")

        patched = get_patched_artifacts(
            artifacts,
            release_name,
            operator_ns,
            operator_name,
            watch_namespaces=watched_namespaces,
            standalone=True,
        )
        install_operator(patched, operator_ns)
        kutil.wait_deploy(operator_ns, operator_name, timeout=300)

        clusters = [
            (ns, f"icsc-{index}-{self.random_suffix}")
            for index, ns in enumerate(watched_namespaces_list)
        ]

        for index, (ns, cluster_name) in enumerate(clusters, start=1):
            base_server_id = index * 1000
            yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
  namespace: {ns}
spec:
  instances: {self.cluster_size}
  baseServerId: {base_server_id}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
            kutil.apply(ns, yaml)
            if index < len(clusters):
                time.sleep(CLUSTERSET_CREATION_GAP_SECONDS)

        try:
            for ns, cluster_name in clusters:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)
                if self.routers_count:
                    self.wait_routers(f"{cluster_name}-router.*", self.routers_count, timeout=self.cluster_size*120, wait=10, ns=ns)
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)

            restart_after = datetime.now(timezone.utc).replace(microsecond=0)
            waiter = tutil.get_deploy_rollover_update_waiter(self, operator_ns, deploy_name=operator_name, timeout=300, delay=5)
            patch_payload = {
                "spec": {
                    "template": {
                        "metadata": {
                            "annotations": {
                                "kubectl.kubernetes.io/restartedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                            }
                        }
                    }
                }
            }
            kutil.patch_dp(operator_ns, operator_name, patch_payload)
            waiter()
            time.sleep(self.post_restart_reconcile_wait)

            for ns, cluster_name in clusters:
                events = kutil.get_ic_ev(ns, cluster_name, after=restart_after, fields=["message", "reason", "type", "action"])
                matching_events = [
                    event for event in events
                    if event.get("reason") == "OperatorRestarted"
                    and event.get("action") == "HandlingOperatorRestart"
                    and "Handling operator restarted" in event.get("message", "")
                ]
                self.assertTrue(
                    matching_events,
                    f"Expected OperatorRestarted event for {ns}/{cluster_name}, got {events}",
                )
        except Exception:
            print_operator_log(operator_ns, operator_name)
            raise

        try:
            for ns, cluster_name in clusters:
                # Trigger deletion asynchronously; the explicit waiters below own the
                # teardown timing and are less prone to kubectl delete timeouts.
                kutil.delete_ic(ns, cluster_name, timeout=60, wait=False)
                self.wait_pods_gone(f"{cluster_name}.*", ns=ns)
                self.wait_routers_gone(f"{cluster_name}-router.*", ns=ns)
                self.wait_ic_gone(cluster_name, ns=ns)
                kutil.delete_pvc(ns, None)
                kutil.delete_secret(ns, self.cluster_secret_name)
                kutil.delete_ns(ns)
        except Exception:
            print_operator_log_tail(operator_ns, operator_name)
            raise

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
            crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
            ckopf_peerings_pattern=get_ckopf_peerings_pattern(operator_ns, operator_name),
            sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
            role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
            role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
            kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
            return_manifests=False
        )


class _StandaloneExplicitShellCrashScenario:
    use_helm = False
    watched_clusters_count = 1
    cluster_size = 1
    routers_count = 0
    post_restart_reconcile_wait = 30

    def _get_shell_crash_scenario_identity(
        self,
        *,
        restart_operator: bool,
    ) -> tuple[str, str, str, list[str]]:
        if self.use_helm:
            # Keep Helm names short enough for the chart-derived
            # app.kubernetes.io/instance label value.
            scenario_tag = "shcr" if restart_operator else "shnr"
            operator_ns = f"op-stand-{scenario_tag}-helm-{self.random_suffix}"
            operator_name = f"myop-stand-{scenario_tag}-helm-{self.random_suffix}"
            release_name = f"myoper-helm-stand-{scenario_tag}-{self.random_suffix}"
            watched_namespace_prefix = f"db-stand-{scenario_tag}-helm"
        else:
            scenario_tag = "shell-crash" if restart_operator else "shell-nr"
            operator_ns = f"op-stand-{scenario_tag}-{self.random_suffix}"
            operator_name = f"myop-stand-{scenario_tag}-{self.random_suffix}"
            release_name = f"myoper-stand-{scenario_tag}-{self.random_suffix}"
            watched_namespace_prefix = f"db-stand-{scenario_tag}"

        watched_namespaces_list = [
            f"{watched_namespace_prefix}-{index}-{self.random_suffix}"
            for index in range(self.watched_clusters_count)
        ]

        return operator_ns, operator_name, release_name, watched_namespaces_list

    def _install_shell_crash_operator(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
        watched_namespaces_list: list[str],
        source_operator_deployment: Optional[dict],
        original_artifacts: dict,
        helm_options=None,
    ):
        if self.use_helm:
            if helm_options is None:
                helm_options = self._build_shell_crash_helm_options(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                    release_name=release_name,
                    watched_namespaces_list=watched_namespaces_list,
                    source_operator_deployment=source_operator_deployment,
                )

            install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                helm_options.namespace,
                helm_options.deployment_name,
                timeout=300,
            )
            return helm_options

        patched = get_patched_artifacts(
            original_artifacts,
            release_name,
            operator_ns,
            operator_name,
            watch_namespaces=",".join(watched_namespaces_list),
            standalone=True,
        )
        install_operator(patched, operator_ns)
        kutil.wait_deploy(operator_ns, operator_name, timeout=300)
        return None

    def _build_shell_crash_helm_options(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        release_name: str,
        watched_namespaces_list: list[str],
        source_operator_deployment: dict,
    ):
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.namespaces, deployment.standalone
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = watched_namespaces_list
        helm_options.operator_values["deployment"]["standalone"] = True
        return helm_options

    def _remove_shell_crash_operator(
        self,
        *,
        operator_ns: str,
        operator_name: str,
        helm_options,
    ) -> None:
        if self.use_helm:
            uninstall_with_helm(
                release_key=get_helm_release_target(helm_options),
                delete_namespace=True,
            )
            return

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
            crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
            ckopf_peerings_pattern=get_ckopf_peerings_pattern(operator_ns, operator_name),
            sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
            role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
            role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
            kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
            return_manifests=False,
        )

    def _restore_original_operator(self, original_artifacts: dict) -> None:
        restore_operator(original_artifacts, self.operator_ns)
        kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)

    def _run_shell_crash_scenario(self, *, restart_operator: bool) -> None:
        operator_ns, operator_name, release_name, watched_namespaces_list = (
            self._get_shell_crash_scenario_identity(
                restart_operator=restart_operator,
            )
        )
        source_operator_deployment = None
        if self.use_helm:
            source_operator_deployment = kutil.get_deploy(
                self.operator_ns,
                self.operator_deploy_name,
            )

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        helm_options = None
        cluster_helm_installs = []
        test_error = None
        cleanup_error = None
        active_operator_ns = operator_ns
        active_operator_name = operator_name

        clusters = [
            (ns, f"icsc-{index}-{self.random_suffix}")
            for index, ns in enumerate(watched_namespaces_list)
        ]

        try:
            for ns in watched_namespaces_list:
                kutil.create_ns(ns, labels={})
                if not self.use_helm:
                    kutil.create_user_secrets(
                        ns,
                        self.cluster_secret_name,
                        "root",
                        "%",
                        "sakila",
                    )

            if self.use_helm:
                helm_options = self._build_shell_crash_helm_options(
                    operator_ns=operator_ns,
                    operator_name=operator_name,
                    release_name=release_name,
                    watched_namespaces_list=watched_namespaces_list,
                    source_operator_deployment=source_operator_deployment,
                )

            helm_options = self._install_shell_crash_operator(
                operator_ns=operator_ns,
                operator_name=operator_name,
                release_name=release_name,
                watched_namespaces_list=watched_namespaces_list,
                source_operator_deployment=source_operator_deployment,
                original_artifacts=original_artifacts,
                helm_options=helm_options,
            )
            if helm_options is not None:
                active_operator_ns = helm_options.namespace
                active_operator_name = helm_options.deployment_name

            for index, (ns, cluster_name) in enumerate(clusters, start=1):
                if self.use_helm:
                    cluster_install = self._build_helm_cluster_tracker(
                        ns,
                        cluster_name,
                    )
                    cluster_helm_installs.append(cluster_install)
                    self._install_helm_cluster(cluster_install)
                else:
                    yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
  namespace: {ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
                    kutil.apply(ns, yaml)
                if index < len(clusters):
                    time.sleep(CLUSTERSET_CREATION_GAP_SECONDS)

            for ns, cluster_name in clusters:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)
                if self.routers_count:
                    self.wait_routers(f"{cluster_name}-router.*", self.routers_count, timeout=self.cluster_size*120, wait=10, ns=ns)
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)

            if restart_operator:
                waiter = tutil.get_deploy_rollover_update_waiter(
                    self,
                    active_operator_ns,
                    deploy_name=active_operator_name,
                    timeout=300,
                    delay=5,
                )
                patch_payload = {
                    "spec": {
                        "template": {
                            "metadata": {
                                "annotations": {
                                    "kubectl.kubernetes.io/restartedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                                }
                            }
                        }
                    }
                }
                kutil.patch_dp(active_operator_ns, active_operator_name, patch_payload)
                waiter()
                time.sleep(self.post_restart_reconcile_wait)
        except Exception as exc:
            test_error = exc
            try:
                print_operator_log(active_operator_ns, active_operator_name)
            except Exception:
                pass
            raise
        finally:
            try:
                if self.use_helm:
                    for cluster_install in reversed(cluster_helm_installs):
                        try:
                            self._cleanup_helm_cluster(cluster_install)
                        except Exception as exc:
                            if cleanup_error is None:
                                cleanup_error = exc
                else:
                    for ns, cluster_name in clusters:
                        if kutil.get(ns, "ic", cluster_name, check=False):
                            kutil.delete_ic(ns, cluster_name, timeout=60, wait=False)
                            self.wait_pods_gone(f"{cluster_name}.*", ns=ns)
                            if self.routers_count:
                                self.wait_routers_gone(f"{cluster_name}-router.*", ns=ns)
                            self.wait_ic_gone(cluster_name, ns=ns)
                        if ns in kutil.ls_ns_ex():
                            kutil.delete_pvc(ns, None)
                            if kutil.get_secret(ns, self.cluster_secret_name, check=False):
                                kutil.delete_secret(ns, self.cluster_secret_name)
                            kutil.delete_ns(ns)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc
                try:
                    print_operator_log_tail(active_operator_ns, active_operator_name)
                except Exception:
                    pass
            finally:
                try:
                    if helm_options is not None or not self.use_helm:
                        self._remove_shell_crash_operator(
                            operator_ns=operator_ns,
                            operator_name=operator_name,
                            helm_options=helm_options,
                        )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if self.use_helm:
                    try:
                        for ns in reversed(watched_namespaces_list):
                            if ns in kutil.ls_ns_ex():
                                kutil.delete_ns(ns)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                try:
                    self._restore_original_operator(original_artifacts)
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class StandaloneExplicitRestartShellCrashTest(_StandaloneExplicitShellCrashScenario, OperatorSingleAndMultipleBaseTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_shell_crash_after_operator_restart_and_cluster_teardown(self) -> None:
        self._run_shell_crash_scenario(restart_operator=True)


class StandaloneExplicitShellCrashNoRestartTest(_StandaloneExplicitShellCrashScenario, OperatorSingleAndMultipleBaseTest):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_shell_crash_without_operator_restart_during_cluster_teardown(self) -> None:
        self._run_shell_crash_scenario(restart_operator=False)


class HelmStandaloneExplicitRestartShellCrashTest(_StandaloneExplicitShellCrashScenario, OperatorSingleAndMultipleBaseTest):
    use_helm = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_shell_crash_after_operator_restart_and_cluster_teardown(self) -> None:
        self._run_shell_crash_scenario(restart_operator=True)


class HelmStandaloneExplicitShellCrashNoRestartTest(_StandaloneExplicitShellCrashScenario, OperatorSingleAndMultipleBaseTest):
    use_helm = True

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_shell_crash_without_operator_restart_during_cluster_teardown(self) -> None:
        self._run_shell_crash_scenario(restart_operator=False)


class MultiNamespaceSingleOperatorTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multi_namespace(self) -> None:
        operator_ns = f"op-multins-{self.random_suffix}"
        cluster_watched1_ns = f"db-multins-watched1-{self.random_suffix}"
        cluster_watched2_ns = f"db-multins-watched2-{self.random_suffix}"
        cluster_nonwatched_ns = f"db-multins-unwatched-{self.random_suffix}"
        operator_name = f"myop-multins-{self.random_suffix}"
        release_name = f"myoper-multins-{self.random_suffix}"

        kutil.create_ns(cluster_watched1_ns, labels={})
        kutil.create_ns(cluster_watched2_ns, labels={})

        watched_ns_str = f"{cluster_watched1_ns},{cluster_watched2_ns}"

        artifacts = self.remove_operator()

        patched = get_patched_artifacts(artifacts, release_name, operator_ns, operator_name, watch_namespaces=watched_ns_str)
        install_operator(patched, operator_ns)
        kutil.wait_deploy(operator_ns, operator_name, timeout=300)

        kutil.create_ns(cluster_nonwatched_ns, labels={})

        namespaces_and_clusters = [
            (cluster_watched1_ns, f"ic-watched1-{self.cluster_name}"),
            (cluster_watched2_ns, f"ic-watched2-{self.cluster_name}"),
            (cluster_nonwatched_ns, f"ic-unwatched-{self.cluster_name}")
        ]

        for ns, cluster_name in namespaces_and_clusters:
            kutil.create_user_secrets(ns, self.cluster_secret_name, "root", "%", "sakila")
            yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
  namespace: {ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
            kutil.apply(ns, yaml)

        try:
            for ns, cluster_name in [(cluster_watched1_ns, f"ic-watched1-{self.cluster_name}"), (cluster_watched2_ns, f"ic-watched2-{self.cluster_name}")]:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)
                if self.routers_count:
                    self.wait_routers(f"{cluster_name}-router.*", self.routers_count, timeout=self.cluster_size*120, wait=10, ns=ns)
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)
        except Exception as exc:
            print_operator_log(operator_ns, operator_name)
            raise

        nonwatched_sts = kutil.ls_sts(cluster_nonwatched_ns, pattern=".*")
        self.assertEqual(len(nonwatched_sts), 0)

        # Cleanup
        for ns, cluster_name in namespaces_and_clusters:
            kutil.delete_ic(ns, cluster_name)
            self.wait_pods_gone(f"{cluster_name}.*", ns=ns)
            self.wait_routers_gone(f"{cluster_name}-router.*", ns=ns)
            self.wait_ic_gone(cluster_name, ns=ns)
            kutil.delete_pvc(ns, None)
            kutil.delete_secret(ns, self.cluster_secret_name)
            kutil.delete_ns(ns)

        remove_operator(
            operator_ns=operator_ns,
            operator_deploy_name=operator_name,
            crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
            crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
            ckopf_peerings_pattern="", # Not generated when watch_namespaces is present
            sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
            role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
            role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
            kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
            return_manifests=False
        )


class HelmMultiNamespaceSingleOperatorTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multi_namespace(self) -> None:
        operator_ns = f"op-multins-{self.random_suffix}"
        cluster_watched1_ns = f"db-multins-watched1-{self.random_suffix}"
        cluster_watched2_ns = f"db-multins-watched2-{self.random_suffix}"
        cluster_nonwatched_ns = f"db-multins-unwatched-{self.random_suffix}"
        operator_name = f"myop-multins-{self.random_suffix}"
        release_name = f"myoper-helm-multins-{self.random_suffix}"
        watched_namespaces = [cluster_watched1_ns, cluster_watched2_ns]
        watched_ns_str = ",".join(watched_namespaces)

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        helm_options = resolve_install_with_helm_options(
            namespace=operator_ns,
            source_operator_namespace=self.operator_ns,
            source_operator_deployment_name=self.operator_deploy_name,
            source_operator_deployment=source_operator_deployment,
        )
        helm_options.release_name = release_name
        # See helm/mysql-operator/values.yaml for the deployment values layout.
        # Keep these keys in sync if the chart changes:
        # deployment.name, deployment.namespaces, deployment.standalone
        helm_options.operator_values["deployment"]["name"] = operator_name
        helm_options.operator_values["deployment"]["namespaces"] = watched_namespaces
        helm_options.operator_values["deployment"]["standalone"] = False

        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        install_result = None
        namespaces_to_clean = []
        namespaces_and_clusters = []
        cluster_helm_installs = []
        test_error = None

        try:
            for namespace in watched_namespaces:
                kutil.create_ns(namespace, labels={})
                namespaces_to_clean.append(namespace)

            install_result = install_with_helm(
                operator_ns,
                resolved_options=helm_options,
            )
            kutil.wait_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
                timeout=300,
            )

            operator_deployment = kutil.get_deploy(
                install_result.options.namespace,
                install_result.options.deployment_name,
            )
            envs = get_operator_envs_from_deployment(operator_deployment)
            self.assertEqual(envs["OPERATOR_STANDALONE"], "false")
            self.assertEqual(envs["OPERATOR_NAMESPACES"], watched_ns_str)

            kutil.create_ns(cluster_nonwatched_ns, labels={})
            namespaces_to_clean.append(cluster_nonwatched_ns)

            namespaces_and_clusters = [
                (cluster_watched1_ns, f"ic-watched1-{self.cluster_name}"),
                (cluster_watched2_ns, f"ic-watched2-{self.cluster_name}"),
                (cluster_nonwatched_ns, f"ic-unwatched-{self.cluster_name}"),
            ]

            for ns, cluster_name in namespaces_and_clusters:
                cluster_install = self._build_helm_cluster_tracker(
                    ns,
                    cluster_name,
                )
                cluster_helm_installs.append(cluster_install)
                self._install_helm_cluster(cluster_install)

            for ns, cluster_name in [
                (cluster_watched1_ns, f"ic-watched1-{self.cluster_name}"),
                (cluster_watched2_ns, f"ic-watched2-{self.cluster_name}"),
            ]:
                self._wait_for_helm_cluster_ready(
                    namespace=ns,
                    cluster_name=cluster_name,
                    server_instances=self.cluster_size,
                    router_instances=self.routers_count,
                )

            nonwatched_sts = kutil.ls_sts(cluster_nonwatched_ns, pattern=".*")
            self.assertEqual(len(nonwatched_sts), 0)
        except Exception as exc:
            test_error = exc
            active_options = install_result.options if install_result else helm_options
            try:
                print_operator_log(
                    active_options.namespace,
                    active_options.deployment_name,
                )
            except Exception:
                pass
            raise
        finally:
            cleanup_error = None
            try:
                for cluster_install in reversed(cluster_helm_installs):
                    try:
                        self._cleanup_helm_cluster(cluster_install)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                try:
                    uninstall_with_helm(
                        release_key=(
                            install_result.release_key
                            if install_result is not None
                            else get_helm_release_target(helm_options)
                        ),
                        delete_namespace=True,
                    )
                except Exception as exc:
                    if cleanup_error is None:
                        cleanup_error = exc

                for namespace in reversed(namespaces_to_clean):
                    try:
                        if namespace in kutil.ls_ns_ex():
                            kutil.delete_ns(namespace)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error


class MultipleOperatorsParallelTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_operators_parallel(self) -> None:
        namespaces_to_clean = []
        all_ics = []
        operators_to_clean = []
        watched_ics = []
        nonwatched_ics = []
        operator_count = 2
        clusters_per_operator = 2

        artifacts = self.remove_operator()

        for operator_nr in range(1, operator_count + 1):
            operator_ns = f"op-par{operator_nr}-{self.random_suffix}"
            op_watched_ns = []
            for cluster_nr in range (1, clusters_per_operator + 1):
                db_ns = f"db-par-op{operator_nr}-watched{cluster_nr}-{self.random_suffix}"
                op_watched_ns.append(db_ns)
                watched_ics.extend([(db_ns, f"ic-op{operator_nr}-watched{cluster_nr}")])

            nonwatched_ns = f"uw-paral-op{operator_nr}-{self.random_suffix}"
            operator_name = f"myop-paral-op{operator_nr}-{self.random_suffix}"
            release_name = f"myoper-paral-op{operator_nr}-{self.random_suffix}"

            operators_to_clean.append((operator_ns, operator_name))

            for db_ns in op_watched_ns + [nonwatched_ns]:
                kutil.create_ns(db_ns, labels={})
                namespaces_to_clean.extend([db_ns])

            watched_ns_str = ",".join(op_watched_ns)
            patched = get_patched_artifacts(artifacts, release_name, operator_ns, operator_name, watch_namespaces=watched_ns_str)
            install_operator(patched, operator_ns)
            kutil.wait_deploy(operator_ns, operator_name, timeout=300)

            nonwatched_ics.append((nonwatched_ns, f"ic-op{operator_nr}-unwatched"))

        print(watched_ics)
        print(nonwatched_ics)
        all_ics = watched_ics + nonwatched_ics

        for ns, cluster_name in all_ics:
            kutil.create_user_secrets(ns, self.cluster_secret_name, "root", "%", "sakila")
            yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
  namespace: {ns}
spec:
  instances: {self.cluster_size}
  router:
      instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
            print(yaml)
            kutil.apply(ns, yaml)

        try:
            for ns, cluster_name in watched_ics:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)

                if self.routers_count:
                    self.wait_routers(f"{cluster_name}-router.*", self.routers_count, timeout=self.cluster_size*120, wait=10, ns=ns)
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)
        except Exception as exc:
            for operator_ns, operator_name in operators_to_clean:
                print_operator_log(operator_ns, operator_name)
            raise

        for nonwatched_ns, _ in nonwatched_ics:
            nonwatched_sts = kutil.ls_sts(nonwatched_ns, pattern=".*")
            self.assertEqual(len(nonwatched_sts), 0)

        # Cleanup
        for ns, cluster_name in all_ics:
            print(f"{ns=} {cluster_name=}")
            kutil.delete_ic(ns, cluster_name)
            self.wait_pods_gone(f"{cluster_name}.*", ns=ns)
            self.wait_routers_gone(f"{cluster_name}-router.*", ns=ns)
            self.wait_ic_gone(cluster_name, ns=ns)
            kutil.delete_pvc(ns, None)
            kutil.delete_secret(ns, self.cluster_secret_name)

        for ns in reversed(namespaces_to_clean):
            print(f"Removing {ns} namespace")
            kutil.delete_ns(ns)

        for operator_ns, operator_name in reversed(operators_to_clean):
            remove_operator(
                operator_ns=operator_ns,
                operator_deploy_name=operator_name,
                crole_names_pattern=get_crole_names_pattern(operator_ns, operator_name),
                crole_binding_pattern=get_crole_binding_pattern(operator_ns, operator_name),
                ckopf_peerings_pattern="", # Not generated when watch_namespaces is present
                sa_names_pattern=get_sa_names_pattern(operator_ns, operator_name),
                role_names_pattern=get_role_names_pattern(operator_ns, operator_name),
                role_binding_pattern=get_role_binding_pattern(operator_ns, operator_name),
                kopf_peerings_pattern=get_kopf_peerings_pattern(operator_name),
                return_manifests=False
            )


class HelmMultipleOperatorsParallelTest(OperatorSingleAndMultipleBaseTest):
    cluster_size = 3
    routers_count = 1

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        kutil.delete_ns(cls.ns)
        super().tearDownClass()

    def test_multiple_operators_parallel(self) -> None:
        namespaces_to_clean = []
        all_ics = []
        operators_to_clean = []
        cluster_helm_installs = []
        watched_ics = []
        nonwatched_ics = []
        operator_count = 2
        clusters_per_operator = 2
        test_error = None

        source_operator_deployment = kutil.get_deploy(
            self.operator_ns,
            self.operator_deploy_name,
        )
        original_artifacts = self.remove_operator()
        self.assertIsNotNone(original_artifacts)

        try:
            for operator_nr in range(1, operator_count + 1):
                operator_ns = f"op-par{operator_nr}-helm-{self.random_suffix}"
                op_watched_ns = []
                for cluster_nr in range(1, clusters_per_operator + 1):
                    db_ns = (
                        f"db-par-op{operator_nr}-watched{cluster_nr}-helm-"
                        f"{self.random_suffix}"
                    )
                    op_watched_ns.append(db_ns)
                    watched_ics.append(
                        (db_ns, f"ic-op{operator_nr}-watched{cluster_nr}")
                    )

                nonwatched_ns = f"uw-paral-op{operator_nr}-helm-{self.random_suffix}"
                operator_name = f"myop-paral-op{operator_nr}-helm-{self.random_suffix}"
                release_name = f"myoper-helm-paral-op{operator_nr}-{self.random_suffix}"

                for db_ns in op_watched_ns + [nonwatched_ns]:
                    kutil.create_ns(db_ns, labels={})
                    namespaces_to_clean.append(db_ns)

                watched_ns_str = ",".join(op_watched_ns)
                helm_options = resolve_install_with_helm_options(
                    namespace=operator_ns,
                    source_operator_namespace=self.operator_ns,
                    source_operator_deployment_name=self.operator_deploy_name,
                    source_operator_deployment=source_operator_deployment,
                )
                helm_options.release_name = release_name
                # See helm/mysql-operator/values.yaml for the deployment values layout.
                # Keep these keys in sync if the chart changes:
                # deployment.name, deployment.namespaces, deployment.standalone
                helm_options.operator_values["deployment"]["name"] = operator_name
                helm_options.operator_values["deployment"]["namespaces"] = op_watched_ns
                helm_options.operator_values["deployment"]["standalone"] = False
                operators_to_clean.append(helm_options)

                install_result = install_with_helm(
                    operator_ns,
                    resolved_options=helm_options,
                )
                kutil.wait_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                    timeout=300,
                )

                operator_deployment = kutil.get_deploy(
                    install_result.options.namespace,
                    install_result.options.deployment_name,
                )
                envs = get_operator_envs_from_deployment(operator_deployment)
                self.assertEqual(envs["OPERATOR_STANDALONE"], "false")
                self.assertEqual(envs["OPERATOR_NAMESPACES"], watched_ns_str)

                nonwatched_ics.append((nonwatched_ns, f"ic-op{operator_nr}-unwatched"))

            print(watched_ics)
            print(nonwatched_ics)
            all_ics = watched_ics + nonwatched_ics

            for ns, cluster_name in all_ics:
                cluster_install = self._build_helm_cluster_tracker(
                    ns,
                    cluster_name,
                )
                cluster_helm_installs.append(cluster_install)
                self._install_helm_cluster(cluster_install)

            for ns, cluster_name in watched_ics:
                self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], ns=ns)
                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{cluster_name}-{instance}", "Running", ns=ns)

                if self.routers_count:
                    self.wait_routers(
                        f"{cluster_name}-router.*",
                        self.routers_count,
                        timeout=self.cluster_size * 120,
                        wait=10,
                        ns=ns,
                    )
                self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size, ns=ns)

            for nonwatched_ns, _ in nonwatched_ics:
                nonwatched_sts = kutil.ls_sts(nonwatched_ns, pattern=".*")
                self.assertEqual(len(nonwatched_sts), 0)
        except Exception as exc:
            test_error = exc
            for options in operators_to_clean:
                try:
                    print_operator_log(options.namespace, options.deployment_name)
                except Exception:
                    pass
            raise
        finally:
            cleanup_error = None
            try:
                for cluster_install in reversed(cluster_helm_installs):
                    try:
                        self._cleanup_helm_cluster(cluster_install)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                for options in reversed(operators_to_clean):
                    try:
                        uninstall_with_helm(
                            release_key=get_helm_release_target(options),
                            delete_namespace=True,
                        )
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc

                for ns in reversed(namespaces_to_clean):
                    try:
                        if ns in kutil.ls_ns_ex():
                            print(f"Removing {ns} namespace")
                            kutil.delete_ns(ns)
                    except Exception as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            finally:
                restore_operator(original_artifacts, self.operator_ns)
                kutil.wait_deploy(self.operator_ns, self.operator_deploy_name, timeout=300)
                if cleanup_error is not None and test_error is None:
                    raise cleanup_error
