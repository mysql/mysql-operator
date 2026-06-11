# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging

from mysqloperator.controller.innodbcluster.cluster_api import InnoDBClusterSpec
from mysqloperator.controller.innodbcluster import cluster_objects, router_objects


def _make_spec(extra=None):
    spec = {
        "secretName": "mypwds",
        "instances": 1,
        "tlsUseSelfSigned": True,
    }
    if extra:
        spec.update(extra)
    return InnoDBClusterSpec("test-ns", "cluster-a", spec)


class _FakeCluster:
    name = "cluster-a"
    namespace = "test-ns"

    def __init__(self, spec):
        self.parsed_spec = spec

    def get_ca_and_tls(self):
        return {}

    def router_tls_exists(self):
        return False


def _container_by_name(pod_spec, name):
    return next(container for container in pod_spec["containers"]
                if container["name"] == name)


def test_service_account_uses_default_name_and_renders_image_pull_secrets():
    spec = _make_spec({
        "imagePullSecrets": [
            {"name": "private-registry"},
            {"name": "backup-registry"},
        ],
    })

    service_account = cluster_objects.prepare_service_account_sidecar(spec)
    patch = cluster_objects.prepare_service_account_patch_for_image_pull_secrets(spec)

    expected = [
        {"name": "private-registry"},
        {"name": "backup-registry"},
    ]
    assert service_account["metadata"]["name"] == "cluster-a-sidecar-sa"
    assert service_account["imagePullSecrets"] == expected
    assert patch == {"imagePullSecrets": expected}


def test_service_account_uses_default_name_and_omits_empty_image_pull_secrets():
    spec = _make_spec()

    service_account = cluster_objects.prepare_service_account_sidecar(spec)
    patch = cluster_objects.prepare_service_account_patch_for_image_pull_secrets(spec)

    assert service_account["metadata"]["name"] == "cluster-a-sidecar-sa"
    assert "imagePullSecrets" not in service_account
    assert patch is None


def test_service_account_uses_custom_name_and_renders_image_pull_secrets():
    spec = _make_spec({
        "serviceAccountName": "custom-sidecar-sa",
        "imagePullSecrets": [
            {"name": "private-registry"},
        ],
    })

    service_account = cluster_objects.prepare_service_account_sidecar(spec)

    assert service_account["metadata"]["name"] == "custom-sidecar-sa"
    assert service_account["imagePullSecrets"] == [{"name": "private-registry"}]


def test_service_account_uses_custom_name_and_omits_empty_image_pull_secrets():
    spec = _make_spec({
        "serviceAccountName": "custom-sidecar-sa",
    })

    service_account = cluster_objects.prepare_service_account_sidecar(spec)

    assert service_account["metadata"]["name"] == "custom-sidecar-sa"
    assert "imagePullSecrets" not in service_account


def test_cluster_stateful_set_renders_pod_metadata_and_pod_spec(monkeypatch):
    monkeypatch.setattr(
        cluster_objects,
        "k8s_cluster_domain",
        lambda logger: "cluster.local",
    )
    spec = _make_spec({
        "podLabels": {
            "server-label": "server-label-value",
        },
        "podAnnotations": {
            "server.example.com/ann": "server-ann-value",
        },
        "podSpec": {
            "nodeSelector": {
                "pool": "mysql",
            },
            "containers": [
                {
                    "name": "mysql",
                    "resources": {
                        "requests": {
                            "cpu": "250m",
                            "memory": "512Mi",
                        },
                    },
                },
            ],
        },
    })
    cluster = _FakeCluster(spec)

    stateful_set = cluster_objects.prepare_cluster_stateful_set(
        cluster, spec, logging.getLogger(__name__))
    template = stateful_set["spec"]["template"]

    assert template["metadata"]["labels"]["server-label"] == "server-label-value"
    assert (
        template["metadata"]["annotations"]["server.example.com/ann"]
        == "server-ann-value"
    )
    assert template["spec"]["nodeSelector"] == {"pool": "mysql"}
    assert _container_by_name(template["spec"], "mysql")["resources"] == {
        "requests": {
            "cpu": "250m",
            "memory": "512Mi",
        },
    }


def test_read_replica_stateful_set_renders_pod_metadata_and_selector(monkeypatch):
    monkeypatch.setattr(
        cluster_objects,
        "k8s_cluster_domain",
        lambda logger: "cluster.local",
    )
    spec = _make_spec({
        "readReplicas": [
            {
                "name": "trr",
                "instances": 1,
                "baseServerId": 500,
                "podLabels": {
                    "rr-label": "rr-label-value",
                },
                "podAnnotations": {
                    "rr.example.com/ann": "rr-ann-value",
                },
            },
        ],
    })
    cluster = _FakeCluster(spec)
    read_replica = spec.get_read_replica("trr")

    stateful_set = cluster_objects.prepare_cluster_stateful_set(
        cluster, read_replica, logging.getLogger(__name__))
    template = stateful_set["spec"]["template"]

    assert stateful_set["metadata"]["name"] == "cluster-a-trr"
    assert stateful_set["spec"]["serviceName"] == "cluster-a-trr-instances"
    expected_match_labels = {
        "component": "mysqld",
        "tier": "mysql",
        "mysql.oracle.com/cluster": "cluster-a",
        "mysql.oracle.com/instance-type": "read-replica",
        "mysql.oracle.com/read-replica": "cluster-a-trr",
    }
    match_labels = stateful_set["spec"]["selector"]["matchLabels"]
    for key, value in expected_match_labels.items():
        assert match_labels[key] == value
    assert template["metadata"]["labels"]["rr-label"] == "rr-label-value"
    assert template["metadata"]["labels"]["mysql.oracle.com/read-replica"] == "cluster-a-trr"
    assert (
        template["metadata"]["annotations"]["rr.example.com/ann"]
        == "rr-ann-value"
    )


def test_cluster_services_render_labels_and_annotations():
    spec = _make_spec({
        "service": {
            "labels": {
                "svc-label": "svc-label-value",
            },
            "annotations": {
                "svc.example.com/ann": "svc-ann-value",
            },
        },
    })

    router_service = router_objects.prepare_router_service(spec)

    assert router_service["metadata"]["labels"]["svc-label"] == "svc-label-value"
    assert (
        router_service["metadata"]["annotations"]["svc.example.com/ann"]
        == "svc-ann-value"
    )
    assert router_service["spec"]["selector"] == {
        "component": "mysqlrouter",
        "tier": "mysql",
        "mysql.oracle.com/cluster": "cluster-a",
    }


def test_instance_service_renders_labels_and_annotations():
    spec = _make_spec({
        "instanceService": {
            "labels": {
                "instance-label": "instance-label-value",
            },
            "annotations": {
                "instance.example.com/ann": "instance-ann-value",
            },
        },
    })

    service = cluster_objects.prepare_cluster_service(
        spec, logging.getLogger(__name__))

    assert service["metadata"]["labels"]["instance-label"] == "instance-label-value"
    assert (
        service["metadata"]["annotations"]["instance.example.com/ann"]
        == "instance-ann-value"
    )
    assert service["spec"]["selector"] == {
        "component": "mysqld",
        "tier": "mysql",
        "mysql.oracle.com/cluster": "cluster-a",
        "mysql.oracle.com/instance-type": "group-member",
    }


def test_read_replica_service_renders_read_replica_selector():
    spec = _make_spec({
        "readReplicas": [
            {
                "name": "trr",
                "instances": 1,
                "baseServerId": 500,
            },
        ],
    })
    read_replica = spec.get_read_replica("trr")

    service = cluster_objects.prepare_cluster_service(
        read_replica, logging.getLogger(__name__))

    assert service["metadata"]["name"] == "cluster-a-trr-instances"
    assert service["metadata"]["labels"]["mysql.oracle.com/read-replica"] == "cluster-a-trr"
    assert service["spec"]["selector"] == {
        "component": "mysqld",
        "tier": "mysql",
        "mysql.oracle.com/cluster": "cluster-a",
        "mysql.oracle.com/instance-type": "read-replica",
        "mysql.oracle.com/read-replica": "cluster-a-trr",
    }


def test_remove_read_replica_deletes_objects_for_raw_read_replica_spec(monkeypatch):
    deleted = []
    cluster = _FakeCluster(_make_spec())

    monkeypatch.setattr(
        cluster_objects.api_core,
        "delete_namespaced_config_map",
        lambda name, namespace: deleted.append(("configmap", namespace, name)),
    )
    monkeypatch.setattr(
        cluster_objects.api_core,
        "delete_namespaced_service",
        lambda name, namespace: deleted.append(("service", namespace, name)),
    )
    monkeypatch.setattr(
        cluster_objects.api_apps,
        "delete_namespaced_stateful_set",
        lambda name, namespace: deleted.append(("statefulset", namespace, name)),
    )

    cluster_objects.remove_read_replica(cluster, {"name": "trr"})

    assert deleted == [
        ("configmap", "test-ns", "cluster-a-trr-initconf"),
        ("service", "test-ns", "cluster-a-trr-instances"),
        ("statefulset", "test-ns", "cluster-a-trr"),
    ]


def test_router_deployment_renders_pod_metadata_and_pod_spec(monkeypatch):
    monkeypatch.setattr(
        router_objects.fqdn,
        "idc_service_fqdn",
        lambda cluster, logger: "cluster-a-instances.test-ns.svc.cluster.local",
    )
    spec = _make_spec({
        "router": {
            "instances": 1,
            "podLabels": {
                "router-label": "router-label-value",
            },
            "podAnnotations": {
                "router.example.com/ann": "router-ann-value",
            },
            "podSpec": {
                "nodeSelector": {
                    "pool": "router",
                },
                "containers": [
                    {
                        "name": "router",
                        "resources": {
                            "requests": {
                                "cpu": "100m",
                                "memory": "128Mi",
                            },
                        },
                    },
                ],
            },
        },
    })
    cluster = _FakeCluster(spec)

    deployment = router_objects.prepare_router_deployment(
        cluster, logging.getLogger(__name__))
    template = deployment["spec"]["template"]

    assert template["metadata"]["labels"]["router-label"] == "router-label-value"
    assert (
        template["metadata"]["annotations"]["router.example.com/ann"]
        == "router-ann-value"
    )
    assert template["spec"]["nodeSelector"] == {"pool": "router"}
    assert _container_by_name(template["spec"], "router")["resources"] == {
        "requests": {
            "cpu": "100m",
            "memory": "128Mi",
        },
    }
