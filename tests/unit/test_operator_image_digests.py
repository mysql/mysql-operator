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
