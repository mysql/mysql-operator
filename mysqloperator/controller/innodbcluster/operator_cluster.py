# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA


from typing import Any, Optional
from kopf.structs.bodies import Body
from kubernetes.client.rest import ApiException

from mysqloperator.controller.api_utils import ApiSpecError
from .. import consts, kubeutils, config, utils, errors, diagnose
from .. import shellutils
from ..group_monitor import g_group_monitor
from ..utils import g_ephemeral_pod_state
from ..kubeutils import api_core, api_apps
from ..backup import backup_objects
from .cluster_controller import ClusterController, ClusterMutex
from . import cluster_objects, router_objects, cluster_api
from .cluster_api import InnoDBCluster, InnoDBClusterSpec, MySQLPod
import kopf
from logging import Logger
import time


# TODO check whether we should store versions in status to make upgrade easier


def on_group_view_change(cluster: InnoDBCluster, members: list, view_id_changed: bool) -> None:
    """
    Triggered from the GroupMonitor whenever the membership view changes.
    This handler should react to changes that wouldn't be noticed by regular
    pod and cluster events.
    It also updates cluster status in the pods and cluster objects.
    """

    c = ClusterController(cluster)
    c.on_group_view_change(members, view_id_changed)


def monitor_existing_clusters(logger: Logger) -> None:
    clusters = cluster_api.get_all_clusters()
    for cluster in clusters:
        if cluster.get_create_time():
            g_group_monitor.monitor_cluster(
                cluster, on_group_view_change, logger)


@kopf.on.create(consts.GROUP, consts.VERSION,
                consts.INNODBCLUSTER_PLURAL)  # type: ignore
def on_innodbcluster_create(name: str, namespace: Optional[str], body: Body,
                            logger: Logger, **kwargs) -> None:
    logger.info(
        f"Initializing InnoDB Cluster name={name} namespace={namespace}")

    cluster = InnoDBCluster(body)

    try:
        cluster.parse_spec()
        cluster.parsed_spec.validate(logger)
    except ApiSpecError as e:
        cluster.error(action="CreateCluster",
                      reason="InvalidArgument", message=str(e))
        raise kopf.TemporaryError(f"Error in InnoDBCluster spec: {e}")

    icspec = cluster.parsed_spec

    def ignore_404(f) -> Any:
        try:
            return f()
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    if not cluster.get_create_time():
        try:
            if not ignore_404(cluster.get_initconf):
                configs = cluster_objects.prepare_initconf(icspec)
                kopf.adopt(configs)
                api_core.create_namespaced_config_map(namespace, configs)

            if not ignore_404(cluster.get_private_secrets):
                secret = cluster_objects.prepare_secrets(icspec)
                kopf.adopt(secret)
                api_core.create_namespaced_secret(
                    namespace=namespace, body=secret)

            if not ignore_404(cluster.get_router_account):
                secret = router_objects.prepare_router_secrets(icspec)
                kopf.adopt(secret)
                api_core.create_namespaced_secret(
                    namespace=namespace, body=secret)

            if not ignore_404(cluster.get_service):
                service = cluster_objects.prepare_cluster_service(icspec)
                kopf.adopt(service)
                api_core.create_namespaced_service(
                    namespace=namespace, body=service)

            if not ignore_404(cluster.get_stateful_set):
                statefulset = cluster_objects.prepare_cluster_stateful_set(
                    icspec)
                kopf.adopt(statefulset)
                api_apps.create_namespaced_stateful_set(
                    namespace=namespace, body=statefulset)

            if not ignore_404(cluster.get_router_service):
                router_service = router_objects.prepare_router_service(icspec)
                kopf.adopt(router_service)
                api_core.create_namespaced_service(
                    namespace=namespace, body=router_service)

            if not ignore_404(cluster.get_router_replica_set):
                if icspec.router.instances > 0:
                    router_replicaset = router_objects.prepare_router_replica_set(
                        icspec)
                    kopf.adopt(router_replicaset)
                    api_apps.create_namespaced_replica_set(
                        namespace=namespace, body=router_replicaset)

            if not ignore_404(cluster.get_backup_account):
                secret = backup_objects.prepare_backup_secrets(icspec)
                kopf.adopt(secret)
                api_core.create_namespaced_secret(
                    namespace=namespace, body=secret)
        except Exception as e:
            cluster.warn(action="CreateCluster", reason="CreateResourceFailed",
                         message=f"{e}")
            raise

        cluster.info(action="CreateCluster", reason="ResourcesCreated",
                     message="Dependency resources created, switching status to PENDING")
        cluster.set_status({
            "cluster": {
                "status":  diagnose.ClusterDiagStatus.PENDING.value,
                "onlineInstances": 0,
                "lastProbeTime": utils.isotime()
            }})


@kopf.on.delete(consts.GROUP, consts.VERSION,
                consts.INNODBCLUSTER_PLURAL)  # type: ignore
def on_innodbcluster_delete(name: str, namespace: str, body: Body,
                            logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    logger.info(f"Deleting cluster {name}")

    g_group_monitor.remove_cluster(cluster)

    # Scale down routers to 0
    logger.info(f"Updating Router ReplicaSet.replicas to 0")
    router_objects.update_size(cluster, 0, logger)

    # Scale down the cluster to 0
    sts = cluster.get_stateful_set()
    if sts:
        logger.info(f"Updating InnoDB Cluster StatefulSet.instances to 0")
        cluster_objects.update_stateful_set_spec(
            sts, {"spec": {"replicas": 0}})


# TODO add a busy state and prevent changes while on it

@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.instances")  # type: ignore
def on_innodbcluster_field_instances(old, new, body: Body,
                                     logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring spec.instances change for unready cluster")
        return

    # TODO - identify what cluster statuses should allow changes to the size of the cluster

    sts = cluster.get_stateful_set()
    if sts and old != new:
        logger.info(
            f"Updating InnoDB Cluster StatefulSet.replicas from {old} to {new}")
        cluster.parsed_spec.validate(logger)

        cluster_objects.update_stateful_set_spec(
            sts, {"spec": {"replicas": new}})


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.version")  # type: ignore
def on_innodbcluster_field_version(old, new, body: Body,
                                   logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring spec.version change for unready cluster")
        return

    # TODO - identify what cluster statuses should allow this change

    sts = cluster.get_stateful_set()
    if sts and old != new:
        logger.info(
            f"Propagating spec.version={new} for {cluster.namespace}/{cluster.name} (was {old})")

        cluster.parse_spec()

        cluster_ctl = ClusterController(cluster)

        try:
            cluster_ctl.on_upgrade(new)
        except:
            # revert version in the spec
            raise

        cluster_objects.update_mysql_image(sts, cluster.parsed_spec)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.imageRepository")  # type: ignore
def on_innodbcluster_field_image_repository(old, new, body: Body,
                                            logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    sts = cluster.get_stateful_set()
    if sts and old != new:
        logger.info(
            f"Propagating spec.imageRepository={new} for {cluster.namespace}/{cluster.name} (was {old})")

        cluster.parse_spec()

        cluster_objects.update_mysql_image(sts, cluster.parsed_spec)
        cluster_objects.update_shell_image(sts, cluster.parsed_spec)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.image")  # type: ignore
def on_innodbcluster_field_image(old, new, body: Body,
                                 logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring spec.image change for unready cluster")
        return

    # TODO - identify what cluster statuses should allow this change

    sts = cluster.get_stateful_set()
    if sts and old != new:
        logger.info(
            f"Updating MySQL image for InnoDB Cluster StatefulSet pod template from {old} to {new}")
        cluster.parsed_spec.validate(logger)

        cluster_ctl = ClusterController(cluster)

        try:
            cluster_ctl.on_upgrade(new)
        except:
            # revert version in the spec
            raise

        cluster_objects.update_mysql_image(sts, cluster.parsed_spec)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.instances")  # type: ignore
def on_innodbcluster_field_router_instances(old, new, body: Body,
                                            logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.get_create_time():
        logger.debug(
            f"Ignoring spec.router.instances change for unready cluster")
        return

    with ClusterMutex(cluster):
        logger.info(f"Updating Router ReplicaSet.replicas from {old} to {new}")
        cluster.parsed_spec.validate(logger)

        router_objects.update_size(cluster, new, logger)


@kopf.on.create("", "v1", "pods",
                labels={"component": "mysqld"})  # type: ignore
def on_pod_create(body: Body, logger: Logger, **kwargs):
    """
    Handle MySQL server Pod creation, which can happen when:
    - cluster is being first created
    - cluster is being scaled up (more members added)
    """

    # TODO ensure that the pod is owned by us
    pod = MySQLPod.from_json(body)

    # check general assumption
    assert not pod.deleting

    ready = pod.check_containers_ready()
    logger.info(f"POD CREATED: pod={pod.name} containers_ready={ready}")
    if not ready:
        # TODO add extra diagnostics about why the pod is not ready yet, for
        # example, unbound volume claims, initconf not finished etc
        raise kopf.TemporaryError(f"{pod.name} is not ready yet", delay=10)

    cluster = pod.get_cluster()
    logger.info(f"CLUSTER DELETING={cluster.deleting}")

    assert cluster

    with ClusterMutex(cluster, pod):
        if pod.index == 0 and not cluster.get_create_time():
            cluster_objects.on_first_cluster_pod_created(cluster, logger)

            g_group_monitor.monitor_cluster(
                cluster, on_group_view_change, logger)

        cluster_ctl = ClusterController(cluster)

        cluster_ctl.on_pod_created(pod, logger)

        # Remember how many restarts happened as of now
        g_ephemeral_pod_state.set(
            pod, "mysql-restarts", pod.get_container_restarts("mysql"))


@kopf.on.event("", "v1", "pods",
               labels={"component": "mysqld"})  # type: ignore
def on_pod_event(event, body: Body, logger: Logger, **kwargs):
    """
    Handle low-level MySQL server pod events. The events we're interested in are:
    - when a container restarts in a Pod (e.g. because of mysqld crash)
    """
    # TODO ensure that the pod is owned by us

    while True:
        try:
            pod = MySQLPod.from_json(body)

            member_info = pod.get_membership_info()
            ready = pod.check_containers_ready()
            if pod.phase != "Running" or pod.deleting or not member_info:
                logger.debug(
                    f"ignored pod event: pod={pod.name} containers_ready={ready} deleting={pod.deleting} phase={pod.phase} member_info={member_info}")
                return

            mysql_restarts = pod.get_container_restarts("mysql")

            event = ""
            if g_ephemeral_pod_state.get(pod, "mysql-restarts") != mysql_restarts:
                event = "mysql-restarted"

            containers = [
                f"{c.name}={'ready' if c.ready else 'not-ready'}" for c in pod.status.container_statuses]
            conditions = [
                f"{c.type}={c.status}" for c in pod.status.conditions]
            logger.info(f"POD EVENT {event}: pod={pod.name} containers_ready={ready} deleting={pod.deleting} phase={pod.phase} member_info={member_info} restarts={mysql_restarts} containers={containers} conditions={conditions}")

            cluster = pod.get_cluster()
            if not cluster:
                logger.info(
                    f"Ignoring event for pod {pod.name} belonging to a deleted cluster")
                return
            with ClusterMutex(cluster, pod):
                cluster_ctl = ClusterController(cluster)

                # Check if a container in the pod restarted
                if ready and event == "mysql-restarted":
                    cluster_ctl.on_pod_restarted(pod, logger)

                    g_ephemeral_pod_state.set(
                        pod, "mysql-restarts", mysql_restarts)

                # Check if we should refresh the cluster status
                status = cluster_ctl.probe_status_if_needed(pod, logger)
                if status == diagnose.ClusterDiagStatus.UNKNOWN:
                    raise kopf.TemporaryError(
                        f"Cluster has unreachable members. status={status}", delay=15)
                break
        except kopf.TemporaryError as e:
            # TODO review this
            # Manually handle retries, the event handler isn't getting called again
            # by kopf (maybe a bug or maybe we're using it wrong)
            logger.info(f"{e}: retrying after {e.delay} seconds")
            if e.delay:
                time.sleep(e.delay)
            continue


@kopf.on.delete("", "v1", "pods",
                labels={"component": "mysqld"})  # type: ignore
def on_pod_delete(body: Body, logger: Logger, **kwargs):
    """
    Handle MySQL server Pod deletion, which can happen when:
    - cluster is being scaled down (members being removed)
    - cluster is being deleted
    - user deletes a pod by hand
    """
    # TODO ensure that the pod is owned by us
    pod = MySQLPod.from_json(body)

    # check general assumption
    assert pod.deleting

    # removeInstance the pod
    cluster = pod.get_cluster()

    if cluster:
        with ClusterMutex(cluster, pod):
            cluster_ctl = ClusterController(cluster)

            cluster_ctl.on_pod_deleted(pod, body, logger)

            if pod.index == 0 and cluster.deleting:
                cluster_objects.on_last_cluster_pod_removed(cluster, logger)
    else:
        pod.remove_member_finalizer(body)

        logger.error(f"Owner cluster for {pod.name} does not exist anymore")
