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
    generation: int | None = None,
):
    deployment_labels = {
        "app.kubernetes.io/name": "mysql-operator",
        "app.kubernetes.io/component": "controller",
    }
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


def test_topology_env_uses_canonical_namespace_set():
    topology = OperatorTopology.from_env(" ns2, ns1 , ns2 ", True)

    assert topology.scope == "scoped"
    assert topology.standalone is True
    assert topology.namespaces == ("ns1", "ns2")
    assert topology.describe() == "scoped standalone watching [ns1, ns2]"


def test_resolve_env_global_generation_one_is_install():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": ""},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
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
        source="env-install",
    )


def test_resolve_env_scoped_generation_greater_than_one_is_upgrade():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-b,team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "false"},
        ],
        generation=2,
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
        source="env-upgrade",
    )


def test_resolve_env_standalone_generation_greater_than_one_is_upgrade():
    deployment = _make_modern_operator_deployment(
        env=[
            {"name": "OPERATOR_NAMESPACES", "value": "team-a"},
            {"name": "OPERATOR_STANDALONE", "value": "true"},
        ],
        generation=3,
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
        source="env-upgrade",
    )


def test_resolve_legacy_global_without_env_uses_default_global_topology():
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
        source="legacy-global",
    )


def test_resolve_typed_legacy_global_without_env_uses_default_global_topology():
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
        source="legacy-global",
    )


@pytest.mark.parametrize(
    "env",
    [
        [{"name": "OPERATOR_NAMESPACES", "value": ""}],
        [{"name": "OPERATOR_STANDALONE", "value": "false"}],
    ],
)
def test_resolve_incomplete_topology_env_fails(env: list[dict]):
    deployment = _make_modern_operator_deployment(env=env, generation=1)

    with pytest.raises(
        OperatorTopologyError,
        match=r"incomplete topology env configuration",
    ):
        resolve_operator_deployment_topology(deployment)


def test_resolve_modern_operator_without_topology_env_fails():
    deployment = _make_modern_operator_deployment(generation=1)

    with pytest.raises(
        OperatorTopologyError,
        match=r"has no topology env configuration",
    ):
        resolve_operator_deployment_topology(deployment)


def test_global_topology_overlaps_everything_and_scoped_overlap_is_sorted():
    global_topology = OperatorTopology.from_env("", False)
    scoped_topology = OperatorTopology.from_env("team-b,team-a,team-b", True)
    other_scoped_topology = OperatorTopology.from_env("team-c,team-a", False)

    assert global_topology.overlaps(scoped_topology) is True
    assert scoped_topology.overlaps(other_scoped_topology) is True
    assert scoped_topology.overlapping_namespaces(other_scoped_topology) == ("team-a",)
