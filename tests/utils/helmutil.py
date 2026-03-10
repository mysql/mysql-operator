# Copyright (c) 2020, 2026 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import copy
import logging
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from typing import Optional, Union

import yaml

from setup.config import g_ts_cfg
from utils import kutil


logger = logging.getLogger(__name__)
REQUIRED_HELM_TEST_CHARTS = (
    "mysql-operator",
    "mysql-innodbcluster",
)


def run_command(
    argv: list[str],
    *,
    check: bool = True,
    cwd: Optional[str] = None,
) -> subprocess.CompletedProcess:
    logger.info("Running command: %s", " ".join(argv))
    try:
        result = subprocess.run(
            argv,
            check=check,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=None,
        )
    except subprocess.CalledProcessError as exc:
        if exc.stdout:
            logger.error("%s", exc.stdout.rstrip())
        raise
    if result.stdout:
        logger.info("%s", result.stdout.rstrip())
    return result


def helm_binary_available() -> bool:
    return shutil.which("helm") is not None


def _get_helm_source_path(source_path: Optional[str] = None) -> str:
    helm_path = source_path or g_ts_cfg.get_helm_path()
    if not helm_path:
        raise ValueError(
            "Helm chart path is not configured. Set OPERATOR_TEST_HELM_PATH "
            "so the test container can see the mounted community charts."
        )
    if not os.path.isdir(helm_path):
        raise FileNotFoundError(f"Helm chart path does not exist: {helm_path}")
    return helm_path


def _get_chart_yaml_path(chart_dir: str) -> str:
    return os.path.join(chart_dir, "Chart.yaml")


def _is_chart_directory(path: str) -> bool:
    return os.path.isdir(path) and os.path.isfile(_get_chart_yaml_path(path))


def _load_chart_metadata(chart_dir: str) -> dict:
    chart_yaml_path = _get_chart_yaml_path(chart_dir)
    if not os.path.isfile(chart_yaml_path):
        raise FileNotFoundError(f"Helm chart does not exist: {chart_dir}")

    with open(chart_yaml_path, "r", encoding="utf8") as chart_yaml:
        metadata = yaml.safe_load(chart_yaml) or {}
    if not isinstance(metadata, dict):
        raise ValueError(f"Helm chart metadata must be a mapping: {chart_yaml_path}")
    return metadata


def _get_chart_name(chart_dir: str) -> str:
    chart_name = _load_chart_metadata(chart_dir).get("name")
    if not isinstance(chart_name, str) or not chart_name:
        raise ValueError(f"Helm chart is missing a valid name: {_get_chart_yaml_path(chart_dir)}")
    return chart_name


def _find_broken_symlink(chart_dir: str) -> Optional[str]:
    visited_real_dirs: set[str] = set()

    def _scan_dir(current_dir: str) -> Optional[str]:
        real_dir = os.path.realpath(current_dir)
        if real_dir in visited_real_dirs:
            return None
        visited_real_dirs.add(real_dir)

        try:
            with os.scandir(current_dir) as entries:
                for entry in entries:
                    entry_path = entry.path
                    if entry.is_symlink():
                        if not os.path.exists(entry_path):
                            return f"{entry_path} -> {os.readlink(entry_path)}"
                        if entry.is_dir(follow_symlinks=True):
                            broken = _scan_dir(entry_path)
                            if broken is not None:
                                return broken
                        continue

                    if entry.is_dir(follow_symlinks=False):
                        broken = _scan_dir(entry_path)
                        if broken is not None:
                            return broken
        except OSError as exc:
            return f"{current_dir}: {exc}"

        return None

    return _scan_dir(chart_dir)


def describe_helm_source_tree(
    source_path: Optional[str] = None,
) -> tuple[str, str]:
    helm_path = _get_helm_source_path(source_path)
    tree_lines = ["."]

    def _append_tree_lines(current_dir: str, prefix: str) -> None:
        with os.scandir(current_dir) as entries:
            sorted_entries = sorted(entries, key=lambda entry: entry.name)

        for index, entry in enumerate(sorted_entries):
            is_last = index == len(sorted_entries) - 1
            branch = "`- " if is_last else "|- "
            child_prefix = prefix + ("   " if is_last else "|  ")
            entry_path = entry.path

            if entry.is_symlink():
                link_target = os.readlink(entry_path)
                suffix = " [broken]" if not os.path.exists(entry_path) else ""
                tree_lines.append(f"{prefix}{branch}{entry.name} -> {link_target}{suffix}")
                continue

            if entry.is_dir(follow_symlinks=False):
                tree_lines.append(f"{prefix}{branch}{entry.name}/")
                _append_tree_lines(entry_path, child_prefix)
                continue

            tree_lines.append(f"{prefix}{branch}{entry.name}")

    _append_tree_lines(helm_path, "")
    return helm_path, "\n".join(tree_lines)


def _get_chart_release_metadata(
    chart_dir: str,
) -> tuple[Optional[str], Optional[str], list[str]]:
    metadata = _load_chart_metadata(chart_dir)

    raw_app_version = metadata.get("appVersion")
    app_version = None if raw_app_version in (None, "") else str(raw_app_version)

    raw_chart_version = metadata.get("version")
    chart_version = None if raw_chart_version in (None, "") else str(raw_chart_version)

    release_candidates = []
    if app_version is not None:
        release_candidates.append(app_version)
        if chart_version is not None and not app_version.endswith(f"-{chart_version}"):
            release_candidates.append(f"{app_version}-{chart_version}")

    return app_version, chart_version, release_candidates


def _get_root_chart_path(source_path: str, chart_name: str) -> str:
    return os.path.join(source_path, chart_name)


def _get_versioned_chart_path(
    source_path: str,
    chart_name: str,
    app_version: str,
) -> str:
    return os.path.join(source_path, app_version, chart_name)


def _parse_numeric_version(
    version_text: str,
    *,
    description: str,
) -> tuple[int, ...]:
    parts = str(version_text).split(".")
    if not parts or any(not part.isdigit() for part in parts):
        raise ValueError(f"{description} must be a dot-separated numeric version: {version_text}")
    return tuple(int(part) for part in parts)


def _split_operator_chart_release(
    release_name: str,
) -> tuple[str, str]:
    mysql_version, separator, chart_version = str(release_name).partition("-")
    if not separator or not mysql_version or not chart_version:
        raise ValueError(
            "operator chart release must use <mysql-version>-<chart-version> "
            f"format: {release_name}"
        )
    return mysql_version, chart_version


def _parse_operator_chart_release(
    release_name: str,
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    mysql_version, chart_version = _split_operator_chart_release(release_name)
    return (
        _parse_numeric_version(
            mysql_version,
            description="operator mysql release",
        ),
        _parse_numeric_version(
            chart_version,
            description="operator chart release",
        ),
    )


def _find_chart_validation_error(
    chart_dir: str,
    chart_name: str,
    app_version: Optional[str] = None,
) -> Optional[str]:
    if not _is_chart_directory(chart_dir):
        return f"Helm chart directory does not exist: {chart_dir}"

    actual_chart_name = _get_chart_name(chart_dir)
    if actual_chart_name != chart_name:
        return (
            f"Configured Helm chart path {chart_dir} points to chart "
            f"{actual_chart_name}, expected {chart_name}"
        )

    broken_symlink = _find_broken_symlink(chart_dir)
    if broken_symlink is not None:
        return (
            f"Configured Helm chart path {chart_dir} contains a broken symlink: "
            f"{broken_symlink}"
        )

    if app_version is not None:
        actual_app_version, actual_chart_version, release_candidates = (
            _get_chart_release_metadata(chart_dir)
        )
        if app_version not in release_candidates:
            candidate_text = ", ".join(release_candidates) if release_candidates else "<none>"
            return (
                f"Configured Helm chart path {chart_dir} has appVersion "
                f"{actual_app_version} and chart version {actual_chart_version}; "
                f"expected release {app_version}. Accepted releases from metadata: "
                f"{candidate_text}"
            )

    return None


def _validate_chart_layout(
    source_path: str,
    *,
    chart_names: tuple[str, ...],
    app_version: str,
    use_root_shortcuts: bool,
) -> list[str]:
    errors = []

    for chart_name in chart_names:
        chart_path = (
            _get_root_chart_path(source_path, chart_name)
            if use_root_shortcuts
            else _get_versioned_chart_path(source_path, chart_name, app_version)
        )
        error = _find_chart_validation_error(
            chart_path,
            chart_name,
            app_version,
        )
        if error is not None:
            errors.append(error)

    return errors


def _list_versioned_chart_releases(source_path: str) -> list[str]:
    releases = []

    for entry in os.scandir(source_path):
        if not entry.is_dir(follow_symlinks=False):
            continue

        try:
            _parse_operator_chart_release(entry.name)
        except ValueError:
            continue

        releases.append(entry.name)

    releases.sort(key=_parse_operator_chart_release)
    return releases


def _validate_versioned_chart_tree(
    source_path: str,
    *,
    chart_names: tuple[str, ...],
) -> list[str]:
    errors = []

    for release_name in _list_versioned_chart_releases(source_path):
        release_errors = _validate_chart_layout(
            source_path,
            chart_names=chart_names,
            app_version=release_name,
            use_root_shortcuts=False,
        )
        errors.extend(
            f"[{release_name}] {error}"
            for error in release_errors
        )

    return errors


def validate_helm_test_environment(source_path: Optional[str] = None) -> None:
    if not helm_binary_available():
        raise RuntimeError("Helm binary is not available in PATH")

    helm_path = _get_helm_source_path(source_path)
    app_version = str(g_ts_cfg.operator_version_tag)

    broken_symlink = _find_broken_symlink(helm_path)
    if broken_symlink is not None:
        raise FileNotFoundError(
            "Mounted Helm chart tree contains a broken symlink under "
            f"{helm_path}: {broken_symlink}"
        )

    shortcut_errors = _validate_chart_layout(
        helm_path,
        chart_names=REQUIRED_HELM_TEST_CHARTS,
        app_version=app_version,
        use_root_shortcuts=True,
    )
    versioned_errors = []
    if shortcut_errors:
        versioned_errors = _validate_chart_layout(
            helm_path,
            chart_names=REQUIRED_HELM_TEST_CHARTS,
            app_version=app_version,
            use_root_shortcuts=False,
        )
    current_release_errors = None
    if shortcut_errors and versioned_errors:
        current_release_errors = "\n".join(
            [
                "Mounted Helm charts are incomplete under "
                f"{helm_path}. Expected either top-level current charts "
                f"{', '.join(REQUIRED_HELM_TEST_CHARTS)} or versioned charts under "
                f"{app_version}.",
                "Shortcut layout errors:",
                *(f"  {error}" for error in shortcut_errors),
                "Versioned layout errors:",
                *(f"  {error}" for error in versioned_errors),
            ]
        )

    historical_release_errors = _validate_versioned_chart_tree(
        helm_path,
        chart_names=REQUIRED_HELM_TEST_CHARTS,
    )

    if current_release_errors is None and not historical_release_errors:
        return

    error_parts = []
    if current_release_errors is not None:
        error_parts.append(current_release_errors)
    if historical_release_errors:
        error_parts.append(
            "\n".join(
                [
                    "Mounted Helm release directories under "
                    f"{helm_path} contain invalid chart layouts:",
                    *historical_release_errors,
                ]
            )
        )

    raise FileNotFoundError("\n\n".join(error_parts))


def get_helm_chart_path(
    chart_name: str,
    source_path: Optional[str] = None,
    app_version: Optional[str] = None,
) -> str:
    if not chart_name:
        raise ValueError("chart_name must be provided")

    source_path = _get_helm_source_path(source_path)
    app_version = str(app_version) if app_version not in (None, "") else None

    if _is_chart_directory(source_path):
        direct_chart_error = _find_chart_validation_error(
            source_path,
            chart_name,
            app_version,
        )
        if direct_chart_error is None:
            return source_path
        raise FileNotFoundError(direct_chart_error)

    root_chart_path = _get_root_chart_path(source_path, chart_name)
    if app_version is None:
        root_chart_error = _find_chart_validation_error(
            root_chart_path,
            chart_name,
        )
        if root_chart_error is None:
            return root_chart_path
        if _is_chart_directory(root_chart_path):
            raise FileNotFoundError(root_chart_error)
        raise FileNotFoundError(
            f"Helm chart {chart_name} was not found under {source_path}"
        )

    versioned_chart_path = _get_versioned_chart_path(
        source_path,
        chart_name,
        app_version,
    )

    if app_version == str(g_ts_cfg.operator_version_tag):
        root_chart_error = _find_chart_validation_error(
            root_chart_path,
            chart_name,
            app_version,
        )
        if root_chart_error is None:
            return root_chart_path

        versioned_chart_error = _find_chart_validation_error(
            versioned_chart_path,
            chart_name,
            app_version,
        )
        if versioned_chart_error is None:
            return versioned_chart_path

        raise FileNotFoundError(
            f"{root_chart_error}. Also checked {versioned_chart_path}: "
            f"{versioned_chart_error}"
        )

    versioned_chart_error = _find_chart_validation_error(
        versioned_chart_path,
        chart_name,
        app_version,
    )
    if versioned_chart_error is None:
        return versioned_chart_path
    raise FileNotFoundError(versioned_chart_error)


def get_previous_operator_chart_release(
    source_path: Optional[str] = None,
    current_app_version: Optional[str] = None,
) -> str:
    source_path = _get_helm_source_path(source_path)
    current_app_version = str(
        current_app_version
        if current_app_version not in (None, "")
        else g_ts_cfg.operator_version_tag
    )
    _, current_chart_version = _parse_operator_chart_release(current_app_version)

    best_release = None
    best_sort_key = None

    for entry in os.scandir(source_path):
        if not entry.is_dir():
            continue

        candidate_release = entry.name
        try:
            candidate_mysql_version, candidate_chart_version = (
                _parse_operator_chart_release(candidate_release)
            )
        except ValueError:
            continue

        if candidate_chart_version >= current_chart_version:
            continue

        chart_error = _find_chart_validation_error(
            _get_versioned_chart_path(
                source_path,
                "mysql-operator",
                candidate_release,
            ),
            "mysql-operator",
            candidate_release,
        )
        if chart_error is not None:
            continue

        candidate_sort_key = (
            candidate_chart_version,
            candidate_mysql_version,
        )
        if best_sort_key is None or candidate_sort_key > best_sort_key:
            best_sort_key = candidate_sort_key
            best_release = candidate_release

    if best_release is not None:
        return best_release

    raise FileNotFoundError(
        "No previous mysql-operator Helm chart release was found under "
        f"{source_path} for current release {current_app_version}"
    )


HelmManifestObject = tuple[str, str, str]
HelmReleaseKey = tuple[str, str, str]

_HELM_SECTION_HEADER = re.compile(r"^[A-Z][A-Z0-9-]*(?: [A-Z0-9-]+)*:$")
_HELM_CLUSTER_SCOPED_KINDS = {
    "ClusterKopfPeering",
    "ClusterRole",
    "ClusterRoleBinding",
    "CustomResourceDefinition",
    "Namespace",
}
_HELM_MANIFEST_SETTLE_TIMEOUT_SECONDS = 10
_HELM_RELEASE_MANIFEST_OBJECTS: dict[HelmReleaseKey, list[HelmManifestObject]] = {}


def _default_k8s_domain() -> str:
    return g_ts_cfg.k8s_cluster_domain_alias or "cluster.local"


class HelmOperatorInstallOptions:
    def __init__(
        self,
        namespace: str,
        *,
        app_version: Optional[str] = None,
        version: Optional[str] = None,
        release_name: str = "myoperator",
        kube_context: Optional[str] = None,
        helm_package: Optional[str] = None,
        helm_wait_timeout_seconds: Optional[int] = None,
        operator_values: Optional[dict] = None,
        use_chart_defaults: bool = False,
    ) -> None:
        self.namespace = namespace
        self.app_version = (
            app_version
            if app_version is not None
            else g_ts_cfg.operator_version_tag
        )
        self.version = version
        self.release_name = release_name
        self.kube_context = kube_context
        self.helm_package = (
            helm_package
            if helm_package is not None
            else "mysql-operator"
        )
        self.wait_for_helm = True
        self.helm_wait_timeout_seconds = helm_wait_timeout_seconds
        self.use_chart_defaults = use_chart_defaults
        self.explicit_operator_values = {}
        self.operator_values = self._build_default_operator_values()
        if operator_values is not None:
            self.explicit_operator_values = copy.deepcopy(operator_values)
            self.operator_values = _merge_operator_values(
                self.operator_values,
                operator_values,
            )
        self.helm_output_preamble = ""
        self.helm_output_sections: dict[str, str] = {}
        self.helm_manifest_objects: list[HelmManifestObject] = []

    def _build_default_operator_values(self) -> dict:
        return {
            "edition": "community",
            "image": {
                "registry": "",
                "repository": "",
                "name": "",
                "tag": None,
                "digest": None,
                "pullPolicy": "IfNotPresent",
            },
            "envs": {
                "imagesPullPolicy": "IfNotPresent",
                "imagesDefaultRegistry": None,
                "imagesDefaultRepository": None,
                "k8sClusterDomain": _default_k8s_domain(),
            },
            "replicas": 1,
            "deployment": {
                "name": "mysql-operator",
                "standalone": False,
                "namespaces": [],
            },
            "operatorDebug": 0,
            "namespaceLabels": {},
        }

    @classmethod
    def from_source(
        cls,
        namespace: str,
        *,
        source_operator_namespace: Optional[str] = None,
        source_operator_deployment_name: Optional[str] = None,
        source_operator_deployment: Optional[dict] = None,
    ) -> "HelmOperatorInstallOptions":
        return _build_default_install_with_helm_options(
            namespace,
            source_operator_namespace=source_operator_namespace,
            source_operator_deployment_name=source_operator_deployment_name,
            source_operator_deployment=source_operator_deployment,
        )

    @property
    def edition(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "edition"),
            default="community",
        )

    @property
    def deployment_name(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "deployment", "name"),
            default="mysql-operator",
        )

    @property
    def operator_namespaces(self) -> str:
        return ",".join(self.operator_namespaces_list)

    @property
    def operator_namespaces_list(self) -> list[str]:
        return _normalize_watch_namespace_list(
            _get_nested_value(self.operator_values, "deployment", "namespaces"),
        )

    @property
    def k8s_domain(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "envs", "k8sClusterDomain"),
            default=_default_k8s_domain(),
        )

    @property
    def operator_registry(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "image", "registry"),
            default="",
        )

    @property
    def operator_repository(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "image", "repository"),
            default="",
        )

    @property
    def operator_image_name(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "image", "name"),
            default="",
        )

    @property
    def operator_image_tag(self) -> Optional[str]:
        value = _get_nested_value(self.operator_values, "image", "tag")
        if value in (None, ""):
            return None
        return str(value)

    @property
    def operator_image_digest(self) -> Optional[str]:
        value = _get_nested_value(self.operator_values, "image", "digest")
        if value in (None, ""):
            return None
        return str(value)

    @property
    def operator_image_pull_policy(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "image", "pullPolicy"),
            default="IfNotPresent",
        )

    @property
    def managed_images_pull_policy(self) -> str:
        return _coerce_str(
            _get_nested_value(self.operator_values, "envs", "imagesPullPolicy"),
            default="IfNotPresent",
        )

    @property
    def default_registry(self) -> Optional[str]:
        value = _get_nested_value(
            self.operator_values,
            "envs",
            "imagesDefaultRegistry",
        )
        return None if value in (None, "") else str(value)

    @property
    def default_repository(self) -> Optional[str]:
        value = _get_nested_value(
            self.operator_values,
            "envs",
            "imagesDefaultRepository",
        )
        return None if value in (None, "") else str(value)

    @property
    def deployment_replicas(self) -> int:
        return _coerce_int(
            _get_nested_value(self.operator_values, "replicas"),
            default=1,
        )

    @property
    def standalone(self) -> bool:
        return _coerce_bool(
            _get_nested_value(self.operator_values, "deployment", "standalone"),
        )

    @property
    def debug_operator(self) -> int:
        return _coerce_int(
            _get_nested_value(self.operator_values, "operatorDebug"),
            default=0,
        )

    @property
    def namespace_labels(self) -> dict:
        labels = _get_nested_value(self.operator_values, "namespaceLabels")
        return labels if isinstance(labels, dict) else {}


class HelmClusterInstallOptions:
    def __init__(
        self,
        namespace: str,
        *,
        app_version: Optional[str] = None,
        version: Optional[str] = None,
        release_name: str = "mycluster",
        kube_context: Optional[str] = None,
        helm_package: Optional[str] = None,
        helm_wait_timeout_seconds: Optional[int] = None,
        cluster_values: Optional[dict] = None,
    ) -> None:
        self.namespace = namespace
        self.app_version = (
            app_version
            if app_version is not None
            else g_ts_cfg.operator_version_tag
        )
        self.version = version
        self.release_name = release_name
        self.kube_context = kube_context
        self.helm_package = (
            helm_package
            if helm_package is not None
            else "mysql-innodbcluster"
        )
        self.wait_for_helm = False
        self.helm_wait_timeout_seconds = helm_wait_timeout_seconds
        self.cluster_values = {}
        if cluster_values is not None:
            self.cluster_values = _merge_operator_values(
                self.cluster_values,
                cluster_values,
            )
        self.helm_output_preamble = ""
        self.helm_output_sections: dict[str, str] = {}
        self.helm_manifest_objects: list[HelmManifestObject] = []

    @property
    def namespace_labels(self) -> dict:
        return {}


HelmInstallOptions = Union[HelmOperatorInstallOptions, HelmClusterInstallOptions]


@dataclass(frozen=True)
class HelmOperatorInstallResult:
    options: HelmInstallOptions
    release_key: HelmReleaseKey = ("", "", "")
    output_preamble: str = ""
    output_sections: dict[str, str] = field(default_factory=dict)
    manifest_objects: list[HelmManifestObject] = field(default_factory=list)


HelmClusterInstallResult = HelmOperatorInstallResult


@dataclass(frozen=True)
class ImageReference:
    registry: str
    repository: str
    name: str
    tag: str = ""
    digest: str = ""


def _helm_release_key(
    namespace: str,
    release_name: str,
    kube_context: Optional[str] = None,
) -> HelmReleaseKey:
    return (namespace, release_name, kube_context or "")


def _coerce_command_output_text(command_output) -> str:
    if command_output is None:
        return ""

    if hasattr(command_output, "stdout"):
        command_output = getattr(command_output, "stdout")

    if command_output is None:
        return ""

    if isinstance(command_output, bytes):
        return command_output.decode("utf8", errors="replace")

    return str(command_output)


def _split_helm_output_sections(command_output: str) -> tuple[str, dict[str, str]]:
    preamble_lines: list[str] = []
    sections: dict[str, str] = {}
    current_section_name: Optional[str] = None
    current_section_lines: list[str] = []

    for line in command_output.splitlines():
        stripped = line.strip()
        if _HELM_SECTION_HEADER.fullmatch(stripped):
            if current_section_name is not None:
                sections[current_section_name] = "\n".join(current_section_lines).strip("\n")
            current_section_name = stripped[:-1]
            current_section_lines = []
            continue

        if current_section_name is None:
            preamble_lines.append(line)
        else:
            current_section_lines.append(line)

    if current_section_name is not None:
        sections[current_section_name] = "\n".join(current_section_lines).strip("\n")

    return "\n".join(preamble_lines).strip("\n"), sections


def _extract_manifest_objects(manifest_contents: str) -> list[HelmManifestObject]:
    manifest_objects: list[HelmManifestObject] = []
    seen: set[HelmManifestObject] = set()

    if not manifest_contents.strip():
        return manifest_objects

    for manifest in yaml.safe_load_all(manifest_contents):
        if not isinstance(manifest, dict):
            continue

        kind = _coerce_str(manifest.get("kind")).strip()
        metadata = manifest.get("metadata")
        if not kind or not isinstance(metadata, dict):
            continue

        name = _coerce_str(metadata.get("name")).strip()
        if not name:
            continue

        namespace = _coerce_str(metadata.get("namespace")).strip()
        manifest_object = (kind, name, namespace)
        if manifest_object in seen:
            continue
        seen.add(manifest_object)
        manifest_objects.append(manifest_object)

    return manifest_objects


def _build_helm_get_manifest_command(
    options: HelmInstallOptions,
) -> list[str]:
    manifest_cmd = [
        "helm",
        "get",
        "manifest",
        options.release_name,
        "--namespace",
        options.namespace,
    ]
    if options.kube_context:
        manifest_cmd.extend(["--kube-context", options.kube_context])
    return manifest_cmd


def _set_tracked_release_manifest_objects(
    options: HelmInstallOptions,
) -> None:
    release_key = _helm_release_key(
        options.namespace,
        options.release_name,
        options.kube_context,
    )
    options.helm_manifest_objects = list(options.helm_manifest_objects)
    _HELM_RELEASE_MANIFEST_OBJECTS[release_key] = list(options.helm_manifest_objects)


def _clear_tracked_release_manifest_objects(
    options: HelmInstallOptions,
) -> None:
    release_key = _helm_release_key(
        options.namespace,
        options.release_name,
        options.kube_context,
    )
    options.helm_manifest_objects = []
    _HELM_RELEASE_MANIFEST_OBJECTS.pop(release_key, None)


def _record_helm_command_output(
    options: HelmInstallOptions,
    command_output: str,
) -> None:
    options.helm_output_preamble, options.helm_output_sections = (
        _split_helm_output_sections(command_output)
    )


def _extract_manifest_objects_from_output(
    manifest_contents: str,
    *,
    namespace: str,
    release_name: str,
    output_name: str,
) -> list[HelmManifestObject]:
    try:
        return _extract_manifest_objects(manifest_contents)
    except yaml.YAMLError as exc:
        raise RuntimeError(
            f"Failed to parse {output_name} for {namespace}/{release_name}: {exc}"
        ) from exc


def _fetch_release_manifest_objects(
    options: HelmInstallOptions,
) -> list[HelmManifestObject]:
    manifest_result = run_command(
        _build_helm_get_manifest_command(options),
    )
    return _extract_manifest_objects_from_output(
        _coerce_command_output_text(manifest_result),
        namespace=options.namespace,
        release_name=options.release_name,
        output_name="helm get manifest output",
    )


def _refresh_tracked_release_manifest_objects(
    options: HelmInstallOptions,
) -> list[HelmManifestObject]:
    options.helm_manifest_objects = _fetch_release_manifest_objects(options)
    _set_tracked_release_manifest_objects(options)
    return list(options.helm_manifest_objects)


def _try_refresh_tracked_release_manifest_objects(
    options: HelmInstallOptions,
) -> bool:
    try:
        _refresh_tracked_release_manifest_objects(options)
    except Exception as exc:
        logger.warning(
            "Failed to fetch current helm manifest for %s/%s: %s",
            options.namespace,
            options.release_name,
            exc,
        )
        return False
    return True


def _restore_tracked_release_manifest_objects(
    options: HelmInstallOptions,
    manifest_objects: list[HelmManifestObject],
    *,
    release_was_tracked: bool,
) -> None:
    if not release_was_tracked:
        _clear_tracked_release_manifest_objects(options)
        return

    options.helm_manifest_objects = list(manifest_objects)
    _set_tracked_release_manifest_objects(options)


def _attach_helm_output_metadata(
    exc: Exception,
    options: HelmInstallOptions,
) -> None:
    exc.helm_output_preamble = options.helm_output_preamble
    exc.helm_output_sections = copy.deepcopy(options.helm_output_sections)
    exc.helm_manifest_objects = list(options.helm_manifest_objects)


def _format_manifest_object(manifest_object: HelmManifestObject) -> str:
    object_type, object_name, object_namespace = manifest_object
    return (
        f"{object_type}/{object_name}"
        + (f" (ns={object_namespace})" if object_namespace else "")
    )


def _format_manifest_object_list(
    manifest_objects: list[HelmManifestObject],
) -> str:
    return "\n".join(
        _format_manifest_object(manifest_object)
        for manifest_object in manifest_objects
    )


def _manifest_object_exists(manifest_object: HelmManifestObject) -> bool:
    object_type, object_name, object_namespace = manifest_object
    namespace = object_namespace or None

    if namespace is None and object_type not in _HELM_CLUSTER_SCOPED_KINDS:
        logger.warning(
            "Skipping existence check for manifest object %s/%s because no namespace was captured",
            object_type,
            object_name,
        )
        return False

    return kutil.get(
        namespace,
        object_type.lower(),
        object_name,
        check=False,
        cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE,
    ) is not None


def _get_remaining_manifest_objects(
    manifest_objects: list[HelmManifestObject],
) -> list[HelmManifestObject]:
    remaining_manifest_objects: list[HelmManifestObject] = []
    seen: set[HelmManifestObject] = set()

    for manifest_object in manifest_objects:
        if manifest_object in seen:
            continue
        seen.add(manifest_object)

        if _manifest_object_exists(manifest_object):
            remaining_manifest_objects.append(manifest_object)

    return remaining_manifest_objects


def _wait_for_manifest_objects_gone(
    manifest_objects: list[HelmManifestObject],
    *,
    timeout: int = _HELM_MANIFEST_SETTLE_TIMEOUT_SECONDS,
) -> list[HelmManifestObject]:
    deadline = time.time() + max(timeout, 0)
    remaining_manifest_objects = _get_remaining_manifest_objects(manifest_objects)

    while remaining_manifest_objects and time.time() < deadline:
        time.sleep(1)
        remaining_manifest_objects = _get_remaining_manifest_objects(manifest_objects)

    return remaining_manifest_objects


def _delete_manifest_objects(manifest_objects: list[HelmManifestObject]) -> None:
    cleanup_error = None

    for object_type, object_name, object_namespace in reversed(manifest_objects):
        namespace = object_namespace or None
        if namespace is None and object_type not in _HELM_CLUSTER_SCOPED_KINDS:
            logger.warning(
                "Skipping manual cleanup for manifest object %s/%s because no namespace was captured",
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


def _normalize_watch_namespaces(watch_namespaces: Optional[str]) -> str:
    if not watch_namespaces:
        return ""

    normalized = []
    seen = set()
    for namespace in watch_namespaces.split(","):
        namespace = namespace.strip()
        if not namespace or namespace in seen:
            continue
        seen.add(namespace)
        normalized.append(namespace)

    return ",".join(normalized)


def _normalize_watch_namespace_list(watch_namespaces) -> list[str]:
    if isinstance(watch_namespaces, list):
        watch_namespaces = ",".join(
            str(namespace)
            for namespace in watch_namespaces
        )
    elif watch_namespaces is None:
        watch_namespaces = ""

    normalized = _normalize_watch_namespaces(str(watch_namespaces))
    return normalized.split(",") if normalized else []


def _get_nested_value(mapping: Optional[dict], *path, default=None):
    current = mapping
    for key in path:
        if not isinstance(current, dict):
            return default
        if key not in current:
            return default
        current = current[key]
    return current


def _is_registry_host(segment: str) -> bool:
    return "." in segment or ":" in segment or segment == "localhost"


def _parse_image_reference(image: str) -> ImageReference:
    digest = ""
    if "@" in image:
        image, digest = image.split("@", 1)

    slash = image.rfind("/")
    colon = image.rfind(":")
    tag = ""
    if colon > slash:
        image, tag = image[:colon], image[colon + 1:]

    parts = image.split("/")

    if len(parts) < 2 or not _is_registry_host(parts[0]):
        raise ValueError(
            f"Expected image reference with explicit registry host: {image}"
        )

    registry = parts[0]
    remainder = parts[1:]

    if len(remainder) == 1:
        return ImageReference(registry, "", remainder[0], tag=tag, digest=digest)

    return ImageReference(
        registry,
        "/".join(remainder[:-1]),
        remainder[-1],
        tag=tag,
        digest=digest,
    )


def _split_registry_and_repository(repository_ref: str) -> tuple[str, str]:
    repository_ref = (repository_ref or "").strip().rstrip("/")
    if not repository_ref:
        return "", ""

    parts = repository_ref.split("/")
    if len(parts) < 2 or not _is_registry_host(parts[0]):
        raise ValueError(
            "Expected repository reference with explicit registry host: "
            f"{repository_ref}"
        )

    return parts[0], "/".join(parts[1:])


def _get_operator_container(deployment: dict) -> dict:
    containers = deployment["spec"]["template"]["spec"].get("containers", [])
    if not containers:
        raise ValueError("Operator deployment has no containers")
    return containers[0]


def _get_operator_env_map(deployment: dict) -> dict:
    operator_container = _get_operator_container(deployment)
    env_map = {}
    for env in operator_container.get("env", []):
        name = env.get("name")
        if not name:
            continue
        if "value" in env:
            env_map[name] = env["value"]
        elif "valueFrom" in env:
            env_map[name] = env["valueFrom"]
    return env_map


def _coerce_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _coerce_int(value, default: int = 0) -> int:
    if value is None or value == "":
        return default
    return int(value)


def _coerce_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value)


def _deduce_edition_from_image_name(image_name: str) -> str:
    if image_name == "enterprise-operator" or image_name.endswith("/enterprise-operator"):
        return "enterprise"
    return "community"


def _sanitize_namespace_labels(labels: Optional[dict]) -> dict:
    clean_labels = dict(labels or {})
    clean_labels.pop("kubernetes.io/metadata.name", None)
    return clean_labels


def _compose_image_reference(
    registry: str,
    repository: str,
    name: str,
    tag: Optional[str] = None,
    digest: Optional[str] = None,
) -> str:
    path_parts = [part.strip("/") for part in (registry, repository, name) if part]
    image = "/".join(path_parts)
    if tag:
        image = f"{image}:{tag}"
    if digest:
        image = f"{image}@{digest}"
    return image


def _reconcile_operator_deployment_image(options: HelmOperatorInstallOptions) -> bool:
    desired_image = _compose_image_reference(
        options.operator_registry,
        options.operator_repository,
        options.operator_image_name,
        options.operator_image_tag,
        options.operator_image_digest,
    )
    deployment = kutil.get_deploy(options.namespace, options.deployment_name)
    operator_container = _get_operator_container(deployment)
    current_image = operator_container.get("image")
    current_pull_policy = operator_container.get("imagePullPolicy")

    if (
        current_image == desired_image
        and current_pull_policy == options.operator_image_pull_policy
    ):
        return False

    kutil.patch_dp(
        options.namespace,
        options.deployment_name,
        {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": operator_container["name"],
                                "image": desired_image,
                                "imagePullPolicy": options.operator_image_pull_policy,
                            }
                        ]
                    }
                }
            }
        },
    )
    return True


def _build_default_install_with_helm_options(
    namespace: str,
    *,
    source_operator_namespace: Optional[str] = None,
    source_operator_deployment_name: Optional[str] = None,
    source_operator_deployment: Optional[dict] = None,
) -> HelmOperatorInstallOptions:
    source_operator_namespace = source_operator_namespace or namespace

    if source_operator_deployment is None:
        source_operator_deployment_name = source_operator_deployment_name or "mysql-operator"
        source_operator_deployment = kutil.get_deploy(
            source_operator_namespace,
            source_operator_deployment_name,
        )

    if not source_operator_deployment:
        raise ValueError(
            f"Could not resolve operator deployment "
            f"{source_operator_namespace}/{source_operator_deployment_name or 'mysql-operator'}"
        )

    source_operator_deployment_name = source_operator_deployment["metadata"]["name"]
    source_operator_env = _get_operator_env_map(source_operator_deployment)
    source_operator_container = _get_operator_container(source_operator_deployment)
    source_image = _parse_image_reference(source_operator_container["image"])

    release_name = (
        source_operator_deployment.get("metadata", {})
        .get("annotations", {})
        .get("meta.helm.sh/release-name")
    ) or "myoperator"

    resolved_operator_image_name = source_image.name
    resolved_edition = _deduce_edition_from_image_name(resolved_operator_image_name)
    resolved_operator_image_pull_policy = (
        source_operator_container.get("imagePullPolicy")
        or g_ts_cfg.operator_pull_policy
    )
    source_managed_images_pull_policy = source_operator_env.get(
        "MYSQL_OPERATOR_IMAGE_PULL_POLICY"
    )
    if not isinstance(source_managed_images_pull_policy, str):
        source_managed_images_pull_policy = resolved_operator_image_pull_policy

    resolved_operator_registry = source_image.registry
    resolved_operator_repository = source_image.repository
    resolved_default_registry = resolved_operator_registry
    resolved_default_repository = resolved_operator_repository

    try:
        namespace_labels = _sanitize_namespace_labels(
            kutil.get_ns_labels(source_operator_namespace)
        )
    except Exception as exc:
        logger.warning(
            "Failed to fetch labels for source operator namespace %s: %s",
            source_operator_namespace,
            exc,
        )
        namespace_labels = {}

    operator_values = {
        "edition": resolved_edition,
        "namespaceLabels": namespace_labels,
        "image": {
            "registry": resolved_operator_registry,
            "repository": resolved_operator_repository,
            "name": resolved_operator_image_name,
            "pullPolicy": _coerce_str(
                resolved_operator_image_pull_policy,
                default=g_ts_cfg.operator_pull_policy,
            ),
            "digest": source_image.digest or None,
        },
        "envs": {
            "imagesPullPolicy": _coerce_str(
                source_managed_images_pull_policy,
                default=g_ts_cfg.operator_pull_policy,
            ),
            "k8sClusterDomain": (
                source_operator_env.get("MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN")
                or _default_k8s_domain()
            ),
            "imagesDefaultRegistry": resolved_default_registry,
            "imagesDefaultRepository": resolved_default_repository,
        },
        "replicas": source_operator_deployment.get("spec", {}).get("replicas", 1),
        "deployment": {
            "name": source_operator_deployment_name,
            "standalone": _coerce_bool(source_operator_env.get("OPERATOR_STANDALONE", False)),
            "namespaces": _normalize_watch_namespace_list(
                source_operator_env.get("OPERATOR_NAMESPACES", "")
            ),
        },
        "operatorDebug": _coerce_int(
            source_operator_env.get("MYSQL_OPERATOR_DEBUG"),
            default=0,
        ),
    }
    if source_image.tag:
        operator_values["image"]["tag"] = source_image.tag

    return HelmOperatorInstallOptions(
        namespace=namespace,
        app_version=source_image.tag or g_ts_cfg.operator_version_tag,
        version=None,
        release_name=release_name,
        kube_context=g_ts_cfg.k8s_context,
        helm_package="mysql-operator",
        operator_values=operator_values,
    )


def resolve_install_with_helm_options(
    namespace: str,
    *,
    source_operator_namespace: Optional[str] = None,
    source_operator_deployment_name: Optional[str] = None,
    source_operator_deployment: Optional[dict] = None,
) -> HelmOperatorInstallOptions:
    return HelmOperatorInstallOptions.from_source(
        namespace,
        source_operator_namespace=source_operator_namespace,
        source_operator_deployment_name=source_operator_deployment_name,
        source_operator_deployment=source_operator_deployment,
    )


def _merge_operator_values(base_values: Optional[dict], override_values: dict) -> dict:
    if base_values is None:
        merged_values = {}
    elif isinstance(base_values, dict):
        merged_values = copy.deepcopy(base_values)
    else:
        raise ValueError("operator_values must be a dict")

    for key, override_value in override_values.items():
        base_value = merged_values.get(key)
        if isinstance(base_value, dict) and isinstance(override_value, dict):
            merged_values[key] = _merge_operator_values(base_value, override_value)
        else:
            merged_values[key] = copy.deepcopy(override_value)
    return merged_values


def _build_operator_values_for_helm(
    options: HelmOperatorInstallOptions,
) -> dict:
    if options.use_chart_defaults:
        return _merge_operator_values({}, options.explicit_operator_values)

    values = _merge_operator_values({}, options.operator_values)
    values.pop("edition", None)
    values.pop("namespaceLabels", None)

    image_values = values.setdefault("image", {})
    image_values["registry"] = options.operator_registry
    image_values["repository"] = options.operator_repository
    image_values["name"] = options.operator_image_name
    image_values["pullPolicy"] = options.operator_image_pull_policy
    image_values.pop("digest", None)
    if options.operator_image_tag is None:
        image_values.pop("tag", None)
    else:
        image_values["tag"] = options.operator_image_tag
    image_values.pop("pullSecrets", None)

    env_values = values.setdefault("envs", {})
    env_values["imagesPullPolicy"] = options.managed_images_pull_policy
    env_values["k8sClusterDomain"] = options.k8s_domain
    if options.default_registry is None:
        env_values.pop("imagesDefaultRegistry", None)
    else:
        env_values["imagesDefaultRegistry"] = options.default_registry
    if options.default_repository is None:
        env_values.pop("imagesDefaultRepository", None)
    else:
        env_values["imagesDefaultRepository"] = options.default_repository

    values["replicas"] = options.deployment_replicas

    deployment_values = values.setdefault("deployment", {})
    deployment_values["name"] = options.deployment_name
    deployment_values["standalone"] = options.standalone
    if options.operator_namespaces_list:
        deployment_values["namespaces"] = options.operator_namespaces_list
    else:
        deployment_values.pop("namespaces", None)

    values["operatorDebug"] = options.debug_operator
    return values


def _load_yaml_mapping(
    yaml_text: str,
    *,
    description: str,
) -> dict:
    loaded = yaml.safe_load(yaml_text)
    if loaded is None:
        return {}
    if not isinstance(loaded, dict):
        raise ValueError(f"{description} must parse to a mapping")
    return loaded


def _merge_named_mapping(
    values: dict,
    field_name: str,
    extra_values: Optional[dict],
) -> None:
    if not extra_values:
        return

    merged_values = copy.deepcopy(extra_values)
    current_values = values.get(field_name)
    if current_values is None:
        values[field_name] = merged_values
        return

    if not isinstance(current_values, dict):
        raise ValueError(f"{field_name} must be a mapping")

    current_values.update(merged_values)


def _get_custom_cluster_server_version() -> Optional[str]:
    custom_server_version_override = g_ts_cfg.get_custom_ic_server_version_override()
    if custom_server_version_override:
        return custom_server_version_override

    custom_server_version = g_ts_cfg.get_custom_ic_server_version()
    if custom_server_version:
        return custom_server_version

    return None


def _validate_custom_cluster_router_version() -> None:
    custom_router_version_override = g_ts_cfg.get_custom_ic_router_version_override()
    custom_router_version = g_ts_cfg.get_custom_ic_router_version()
    if custom_router_version_override or custom_router_version:
        raise RuntimeError(
            "mysql-innodbcluster Helm installs cannot emulate test-harness "
            "router version overrides because the chart has no existing value "
            "for router.version"
        )


def _build_cluster_values_for_helm(
    options: HelmClusterInstallOptions,
) -> dict:
    values = _merge_operator_values({}, options.cluster_values)

    _merge_named_mapping(
        values,
        "podLabels",
        g_ts_cfg.get_custom_sts_labels(),
    )

    custom_sts_podspec = g_ts_cfg.get_custom_sts_podspec()
    if custom_sts_podspec:
        _merge_named_mapping(
            values,
            "podSpec",
            _load_yaml_mapping(
                custom_sts_podspec,
                description="custom STS podSpec",
            ),
        )

    custom_server_version = _get_custom_cluster_server_version()
    if custom_server_version:
        values["serverVersion"] = custom_server_version

    _validate_custom_cluster_router_version()

    if values.get("serverVersion") == g_ts_cfg.operator_version_tag:
        values.pop("serverVersion", None)

    return values


def _build_values_for_helm(
    options: HelmInstallOptions,
) -> dict:
    if isinstance(options, HelmClusterInstallOptions):
        return _build_cluster_values_for_helm(options)
    return _build_operator_values_for_helm(options)


def _build_helm_apply_command(
    options: HelmInstallOptions,
    *,
    values_path: Optional[str],
    chart_path: str,
    install_if_missing: bool,
) -> list[str]:
    helm_cmd = ["helm", "upgrade"]
    if install_if_missing:
        helm_cmd.append("--install")
    helm_cmd.append(options.release_name)
    helm_cmd.extend(["--namespace", options.namespace])
    if options.version:
        helm_cmd.extend(["--version", options.version])
    if options.kube_context:
        helm_cmd.extend(["--kube-context", options.kube_context])
    if values_path is not None:
        helm_cmd.extend(["--values", values_path])
    helm_cmd.append("--debug")
    if options.wait_for_helm:
        helm_cmd.append("--wait")
    if options.wait_for_helm and options.helm_wait_timeout_seconds is not None:
        helm_cmd.extend(
            ["--timeout", f"{int(options.helm_wait_timeout_seconds)}s"]
        )
    helm_cmd.append(chart_path)
    return helm_cmd


def _build_install_with_helm_options(
    namespace: str,
    *,
    resolved_options: Optional[HelmOperatorInstallOptions] = None,
    source_operator_namespace: Optional[str] = None,
    source_operator_deployment_name: Optional[str] = None,
    source_operator_deployment: Optional[dict] = None,
) -> HelmOperatorInstallOptions:
    return resolved_options or HelmOperatorInstallOptions.from_source(
        namespace,
        source_operator_namespace=source_operator_namespace,
        source_operator_deployment_name=source_operator_deployment_name,
        source_operator_deployment=source_operator_deployment,
    )


def _install_or_upgrade_with_helm(
    options: HelmInstallOptions,
    *,
    install_if_missing: bool,
) -> HelmOperatorInstallResult:
    release_key = _helm_release_key(
        options.namespace,
        options.release_name,
        options.kube_context,
    )
    release_was_tracked = release_key in _HELM_RELEASE_MANIFEST_OBJECTS
    previous_manifest_objects = list(
        _HELM_RELEASE_MANIFEST_OBJECTS.get(release_key, [])
    )

    if install_if_missing:
        kutil.create_ns(options.namespace, labels=options.namespace_labels)

    chart_path = get_helm_chart_path(
        options.helm_package,
        app_version=options.app_version,
    )
    helm_values = _build_values_for_helm(options)

    def run_helm_apply(helm_cmd: list[str]) -> None:
        try:
            helm_result = run_command(helm_cmd)
        except subprocess.CalledProcessError as exc:
            _record_helm_command_output(
                options,
                _coerce_command_output_text(exc),
            )
            if release_was_tracked:
                if not _try_refresh_tracked_release_manifest_objects(options):
                    _restore_tracked_release_manifest_objects(
                        options,
                        previous_manifest_objects,
                        release_was_tracked=release_was_tracked,
                    )
            else:
                _clear_tracked_release_manifest_objects(options)
            _attach_helm_output_metadata(exc, options)
            raise

        _record_helm_command_output(
            options,
            _coerce_command_output_text(helm_result),
        )
        try:
            _refresh_tracked_release_manifest_objects(options)
        except Exception as exc:
            _attach_helm_output_metadata(exc, options)
            raise

    if helm_values:
        rendered_values = yaml.safe_dump(helm_values, sort_keys=False)

        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".yaml",
            encoding="utf8",
        ) as values_file:
            values_file.write(rendered_values)
            values_file.flush()
            print(
                f"Generated Helm values file {values_file.name}:\n"
                f"{rendered_values}",
                flush=True,
            )

            helm_cmd = _build_helm_apply_command(
                options,
                values_path=values_file.name,
                chart_path=chart_path,
                install_if_missing=install_if_missing,
            )
            run_helm_apply(helm_cmd)
    else:
        helm_cmd = _build_helm_apply_command(
            options,
            values_path=None,
            chart_path=chart_path,
            install_if_missing=install_if_missing,
        )
        run_helm_apply(helm_cmd)

    if (
        isinstance(options, HelmOperatorInstallOptions)
        and not options.use_chart_defaults
    ):
        patched_operator_image = _reconcile_operator_deployment_image(options)

        if patched_operator_image:
            kutil.wait_deploy(options.namespace, options.deployment_name, timeout=300)
            for pod in kutil.ls_po(options.namespace, pattern=f"{options.deployment_name}-.*"):
                kutil.wait_pod(
                    options.namespace,
                    pod["NAME"],
                    "Running",
                    timeout=300,
                    checkready=True,
                )

    return HelmOperatorInstallResult(
        options=options,
        release_key=_helm_release_key(
            options.namespace,
            options.release_name,
            options.kube_context,
        ),
        output_preamble=options.helm_output_preamble,
        output_sections=copy.deepcopy(options.helm_output_sections),
        manifest_objects=list(options.helm_manifest_objects),
    )


def install_with_helm(
    namespace: str,
    *,
    resolved_options: Optional[HelmOperatorInstallOptions] = None,
    source_operator_namespace: Optional[str] = None,
    source_operator_deployment_name: Optional[str] = None,
    source_operator_deployment: Optional[dict] = None,
) -> HelmOperatorInstallResult:
    options = _build_install_with_helm_options(
        namespace,
        resolved_options=resolved_options,
        source_operator_namespace=source_operator_namespace,
        source_operator_deployment_name=source_operator_deployment_name,
        source_operator_deployment=source_operator_deployment,
    )
    return _install_or_upgrade_with_helm(
        options,
        install_if_missing=True,
    )


def install_cluster_with_helm(
    namespace: str,
    *,
    resolved_options: Optional[HelmClusterInstallOptions] = None,
) -> HelmClusterInstallResult:
    options = resolved_options or HelmClusterInstallOptions(
        namespace=namespace,
        kube_context=g_ts_cfg.k8s_context,
    )
    return _install_or_upgrade_with_helm(
        options,
        install_if_missing=True,
    )


def upgrade_cluster_with_helm(
    namespace: str,
    *,
    resolved_options: Optional[HelmClusterInstallOptions] = None,
) -> HelmClusterInstallResult:
    options = resolved_options or HelmClusterInstallOptions(
        namespace=namespace,
        kube_context=g_ts_cfg.k8s_context,
    )
    return _install_or_upgrade_with_helm(
        options,
        install_if_missing=False,
    )


def upgrade_with_helm(
    namespace: str,
    *,
    resolved_options: Optional[HelmOperatorInstallOptions] = None,
    source_operator_namespace: Optional[str] = None,
    source_operator_deployment_name: Optional[str] = None,
    source_operator_deployment: Optional[dict] = None,
) -> HelmOperatorInstallResult:
    options = _build_install_with_helm_options(
        namespace,
        resolved_options=resolved_options,
        source_operator_namespace=source_operator_namespace,
        source_operator_deployment_name=source_operator_deployment_name,
        source_operator_deployment=source_operator_deployment,
    )
    return _install_or_upgrade_with_helm(
        options,
        install_if_missing=False,
    )


def uninstall_with_helm(
    release_key: HelmReleaseKey,
    *,
    delete_namespace: bool = True,
    check_namespace_empty: bool = False,
    check_namespace_empty_timeout: int = 60,
    manifest_objects: Optional[list[HelmManifestObject]] = None,
) -> None:
    if len(release_key) != 3:
        raise ValueError(
            "release_key must be a tuple of "
            "(namespace, release_name, kube_context)"
        )

    namespace, release_name, kube_context = release_key
    release_key = _helm_release_key(namespace, release_name, kube_context)
    uninstall_cmd = [
        "helm",
        "uninstall",
        release_name,
        "--namespace",
        namespace,
        "--wait",
    ]
    if kube_context:
        uninstall_cmd.extend(["--kube-context", kube_context])

    if manifest_objects is None:
        manifest_objects = _HELM_RELEASE_MANIFEST_OBJECTS.pop(release_key, [])
    else:
        _HELM_RELEASE_MANIFEST_OBJECTS.pop(release_key, None)

    run_command(uninstall_cmd, check=False)

    cleanup_error = None
    if manifest_objects:
        remaining_manifest_objects = _wait_for_manifest_objects_gone(
            manifest_objects,
            timeout=_HELM_MANIFEST_SETTLE_TIMEOUT_SECONDS,
        )
        if remaining_manifest_objects:
            logger.warning(
                "Helm uninstall left manifest objects behind for %s/%s; "
                "attempting manual cleanup:\n%s",
                namespace,
                release_name,
                _format_manifest_object_list(remaining_manifest_objects),
            )
            manual_cleanup_error = None
            try:
                _delete_manifest_objects(remaining_manifest_objects)
            except Exception as exc:
                manual_cleanup_error = exc

            remaining_manifest_objects = _wait_for_manifest_objects_gone(
                remaining_manifest_objects,
                timeout=_HELM_MANIFEST_SETTLE_TIMEOUT_SECONDS,
            )
            if remaining_manifest_objects:
                cleanup_error = RuntimeError(
                    "Helm uninstall manual cleanup still left manifest objects "
                    "behind:\n"
                    + _format_manifest_object_list(remaining_manifest_objects)
                    + (
                        f"\nFirst manual cleanup error: {manual_cleanup_error}"
                        if manual_cleanup_error is not None
                        else ""
                    )
                )

    if namespace in kutil.ls_ns_ex():
        namespace_ready_for_deletion = True
        if check_namespace_empty and not manifest_objects:
            try:
                wait_for_namespace_empty(
                    namespace,
                    timeout=check_namespace_empty_timeout,
                )
            except Exception as exc:
                namespace_ready_for_deletion = False
                if cleanup_error is None:
                    cleanup_error = exc
        if delete_namespace and namespace_ready_for_deletion:
            try:
                kutil.delete_ns(namespace)
            except Exception as exc:
                if cleanup_error is None:
                    cleanup_error = exc

    if cleanup_error is not None:
        raise cleanup_error


def wait_for_namespace_empty(
    namespace: str,
    *,
    timeout: int = 60,
) -> None:
    deadline = time.time() + max(timeout, 0)
    namespace_objects = ""

    while True:
        if namespace not in kutil.ls_ns_ex():
            return

        namespace_objects = kutil.ls_all_raw(namespace)
        if not namespace_objects:
            return

        remaining_timeout = max(int(deadline - time.time()), 0)
        if remaining_timeout > 0:
            pods = kutil.ls_po(namespace)
            if pods and all(
                pod.get("STATUS") in ("Terminating", "Completed")
                for pod in pods
            ):
                for pod in pods:
                    kutil.wait_pod_gone(
                        namespace,
                        pod["NAME"],
                        timeout=remaining_timeout,
                    )
                continue

        if time.time() >= deadline:
            break

        time.sleep(1)

    raise RuntimeError(
        f"Namespace {namespace} still contains objects after helm uninstall:\n"
        f"{namespace_objects}"
    )
