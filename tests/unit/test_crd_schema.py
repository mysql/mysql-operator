# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import pathlib

import pytest
import yaml


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
CRD_PATHS = [
    REPO_ROOT / "deploy" / "deploy-crds.yaml",
    REPO_ROOT / "helm" / "mysql-operator" / "crds" / "crd.yaml",
]


def _load_innodbcluster_resource_schema(crd_path: pathlib.Path) -> dict:
    for doc in yaml.safe_load_all(crd_path.read_text(encoding="utf8")):
        if (
            doc
            and doc.get("kind") == "CustomResourceDefinition"
            and doc.get("metadata", {}).get("name")
            == "innodbclusters.mysql.oracle.com"
        ):
            return doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"][
                "properties"
            ]["spec"]

    raise AssertionError(f"InnoDBCluster CRD not found in {crd_path}")


def _logs_schema(crd_path: pathlib.Path) -> dict:
    return _load_innodbcluster_resource_schema(crd_path)["properties"]["logs"]


@pytest.mark.parametrize("crd_path", CRD_PATHS)
def test_logs_collector_schema_requires_fluentd_collector_and_sinks(
    crd_path,
) -> None:
    logs_schema = _logs_schema(crd_path)

    collector = logs_schema["properties"]["collector"]
    assert collector["oneOf"] == [{"required": ["image", "fluentd"]}]

    fluentd = collector["properties"]["fluentd"]
    assert fluentd["oneOf"] == [{"required": ["sinks"]}]

    sink_items = fluentd["properties"]["sinks"]["items"]
    assert sink_items["required"] == ["name", "rawConfig"]
    assert set(sink_items["properties"]) == {"name", "rawConfig"}


@pytest.mark.parametrize("crd_path", CRD_PATHS)
def test_logs_schema_constrains_error_verbosity_and_long_query_time(
    crd_path,
) -> None:
    logs_schema = _logs_schema(crd_path)

    verbosity = logs_schema["properties"]["error"]["properties"]["verbosity"]
    assert verbosity["type"] == "integer"
    assert verbosity["minimum"] == 1
    assert verbosity["maximum"] == 3

    long_query_time = logs_schema["properties"]["slowQuery"]["properties"][
        "longQueryTime"
    ]
    assert long_query_time["type"] == "number"
    assert long_query_time["minimum"] == 0


@pytest.mark.parametrize("crd_path", CRD_PATHS)
@pytest.mark.parametrize(
    ("section", "required", "properties"),
    [
        (
            "annotations",
            ["fieldName", "annotationName"],
            {"fieldName", "annotationName"},
        ),
        ("labels", ["fieldName", "labelName"], {"fieldName", "labelName"}),
        ("podFields", ["fieldName", "fieldPath"], {"fieldName", "fieldPath"}),
        (
            "resourceFields",
            ["fieldName", "containerName", "resource"],
            {"fieldName", "containerName", "resource"},
        ),
        (
            "staticFields",
            ["fieldName", "fieldValue"],
            {"fieldName", "fieldValue"},
        ),
    ],
)
def test_logs_record_augmentation_schema_uses_canonical_field_names(
    crd_path,
    section,
    required,
    properties,
) -> None:
    fluentd = _logs_schema(crd_path)["properties"]["collector"]["properties"][
        "fluentd"
    ]
    record_augmentation = fluentd["properties"]["recordAugmentation"]
    item_schema = record_augmentation["properties"][section]["items"]

    assert item_schema["required"] == required
    assert set(item_schema["properties"]) == properties
