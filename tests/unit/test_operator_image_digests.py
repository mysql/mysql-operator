# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

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
def image_digests_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules("setup.image_digests")

    module = importlib.import_module("setup.image_digests")
    yield module

    _drop_modules("setup.image_digests")


@pytest.fixture
def operator_t_module(monkeypatch):
    monkeypatch.syspath_prepend(str(TESTS_ROOT))
    _drop_modules(
        "e2e.mysqloperator.operator.operator_t",
        "e2e.mysqloperator.operator",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.stream",
        "mysql",
        "mysql.connector",
        "utils.helmutil",
        "utils.kutil",
        "utils.tutil",
        "utils.utils",
        "setup.config",
        "setup.defaults",
        "setup.image_digests",
    )

    mysql_module = types.ModuleType("mysql")
    mysql_connector_module = types.ModuleType("mysql.connector")
    mysql_module.connector = mysql_connector_module
    sys.modules["mysql"] = mysql_module
    sys.modules["mysql.connector"] = mysql_connector_module

    kubernetes_module = types.ModuleType("kubernetes")
    kubernetes_client_module = types.ModuleType("kubernetes.client")
    kubernetes_client_rest_module = types.ModuleType("kubernetes.client.rest")
    kubernetes_stream_module = types.ModuleType("kubernetes.stream")

    class ApiException(Exception):
        pass

    kubernetes_client_rest_module.ApiException = ApiException
    kubernetes_stream_module.stream = lambda *args, **kwargs: None
    kubernetes_module.client = kubernetes_client_module
    kubernetes_module.stream = kubernetes_stream_module

    sys.modules["kubernetes"] = kubernetes_module
    sys.modules["kubernetes.client"] = kubernetes_client_module
    sys.modules["kubernetes.client.rest"] = kubernetes_client_rest_module
    sys.modules["kubernetes.stream"] = kubernetes_stream_module

    tutil_module = types.ModuleType("utils.tutil")

    class OperatorTest(unittest.TestCase):
        pass

    tutil_module.OperatorTest = OperatorTest
    tutil_module.g_full_log = False
    sys.modules["utils.tutil"] = tutil_module

    module = importlib.import_module("e2e.mysqloperator.operator.operator_t")
    yield module

    _drop_modules(
        "e2e.mysqloperator.operator.operator_t",
        "e2e.mysqloperator.operator",
        "kubernetes",
        "kubernetes.client",
        "kubernetes.client.rest",
        "kubernetes.stream",
        "mysql",
        "mysql.connector",
        "utils.helmutil",
        "utils.kutil",
        "utils.tutil",
        "utils.utils",
        "setup.config",
        "setup.defaults",
        "setup.image_digests",
    )


def test_canonicalize_image_name_supports_harness_aliases(image_digests_module):
    assert image_digests_module.canonicalize_image_name("mysql-operator") == "community-operator"
    assert image_digests_module.canonicalize_image_name("mysql-enterprise-server") == "enterprise-server"
    assert image_digests_module.canonicalize_image_name("mysql-router") == "community-router"


def test_lookup_historical_image_digest_succeeds_for_known_release(
    image_digests_module,
):
    assert (
        image_digests_module.lookup_historical_image_digest(
            "community-operator",
            "9.3.0-2.2.4",
        )
        == "sha256:cd424d7c5ec40d95fbaccfa448cfca6d7345235e51647209439b8665307620be"
    )


def test_lookup_historical_image_digest_reports_known_missing_ocr_artifact(
    image_digests_module,
):
    with pytest.raises(
        image_digests_module.KnownMissingImageArtifactError,
        match="Known missing OCR artifact",
    ):
        image_digests_module.lookup_historical_image_digest(
            "enterprise-operator",
            "8.0.36-2.0.13",
        )


def test_lookup_historical_image_digest_reports_missing_coverage(
    image_digests_module,
):
    with pytest.raises(
        image_digests_module.HistoricalImageDigestCoverageError,
        match="No embedded historical digest",
    ):
        image_digests_module.lookup_historical_image_digest(
            "community-operator",
            "9.9.9-9.9.9",
        )


def test_split_image_reference_uses_image_key_not_registry_path(
    operator_t_module,
):
    assert (
        operator_t_module.OperatorSingleAndMultipleBaseTest._split_image_reference(
            "registry.example.com:5000/mysql/community-operator:9.3.0-2.2.4"
        )
        == ("community-operator", "9.3.0-2.2.4")
    )


def test_current_dev_tags_are_skipped_from_digest_lookup(
    operator_t_module,
    monkeypatch,
):
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_version_tag",
        "9.7.0-2.2.8",
    )
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "version_tag",
        "9.7.0",
    )

    assert (
        operator_t_module.OperatorSingleAndMultipleBaseTest
        ._expected_historical_image_digest_for_tag(
            "community-operator",
            "9.7.0-2.2.8",
        )
        is None
    )
    assert (
        operator_t_module.OperatorSingleAndMultipleBaseTest
        ._expected_historical_image_digest_for_tag(
            "community-server",
            "9.7.0",
        )
        is None
    )
    assert (
        operator_t_module.OperatorSingleAndMultipleBaseTest
        ._expected_historical_image_digest_for_tag(
            "community-router",
            "9.7.0",
        )
        is None
    )


def test_digest_lookup_skips_releases_after_9_5_cutoff(operator_t_module):
    helper = (
        operator_t_module.OperatorSingleAndMultipleBaseTest
        ._expected_historical_image_digest_for_tag
    )

    assert helper("community-operator", "9.6.0-2.2.7") is None
    assert helper("enterprise-operator", "9.6.0-2.2.7") is None
    assert helper("community-server", "9.6.0") is None
    assert helper("enterprise-router", "9.6.0") is None


def test_digest_lookup_checks_9_5_cutoff_release(operator_t_module):
    helper = (
        operator_t_module.OperatorSingleAndMultipleBaseTest
        ._expected_historical_image_digest_for_tag
    )

    assert (
        helper("community-operator", "9.5.0-2.2.6")
        == "sha256:94f00afa435f356d0ee1f6a8d77ee610f0b5a551a09891f6a3497aa05d590436"
    )
    assert (
        helper("community-server", "9.5.0")
        == "sha256:9a964baff432ca44c625035a60811639047de9c0523b1ad5746edf54428d2129"
    )


def test_raw_manifest_operator_sidecar_tag_check_uses_9_5_cutoff(
    operator_t_module,
    monkeypatch,
):
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_version_tag",
        "9.7.0-2.2.8",
    )
    helper = (
        operator_t_module.OperatorSingleAndMultipleBaseTest
        ._should_check_raw_manifest_cluster_operator_image_tag
    )

    assert helper("9.5.0-2.2.6")
    assert not helper("9.6.0-2.2.7")
    assert helper("9.7.0-2.2.8")


def test_image_identity_can_skip_tag_check_for_local_historic_operator_image(
    operator_t_module,
):
    testcase = operator_t_module.OperatorSingleAndMultipleBaseTest(
        methodName="runTest"
    )
    pod = {
        "metadata": {"name": "cluster-0"},
        "spec": {
            "initContainers": [
                {
                    "name": "fixdatadir",
                    "image": "registry.example.com/mysql/community-operator:9.7.0-2.2.8",
                }
            ],
        },
        "status": {
            "initContainerStatuses": [
                {
                    "name": "fixdatadir",
                    "imageID": (
                        "registry.example.com/mysql/community-operator@"
                        "sha256:fcd5bc5a5afbaeeed2a99a481f40b5b423ad2bd07c81b8e9fa2bf4eed648f7b1"
                    ),
                }
            ],
        },
    }

    testcase._assert_pod_container_image_identity(
        pod=pod,
        container_name="fixdatadir",
        spec_container_key="initContainers",
        status_container_key="initContainerStatuses",
        expected_tag=None,
        expected_image_name_keys=operator_t_module.OPERATOR_IMAGE_DIGEST_KEYS,
    )


def test_image_identity_waits_for_runtime_image_id(
    operator_t_module,
    monkeypatch,
):
    testcase = operator_t_module.OperatorSingleAndMultipleBaseTest(
        methodName="runTest"
    )
    pod = {
        "metadata": {"namespace": "cluster-ns", "name": "cluster-0"},
        "spec": {
            "containers": [
                {
                    "name": "sidecar",
                    "image": "registry.example.com/mysql/community-operator:9.7.0-2.2.8",
                }
            ],
        },
        "status": {
            "containerStatuses": [
                {
                    "name": "sidecar",
                    "imageID": "",
                }
            ],
        },
    }
    refreshed_pod = {
        "status": {
            "containerStatuses": [
                {
                    "name": "sidecar",
                    "imageID": (
                        "registry.example.com/mysql/community-operator@"
                        "sha256:fcd5bc5a5afbaeeed2a99a481f40b5b423ad2bd07c81b8e9fa2bf4eed648f7b1"
                    ),
                }
            ],
        },
    }
    get_po_calls = []

    def get_po(namespace, name, **kwargs):
        get_po_calls.append((namespace, name, kwargs))
        return refreshed_pod

    monkeypatch.setattr(operator_t_module.kutil, "get_po", get_po)
    monkeypatch.setattr(
        operator_t_module.g_ts_cfg,
        "operator_version_tag",
        "9.7.0-2.2.8",
    )

    testcase._assert_pod_container_image_identity(
        pod=pod,
        container_name="sidecar",
        spec_container_key="containers",
        status_container_key="containerStatuses",
        expected_tag="9.7.0-2.2.8",
        expected_image_name_keys=operator_t_module.OPERATOR_IMAGE_DIGEST_KEYS,
    )

    assert get_po_calls == [
        (
            "cluster-ns",
            "cluster-0",
            {
                "check": False,
                "cmd_output_log": operator_t_module.kutil.KubectlCmdOutputLogging.MUTE,
            },
        )
    ]


def test_helm_cluster_runtime_image_identities_allow_operator_image_roll_forward(
    operator_t_module,
    monkeypatch,
):
    testcase = operator_t_module.OperatorSingleAndMultipleBaseTest(
        methodName="runTest"
    )
    cluster_name = "cluster"
    namespace = "cluster-ns"
    cluster_release = "9.6.0-2.2.7"
    operator_release = "9.7.0-2.2.8"
    mysql_version = "9.6.0"
    runtime_image_id = "containerd://sha256:" + ("1" * 64)

    def spec_container(name, image_name, tag):
        return {
            "name": name,
            "image": f"registry.example.com/mysql/{image_name}:{tag}",
        }

    def status_container(name):
        return {
            "name": name,
            "imageID": runtime_image_id,
        }

    server_pod = {
        "metadata": {
            "namespace": namespace,
            "name": f"{cluster_name}-0",
        },
        "spec": {
            "initContainers": [
                spec_container("fixdatadir", "community-operator", operator_release),
                spec_container("initconf", "community-operator", operator_release),
                spec_container("initmysql", "community-server", mysql_version),
            ],
            "containers": [
                spec_container("mysql", "community-server", mysql_version),
                spec_container("sidecar", "community-operator", operator_release),
            ],
        },
        "status": {
            "initContainerStatuses": [
                status_container("fixdatadir"),
                status_container("initconf"),
                status_container("initmysql"),
            ],
            "containerStatuses": [
                status_container("mysql"),
                status_container("sidecar"),
            ],
        },
    }
    router_pod_name = f"{cluster_name}-router-abc"
    router_pod = {
        "metadata": {
            "namespace": namespace,
            "name": router_pod_name,
        },
        "spec": {
            "containers": [
                spec_container("router", "community-router", mysql_version),
            ],
        },
        "status": {
            "containerStatuses": [
                status_container("router"),
            ],
        },
    }
    pods = {
        (namespace, f"{cluster_name}-0"): server_pod,
        (namespace, router_pod_name): router_pod,
    }

    def get_po(pod_namespace, pod_name):
        return pods[(pod_namespace, pod_name)]

    def ls_po(pod_namespace, pattern):
        assert pod_namespace == namespace
        assert pattern == f"{cluster_name}-router-.*"
        return [{"NAME": router_pod_name}]

    monkeypatch.setattr(operator_t_module.kutil, "get_po", get_po)
    monkeypatch.setattr(operator_t_module.kutil, "ls_po", ls_po)

    with pytest.raises(AssertionError, match="Unexpected image tag"):
        testcase._assert_helm_cluster_runtime_image_identities(
            namespace=namespace,
            cluster_name=cluster_name,
            server_instances=1,
            router_instances=1,
            expected_release=cluster_release,
            expected_operator_release=cluster_release,
        )

    testcase._assert_helm_cluster_runtime_image_identities(
        namespace=namespace,
        cluster_name=cluster_name,
        server_instances=1,
        router_instances=1,
        expected_release=cluster_release,
        expected_operator_release=operator_release,
    )


def test_extract_runtime_image_digest_supports_docker_pullable(operator_t_module):
    assert (
        operator_t_module.OperatorSingleAndMultipleBaseTest._extract_runtime_image_digest(
            "docker-pullable://example/mysql/community-operator@sha256:cd424d7c5ec40d95fbaccfa448cfca6d7345235e51647209439b8665307620be"
        )
        == "sha256:cd424d7c5ec40d95fbaccfa448cfca6d7345235e51647209439b8665307620be"
    )


def test_extract_runtime_image_digest_supports_containerd(operator_t_module):
    assert (
        operator_t_module.OperatorSingleAndMultipleBaseTest._extract_runtime_image_digest(
            "containerd://sha256:51a02cee332642951edfb2b3244d3396c016d47e0bb52ae780677ba96c42c6bd"
        )
        == "sha256:51a02cee332642951edfb2b3244d3396c016d47e0bb52ae780677ba96c42c6bd"
    )
