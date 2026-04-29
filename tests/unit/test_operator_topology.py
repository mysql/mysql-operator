# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import pytest
from kubernetes.client import (
    V1Container,
    V1Deployment,
    V1DeploymentSpec,
    V1LabelSelector,
    V1ObjectMeta,
    V1PodSpec,
    V1PodTemplateSpec,
)

from mysqloperator.controller.operator_topology import (
    TOPOLOGY_ANNOTATION,
    OperatorTopology,
    OperatorTopologyError,
    ResolvedOperatorDeploymentTopology,
    resolve_operator_deployment_topology,
)


def _make_modern_operator_deployment(
    *,
    name: str = "custom-operator",
    namespace: str = "operator-ns",
    annotations: dict | None = None,
    env: list[dict] | None = None,
    labels: dict | None = None,
    raw_manifest: bool = False,
    generation: int | None = None,
):
    deployment_labels = {
        "app.kubernetes.io/name": "mysql-operator",
        "app.kubernetes.io/component": "controller",
    }
    if raw_manifest:
        deployment_labels.update(
            {
                "app.kubernetes.io/managed-by": "mysql-operator",
                "app.kubernetes.io/created-by": "mysql-operator",
            }
        )
    if labels:
        deployment_labels.update(labels)

    metadata = {
        "name": name,
        "namespace": namespace,
        "annotations": annotations or {},
        "labels": deployment_labels,
    }
    if generation is not None:
        metadata["generation"] = generation

    return {
        "metadata": metadata,
        "spec": {
            "selector": {
                "matchLabels": {
                    "name": name,
                    "app.kubernetes.io/name": "mysql-operator",
                    "app.kubernetes.io/component": "controller",
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "name": name,
                        "app.kubernetes.io/name": "mysql-operator",
                        "app.kubernetes.io/component": "controller",
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                            "env": env or [],
                        }
                    ]
                },
            },
        },
    }


def _make_legacy_global_operator_deployment():
    return {
        "metadata": {
            "name": "mysql-operator",
            "namespace": "mysql-operator",
            "annotations": {},
            "labels": {
                "app.kubernetes.io/name": "mysql-operator",
                "app.kubernetes.io/component": "controller",
            },
        },
        "spec": {
            "selector": {
                "matchLabels": {
                    "name": "mysql-operator",
                }
            },
            "template": {
                "metadata": {
                    "labels": {
                        "name": "mysql-operator",
                    }
                },
                "spec": {
                    "containers": [
                        {
                            "name": "mysql-operator",
                            "env": [],
                        }
                    ]
                },
            },
        },
    }


def _make_typed_legacy_global_operator_deployment():
    return V1Deployment(
        metadata=V1ObjectMeta(
            name="mysql-operator",
            namespace="mysql-operator",
            annotations={},
            labels={"name": "mysql-operator"},
        ),
        spec=V1DeploymentSpec(
            selector=V1LabelSelector(
                match_labels={"name": "mysql-operator"},
            ),
            template=V1PodTemplateSpec(
                metadata=V1ObjectMeta(
                    labels={"name": "mysql-operator"},
                ),
                spec=V1PodSpec(
                    containers=[V1Container(name="mysql-operator", env=[])],
                ),
            ),
        ),
    )


def test_topology_annotation_round_trip_uses_canonical_namespace_set():
    topology = OperatorTopology.from_env(" ns2, ns1 , ns2 ", True)

    assert topology.scope == "scoped"
    assert topology.standalone is True
    assert topology.namespaces == ("ns1", "ns2")
    assert topology.to_annotation_value() == (
        '{"version":1,"scope":"scoped","standalone":true,"namespaces":["ns1","ns2"]}'
    )


def test_resolve_deployment_uses_persisted_annotation_when_present():
    persisted_topology = OperatorTopology.from_env("ns-b,ns-a", False)
    deployment = _make_modern_operator_deployment(
        annotations={
            TOPOLOGY_ANNOTATION: persisted_topology.to_annotation_value(),
        },
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "ignored"},
            {"name": "OPERATOR_STANDALONE", "value": "true"},
        ],
    )

    resolved = resolve_operator_deployment_topology(deployment)

    assert resolved == ResolvedOperatorDeploymentTopology(
        namespace="operator-ns",
        name="custom-operator",
        topology=OperatorTopology(
            scope="scoped",
            standalone=False,
            namespaces=("ns-a", "ns-b"),
        ),
        needs_bootstrap_annotation=False,
        source="annotation",
    )


def test_resolve_empty_persisted_annotation_fails_without_env_fallback():
    deployment = _make_modern_operator_deployment(
        annotations={
            TOPOLOGY_ANNOTATION: "",
        },
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-b,team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        raw_manifest=True,
        generation=1,
    )

    with pytest.raises(
        OperatorTopologyError,
        match=r"has invalid mysql\.oracle\.com/operator-topology: "
        r"mysql\.oracle\.com/operator-topology must contain valid JSON",
    ):
        resolve_operator_deployment_topology(deployment)


def test_resolve_initial_raw_global_without_annotation_requests_bootstrap():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": ""},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        raw_manifest=True,
        generation=1,
    )

    resolved = resolve_operator_deployment_topology(deployment)

    assert resolved == ResolvedOperatorDeploymentTopology(
        namespace="operator-ns",
        name="custom-operator",
        topology=OperatorTopology(
            scope="global",
            standalone=False,
            namespaces=(),
        ),
        needs_bootstrap_annotation=True,
        source="initial-raw-env",
    )


def test_resolve_initial_raw_scoped_without_annotation_requests_bootstrap():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-b,team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        raw_manifest=True,
        generation=1,
    )

    resolved = resolve_operator_deployment_topology(deployment)

    assert resolved == ResolvedOperatorDeploymentTopology(
        namespace="operator-ns",
        name="custom-operator",
        topology=OperatorTopology(
            scope="scoped",
            standalone=False,
            namespaces=("team-a", "team-b"),
        ),
        needs_bootstrap_annotation=True,
        source="initial-raw-env",
    )


def test_resolve_initial_raw_standalone_without_annotation_requests_bootstrap():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "true"},
        ],
        raw_manifest=True,
        generation=1,
    )

    resolved = resolve_operator_deployment_topology(deployment)

    assert resolved == ResolvedOperatorDeploymentTopology(
        namespace="operator-ns",
        name="custom-operator",
        topology=OperatorTopology(
            scope="scoped",
            standalone=True,
            namespaces=("team-a",),
        ),
        needs_bootstrap_annotation=True,
        source="initial-raw-env",
    )


def test_resolve_legacy_global_without_annotation_requests_bootstrap():
    resolved = resolve_operator_deployment_topology(
        _make_legacy_global_operator_deployment()
    )

    assert resolved == ResolvedOperatorDeploymentTopology(
        namespace="mysql-operator",
        name="mysql-operator",
        topology=OperatorTopology(
            scope="global",
            standalone=False,
            namespaces=(),
        ),
        needs_bootstrap_annotation=True,
        source="legacy-global",
    )


def test_resolve_typed_legacy_global_without_annotation_requests_bootstrap():
    resolved = resolve_operator_deployment_topology(
        _make_typed_legacy_global_operator_deployment()
    )

    assert resolved == ResolvedOperatorDeploymentTopology(
        namespace="mysql-operator",
        name="mysql-operator",
        topology=OperatorTopology(
            scope="global",
            standalone=False,
            namespaces=(),
        ),
        needs_bootstrap_annotation=True,
        source="legacy-global",
    )


@pytest.mark.parametrize(
    ("namespaces", "standalone"),
    [
        ("", "false"),
        ("team-a,team-b", "false"),
        ("team-a", "true"),
    ],
)
def test_resolve_missing_annotation_outside_initial_raw_install_fails_closed(
    namespaces: str,
    standalone: str,
):
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": namespaces},
            {"name": "OPERATOR_STANDALONE", "value": standalone},
        ],
        generation=1,
    )

    with pytest.raises(
        OperatorTopologyError,
        match=r"initial raw-manifest install flow",
    ):
        resolve_operator_deployment_topology(deployment)


def test_resolve_raw_missing_annotation_after_initial_install_fails_closed():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "true"},
        ],
        raw_manifest=True,
        generation=2,
    )

    with pytest.raises(
        OperatorTopologyError,
        match=r"initial raw-manifest install flow",
    ):
        resolve_operator_deployment_topology(deployment)


def test_global_topology_overlaps_everything_and_scoped_overlap_is_sorted():
    global_topology = OperatorTopology.from_env("", False)
    scoped_topology = OperatorTopology.from_env("team-b,team-a,team-b", True)
    other_scoped_topology = OperatorTopology.from_env("team-c,team-a", False)

    assert global_topology.overlaps(scoped_topology) is True
    assert scoped_topology.overlaps(other_scoped_topology) is True
    assert scoped_topology.overlapping_namespaces(other_scoped_topology) == ("team-a",)
