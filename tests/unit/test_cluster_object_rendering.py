# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from mysqloperator.controller.innodbcluster.cluster_api import InnoDBClusterSpec
from mysqloperator.controller.innodbcluster import cluster_objects, router_objects, operator_cluster
from mysqloperator.controller.backup import backup_objects


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

@pytest.mark.parametrize("edition", ["community", "enterprise"])
def test_hotfix_images_preserve_all_version_components(edition, monkeypatch):
    monkeypatch.setenv("MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN", "cluster.local")
    spec = _make_spec({"edition": edition, "version": "9.7.4.1",
                       "router": {"version": "26.10.0.1"},
                       "imageRepository": "local/mysql"})
    spec.sidecarVersion = "26.10.0.1-2.3.2"
    sts = cluster_objects.prepare_cluster_stateful_set(
        _FakeCluster(spec), spec, logging.getLogger(__name__))
    pod = sts["spec"]["template"]["spec"]
    for container in pod["containers"] + pod["initContainers"]:
        if container["name"] in ("mysql", "initmysql"):
            assert container["image"] == f"local/mysql/{edition}-server:9.7.4.1"
        elif container["name"] in ("sidecar", "initconf", "fixdatadir"):
            assert container["image"] == f"local/mysql/{edition}-operator:26.10.0.1-2.3.2"
    assert spec.router_image == f"local/mysql/{edition}-router:26.10.0.1"
    monkeypatch.setattr(backup_objects, "k8s_cluster_domain", lambda logger: "cluster.local")
    backup_spec = SimpleNamespace(
        clusterName="cluster-a", namespace="test-ns", name="backup-a",
        serviceAccountName="mysql-sidecar", operator_image=spec.operator_image,
        operator_image_pull_policy="IfNotPresent",
        backupProfile=SimpleNamespace(podAnnotations={}, podLabels={}),
        add_to_pod_spec=lambda *args: None)
    job = backup_objects.prepare_backup_job("hotfix-backup", backup_spec)
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == (
        f"local/mysql/{edition}-operator:26.10.0.1-2.3.2")

@pytest.mark.parametrize("old,migrates", [("8.2.0", True), ("8.3.0", False),
                                           ("8.4.13.1", False), ("26.10.0.1", False)])
def test_keyring_upgrade_uses_numeric_release_threshold(monkeypatch, old, migrates):
    cluster = Mock()
    monkeypatch.setattr(cluster_objects, "update_objects_for_keyring", lambda *args: None)
    cluster_objects.on_upgrade(cluster, old, "26.10.0.1", Mock(), Mock(), logging.getLogger(__name__))
    assert cluster.parsed_spec.keyring.upgrade_to_component.call_count == int(migrates)


@pytest.mark.parametrize("old,new,upgrades", [("26.10.0.9", "26.10.0.10", True),
                                               ("26.10.0.10", "26.10.0.9", False),
                                               ("26.10.0", "26.10.0.0", False)])
def test_server_version_change_updates_router_account_for_numeric_upgrade(monkeypatch, old, new, upgrades):
    cluster = Mock()
    controller = Mock()
    monkeypatch.setattr(operator_cluster, "ClusterController", lambda cluster: controller)
    monkeypatch.setattr(operator_cluster.cluster_objects, "on_upgrade", lambda *args: None)
    monkeypatch.setattr(operator_cluster.cluster_objects, "update_mysql_image", lambda *args: None)
    cluster.get_router_deployment.return_value = None
    operator_cluster.on_innodbcluster_field_version(old, new, {}, cluster, Mock(), logging.getLogger(__name__))
    assert controller.on_router_upgrade.call_count == int(upgrades)
    controller.on_server_version_change.assert_called_once_with(new)


@pytest.mark.parametrize("server,old,new,expected,refresh_account", [
    ("26.10.0", None, "26.10.0", "26.10.0", False),
    ("26.10.0", None, "26.10.0.1", "26.10.0.1", True),
    ("26.10.0", "26.10.0.1", None, "26.10.0", False),
    ("26.10.0.1", "26.10.0", None, "26.10.0.1", True),
    ("26.10.0", "26.10.0.9", "26.10.0.10", "26.10.0.10", True),
    ("26.10.0", "26.10.0.10", "26.10.0.9", "26.10.0.9", False),
    ("26.10.0", "26.10.0", "26.10.0.0", "26.10.0.0", False),
    ("26.10.0", None, "26.10.0.1-custom", "26.10.0.1-custom", True),
    ("26.10.0", "26.10.0.9-custom", "26.10.0.10-custom", "26.10.0.10-custom", True),
])
def test_router_version_change_resolves_defaults_and_patches_image(
    monkeypatch, server, old, new, expected, refresh_account,
):
    spec = _make_spec({
        "version": server,
        "router": {"version": new} if new is not None else {},
        "imageRepository": "local/mysql",
    })
    cluster = SimpleNamespace(
        parsed_spec=spec,
        validate_spec=lambda logger: None,
        get_router_deployment=lambda: SimpleNamespace(),
    )
    controller = Mock()
    monkeypatch.setattr(operator_cluster, "ClusterController", lambda cluster: controller)
    patches = []
    patcher = SimpleNamespace(patch_deploy=patches.append)

    operator_cluster.on_innodbcluster_field_router_version(
        old, new, {}, cluster, patcher, logging.getLogger(__name__))

    assert patches == [{"spec": {"template": {"spec": {"containers": [
        {"name": "router", "image": f"local/mysql/community-router:{expected}"},
    ]}}}}]
    assert controller.on_router_upgrade.call_count == int(refresh_account)
