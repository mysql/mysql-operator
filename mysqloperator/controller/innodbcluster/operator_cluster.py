# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


from typing import Any, List, Optional
from kopf._cogs.structs.bodies import Body
from kubernetes.client.rest import ApiException

from mysqloperator.controller.api_utils import ApiSpecError
from .. import consts, kubeutils, config, utils, errors, diagnose
from .. import shellutils
from ..group_monitor import g_group_monitor
from ..utils import g_ephemeral_pod_state
from ..kubeutils import api_core, api_apps, api_policy, api_rbac, api_customobj, api_cron_job, k8s_version
from ..backup import backup_objects
from ..config import DEFAULT_OPERATOR_VERSION_TAG
from .cluster_controller import ClusterController, ClusterMutex
from . import cluster_objects, router_objects, cluster_api
from .cluster_api import InnoDBCluster, InnoDBClusterSpec, MySQLPod, get_all_clusters
import kopf
from logging import Logger
import time


# TODO check whether we should store versions in status to make upgrade easier


def on_group_view_change(cluster: InnoDBCluster, members: list[tuple], view_id_changed: bool) -> None:
    """
    Triggered from the GroupMonitor whenever the membership view changes.
    This handler should react to changes that wouldn't be noticed by regular
    pod and cluster events.
    It also updates cluster status in the pods and cluster objects.
    """

    c = ClusterController(cluster)
    c.on_group_view_change(members, view_id_changed)


def monitor_existing_clusters(clusters: List[InnoDBCluster], logger: Logger) -> None:
    for cluster in clusters:
        if cluster.get_create_time():
            g_group_monitor.monitor_cluster(
                cluster, on_group_view_change, logger)


def ensure_backup_schedules_use_current_image(clusters: List[InnoDBCluster], logger: Logger) -> None:
    for cluster in clusters:
        try:
            backup_objects.ensure_schedules_use_current_image(cluster.parsed_spec, logger)
        except Exception as exc:
            # In case of any error we report but continue
            logger.warn(f"Error while ensuring {cluster.namespace}/{cluster.name} uses current operator version for scheduled backups: {exc}")


def ensure_router_accounts_are_uptodate(clusters: List[InnoDBCluster], logger: Logger) -> None:
    for cluster in clusters:
        router_objects.update_router_account(cluster,
                                             lambda: logger.warning(f"Cluster {cluster.namespace}/{cluster.name} unreachable"),
                                             logger)


def ignore_404(f) -> Any:
    try:
        return f()
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def do_create_read_replica(cluster: InnoDBCluster, rr: cluster_objects.ReadReplicaSpec,
                           set_replicas_to_zero: bool,
                           indention: str, logger: Logger) -> None:
    namespace = cluster.namespace
    print(f"{indention}Components ConfigMaps and Secrets")
    for cm in cluster_objects.prepare_component_config_configmaps(rr, logger):
        if not cluster.get_configmap(cm['metadata']['name']):
            print(f"{indention}\tCreating CM {cm['metadata']['name']} ...")
            kopf.adopt(cm)
            api_core.create_namespaced_config_map(namespace, cm)
    for secret in cluster_objects.prepare_component_config_secrets(rr, logger):
        if not cluster.get_secret(secret['metadata']['name']):
            print(f"{indention}\tCreating Secret {secret['metadata']['name']} ...")
            kopf.adopt(secret)
            api_core.create_namespaced_secret(namespace, secret)

    print(f"{indention}Initconf")
    if not ignore_404(lambda: cluster.get_initconf(rr)):
        print(f"{indention}\tPreparing... {rr.name}")
        configs = cluster_objects.prepare_initconf(cluster, rr, logger)
        print(f"{indention}\tCreating...")
        kopf.adopt(configs)
        api_core.create_namespaced_config_map(namespace, configs)

    print(f"{indention}RR ServiceAccount")
    existing_sa = ignore_404(lambda: cluster.get_service_account(rr))
    print(f"{indention}\tExisting SA: {existing_sa}")
    print(f"{indention}\tImagePullSecrets: {rr.imagePullSecrets}")
    if not existing_sa:
        print(f"{indention}\tPreparing...")
        sa = cluster_objects.prepare_service_account(rr)
        print(f"{indention}\tCreating...{sa}")
        kopf.adopt(sa)
        api_core.create_namespaced_service_account(namespace=namespace, body=sa)
    elif rr.imagePullSecrets:
        patch = cluster_objects.prepare_service_account_patch_for_image_pull_secrets(rr)
        print(f"{indention}\tPatching existing SA with {patch}")
        api_core.patch_namespaced_service_account(name=existing_sa.metadata.name, namespace=namespace, body=patch)

    print(f"{indention}RR RoleBinding")
    if not ignore_404(lambda: cluster.get_role_binding(rr)):
        print(f"{indention}\tPreparing...")
        rb = cluster_objects.prepare_role_binding(rr)
        print(f"{indention}\tCreating RoleBinding {rb['metadata']['name']} {rb}...")
        kopf.adopt(rb)
        api_rbac.create_namespaced_role_binding(namespace=namespace, body=rb)

    print(f"{indention}RR Service")
    if not ignore_404(lambda: cluster.get_read_replica_service(rr.name)):
        print(f"{indention}\tPreparing... {rr.name} Service")
        service = cluster_objects.prepare_cluster_service(rr, logger)
        print(f"{indention}\tCreating...{service}")
        kopf.adopt(service)
        api_core.create_namespaced_service(namespace=namespace, body=service)

    print(f"{indention}RR STS")
    if not ignore_404(lambda: cluster.get_read_replica_stateful_set(rr.name)):
        print(f"{indention}\tPreparing {rr.name} StatefulSet")
        statefulset = cluster_objects.prepare_cluster_stateful_set(rr, logger)
        if set_replicas_to_zero:
            # This is initial startup where scaling the read reaplica is delayed
            # till the clsuter is read
            statefulset['spec']['replicas'] = 0
        print(f"{indention}\tCreating...{statefulset}")
        kopf.adopt(statefulset)
        api_apps.create_namespaced_stateful_set(namespace=namespace, body=statefulset)


def do_reconcile_read_replica(cluster: InnoDBCluster,
                              rr: cluster_objects.ReadReplicaSpec,
                              logger: Logger) -> None:
    statefulset = cluster_objects.prepare_cluster_stateful_set(rr, logger)
    kopf.adopt(statefulset)
    api_apps.patch_namespaced_stateful_set(namespace=cluster.namespace,
                                           name=rr.name,
                                           body=statefulset)



@kopf.on.create(consts.GROUP, consts.VERSION,
                consts.INNODBCLUSTER_PLURAL)  # type: ignore
def on_innodbcluster_create(name: str, namespace: Optional[str], body: Body,
                            logger: Logger, **kwargs) -> None:
    logger.info(
        f"Initializing InnoDB Cluster name={name} namespace={namespace} on K8s {k8s_version()}")

    cluster = InnoDBCluster(body)

    # TODO: If we set the status here it will be emptied for unknown reasons later
    #       and hide other later set status (i.e. when using an invalid spec.version)
    #
    #cluster.set_status({
    #    "cluster": {
    #        "status":  diagnose.ClusterDiagStatus.INITIALIZING.value,
    #        "onlineInstances": 0,
    #        "lastProbeTime": utils.isotime()
    #    }})

    try:
        cluster.parse_spec()
        cluster.parsed_spec.validate(logger)
    except ApiSpecError as e:
        cluster.set_status({
            "cluster": {
                "status":  diagnose.ClusterDiagStatus.INVALID.value,
                "onlineInstances": 0,
                "lastProbeTime": utils.isotime()
            }})
        cluster.error(action="CreateCluster",
                      reason="InvalidArgument", message=str(e))
        raise kopf.TemporaryError(f"Error in InnoDBCluster spec: {e}")

    icspec = cluster.parsed_spec

    #print(f"Default operator IC edition: {config.MYSQL_OPERATOR_DEFAULT_IC_EDITION} Edition")
    cluster.log_cluster_info(logger)

    cluster.update_cluster_fqdn()

    if not cluster.ready:
        try:
            print("0. Components ConfigMaps and Secrets")
            for cm in cluster_objects.prepare_component_config_configmaps(icspec, logger):
                if not cluster.get_configmap(cm['metadata']['name']):
                    print(f"\tCreating CM {cm['metadata']['name']} ...")
                    kopf.adopt(cm)
                    api_core.create_namespaced_config_map(namespace, cm)

            for secret in cluster_objects.prepare_component_config_secrets(icspec, logger):
                if not cluster.get_secret(secret['metadata']['name']):
                    print(f"\tCreating Secret {secret['metadata']['name']} ...")
                    kopf.adopt(secret)
                    api_core.create_namespaced_secret(namespace, secret)

            print("0.5. Additional ConfigMaps")
            for cm in cluster_objects.prepare_additional_configmaps(icspec, logger):
                if not cluster.get_configmap(cm['metadata']['name']):
                    print(f"\tCreating CM {cm['metadata']['name']} ...")
                    kopf.adopt(cm)
                    api_core.create_namespaced_config_map(namespace, cm)

            print("1. Initial Configuration ConfigMap and Container Probes")
            if not ignore_404(lambda: cluster.get_initconf(icspec)):
                print("\tPreparing...")
                configs = cluster_objects.prepare_initconf(cluster, icspec, logger)
                print("\tCreating...")
                kopf.adopt(configs)
                api_core.create_namespaced_config_map(namespace, configs)

            print("2. Cluster Accounts")
            if not ignore_404(cluster.get_private_secrets):
                print("\tPreparing...")
                secret = cluster_objects.prepare_secrets(icspec)
                print("\tCreating...")
                kopf.adopt(secret)
                api_core.create_namespaced_secret(namespace=namespace, body=secret)

            print("3. Router Accounts")
            if not ignore_404(cluster.get_router_account):
                print("\tPreparing...")
                secret = router_objects.prepare_router_secrets(icspec)
                print("\tCreating...")
                kopf.adopt(secret)
                api_core.create_namespaced_secret(namespace=namespace, body=secret)

            print("4. Cluster Service")
            if not ignore_404(cluster.get_service):
                print("\tPreparing...")
                service = cluster_objects.prepare_cluster_service(icspec, logger)
                print(f"\tCreating Service {service['metadata']['name']}...{service}")
                kopf.adopt(service)
                api_core.create_namespaced_service(namespace=namespace, body=service)

            print("5. Cluster ServiceAccount")
            existing_sa = ignore_404(lambda: cluster.get_service_account(icspec))
            print(f"\tExisting SA: {existing_sa}")
            print(f"\tImagePullSecrets: {icspec.imagePullSecrets}")
            if not existing_sa:
                print("\tPreparing...")
                sa = cluster_objects.prepare_service_account(icspec)
                print(f"\tCreating...{sa}")
                kopf.adopt(sa)
                api_core.create_namespaced_service_account(namespace=namespace, body=sa)
            elif icspec.imagePullSecrets:
                patch = cluster_objects.prepare_service_account_patch_for_image_pull_secrets(icspec)
                print(f"\tPatching existing SA with {patch}")
                api_core.patch_namespaced_service_account(name=existing_sa.metadata.name, namespace=namespace, body=patch)

            print("6. Cluster RoleBinding")
            if not ignore_404(lambda: cluster.get_role_binding(icspec)):
                print("\tPreparing...")
                rb = cluster_objects.prepare_role_binding(icspec)
                print(f"\tCreating RoleBinding {rb['metadata']['name']} ...")
                kopf.adopt(rb)
                api_rbac.create_namespaced_role_binding(namespace=namespace, body=rb)

            print("7. Cluster StatefulSet")
            if not ignore_404(cluster.get_stateful_set):
                print("\tPreparing...")
                statefulset = cluster_objects.prepare_cluster_stateful_set(icspec, logger)
                print(f"\tCreating...{statefulset}")
                kopf.adopt(statefulset)
                api_apps.create_namespaced_stateful_set(namespace=namespace, body=statefulset)

            print("8. Cluster PodDisruptionBudget")
            if not ignore_404(cluster.get_disruption_budget):
                print("\tPreparing...")
                disruption_budget = cluster_objects.prepare_cluster_pod_disruption_budget(icspec)
                print("\tCreating...")
                kopf.adopt(disruption_budget)
                api_policy.create_namespaced_pod_disruption_budget(namespace=namespace, body=disruption_budget)

            print("9. Read Replica StatefulSets")
            if len(icspec.readReplicas) > 0:
                print(f"\t{len(icspec.readReplicas)} Read Replica STS ...")
                for rr in icspec.readReplicas:
                    do_create_read_replica(cluster, rr, True, "\t\t", logger)
            else:
                print("\tNo Read Replica")

            print("10. Router Service")
            if not ignore_404(cluster.get_router_service):
                print("\tPreparing...")
                router_service = router_objects.prepare_router_service(icspec)
                print("\tCreating...")
                kopf.adopt(router_service)
                api_core.create_namespaced_service(namespace=namespace, body=router_service)

            print("11. Router Deployment")
            if not ignore_404(cluster.get_router_deployment):
                if icspec.router.instances > 0:
                    print("\tPreparing...")
                    # This will create the deployment but 0 instances. When the cluster is created (first
                    # instance joins it) the instance count will be set to icspec.router.instances
                    router_deployment = router_objects.prepare_router_deployment(cluster, logger, init_only=True)
                    print(f"\tCreating...{router_deployment}")
                    kopf.adopt(router_deployment)
                    api_apps.create_namespaced_deployment(namespace=namespace, body=router_deployment)
                else:
                    # If the user decides to set !0 routers, the routine that handles that that
                    # will create the deployment
                    print("\tRouter count is 0. No Deployment is created.")

            print("12. Backup Secrets")
            if not ignore_404(cluster.get_backup_account):
                print("\tPreparing...")
                secret = backup_objects.prepare_backup_secrets(icspec)
                print("\tCreating...")
                kopf.adopt(secret)
                api_core.create_namespaced_secret(namespace=namespace, body=secret)

            print("13. Service Monitors")
            monitors = cluster_objects.prepare_metrics_service_monitors(cluster.parsed_spec, logger)
            if len(monitors) == 0:
                print("\tNone requested")
            for monitor in monitors:
                if not ignore_404(lambda: cluster.get_service_monitor(monitor['metadata']['name'])):
                    print(f"\tCreating ServiceMonitor {monitor} ...")
                    kopf.adopt(monitor)
                    try:
                        api_customobj.create_namespaced_custom_object(
                            "monitoring.coreos.com", "v1", cluster.namespace,
                            "servicemonitors", monitor)
                    except Exception as exc:
                        # This might be caused by Prometheus Operator missing
                        # we won't fail for that
                        print(f"\tServiceMonitor {monitor['metadata']['name']} NOT created!")
                        print(exc)
                        cluster.warn(action="CreateCluster", reason="CreateResourceFailed", message=f"{exc}")

        except Exception as exc:
            cluster.warn(action="CreateCluster", reason="CreateResourceFailed",
                         message=f"{exc}")
            raise

        print(f"13. Setting operator version for the IC to {DEFAULT_OPERATOR_VERSION_TAG}")
        cluster.set_operator_version(DEFAULT_OPERATOR_VERSION_TAG)
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
    logger.info(f"Updating Router Deployment.replicas to 0")
    router_objects.update_size(cluster, 0, logger)

    # Scale down the cluster to 0
    sts = cluster.get_stateful_set()
    if sts:
        # First we need to check if there is only one pod there and whether it is being deleted
        # In case it is being deleted on_pod_delete() won't be called when we scale down the STS to 0
        # In this case the code that calls cluster finalizer removal won't be called too and the
        # cluster finalizer will stay hanging
        # If we check after scaling down to 0, and there is only one pod, it will be moved to Terminating
        # state and we won't know whether it was in Terminating beforehand. If it wasn't then
        # on_pod_delete() will be called and we will try to remove the finalizer again
        # This is true if only the PodDisruptionBudget has set to maxUnavailable to 1. If more
        # then len(pods) == maxUnavailable and all pods should be inspected whether they are terminating
        pods = cluster.get_pods()
        if len(pods) == 1 and pods[0].deleting:
            # if there is only one pod and it is deleting then on_pod_delete() won't be called
            # in this case the IC finalizer won't be removed and the IC will hang
            logger.info("on_innodbcluster_delete: The cluster's only one pod is already deleting. Removing cluster finalizer here")
            cluster.remove_cluster_finalizer()

        logger.info(f"Updating InnoDB Cluster StatefulSet.instances to 0")
        cluster_objects.update_stateful_set_spec(sts, {"spec": {"replicas": 0}})


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
        with ClusterMutex(cluster):
            cluster_objects.update_stateful_set_spec(sts, {"spec": {"replicas": new}})


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

        cluster.parsed_spec.validate(logger)
        with ClusterMutex(cluster):
            cluster_ctl = ClusterController(cluster)
            try:
                cluster_ctl.on_router_upgrade(logger)
                cluster_ctl.on_server_version_change(new)
            except:
                # revert version in the spec
                raise

            cluster_objects.update_mysql_image(sts, cluster, cluster.parsed_spec, logger)

            router_deploy = cluster.get_router_deployment()
            if router_deploy:
                router_objects.update_router_image(router_deploy, cluster.parsed_spec, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.imageRepository")  # type: ignore
def on_innodbcluster_field_image_repository(old, new, body: Body,
                                            logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring spec.imageRepository change for unready cluster")
        return

    sts = cluster.get_stateful_set()
    if sts and old != new:
        logger.info(
            f"Propagating spec.imageRepository={new} for {cluster.namespace}/{cluster.name} (was {old})")

        cluster.parsed_spec.validate(logger)
        with ClusterMutex(cluster):
            try:
                cluster_ctl = ClusterController(cluster)
                cluster_ctl.on_router_upgrade(logger)
            except:
                # revert version in the spec
                raise
            cluster_objects.update_mysql_image(sts, cluster, cluster.parsed_spec, logger)
            cluster_objects.update_operator_image(sts, cluster.parsed_spec)
            router_deploy = cluster.get_router_deployment()
            if router_deploy:
                router_objects.update_router_image(router_deploy, cluster.parsed_spec, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.imagePullPolicy")  # type: ignore
def on_innodbcluster_field_image_pull_policy(old, new, body: Body,
                                            logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring spec.imagePullPolicy change for unready cluster")
        return

    sts = cluster.get_stateful_set()
    if sts and old != new:
        logger.info(
            f"Propagating spec.imagePullPolicy={new} for {cluster.namespace}/{cluster.name} (was {old})")

        cluster.parsed_spec.validate(logger)
        with ClusterMutex(cluster):
            cluster_objects.update_pull_policy(sts, cluster.parsed_spec, logger)
            router_deploy = cluster.get_router_deployment()
            if router_deploy:
                router_objects.update_pull_policy(router_deploy, cluster.parsed_spec, logger)


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
        with ClusterMutex(cluster):
            try:
                cluster_ctl = ClusterController(cluster)
                cluster_ctl.on_server_image_change(new)
            except:
                # revert version in the spec
                raise

            cluster_objects.update_mysql_image(sts, cluster, cluster.parsed_spec, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.instances")  # type: ignore
def on_innodbcluster_field_router_instances(old: int, new: int, body: Body,
                                            logger: Logger, **kwargs):
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.get_create_time():
        logger.debug(
            f"Ignoring spec.router.instances change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        logger.info(f"Updating Router Deployment.replicas from {old} to {new}")
        router_objects.update_size(cluster, new, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.version")  # type: ignore
def on_innodbcluster_field_router_version(old: str, new: str, body: Body,
                                          logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.get_create_time():
        logger.debug(
            f"Ignoring spec.router.version change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        try:
            cluster_ctl = ClusterController(cluster)
            cluster_ctl.on_router_upgrade(logger)
        except:
            # revert version in the spec
            raise
        router_deploy = cluster.get_router_deployment()
        if router_deploy:
            router_objects.update_router_image(router_deploy, cluster.parsed_spec, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.bootstrapOptions")  # type: ignore
def on_innodbcluster_field_router_bootstrap_options(old: str, new: str, body: Body,
                                                    logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.get_create_time():
        logger.debug(
            f"Ignoring spec.router.bootstrapOptions change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        router_deploy = cluster.get_router_deployment()
        if router_deploy:
            router_objects.update_bootstrap_options(router_deploy, cluster, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.options")  # type: ignore
def on_innodbcluster_field_router_options(old: str, new: str, body: Body,
                                          logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.get_create_time():
        logger.debug(
            f"Ignoring spec.router.options change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        router_deploy = cluster.get_router_deployment()
        if router_deploy:
            router_objects.update_options(router_deploy, cluster.parsed_spec, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.routingOptions")  # type: ignore
def on_innodbcluster_field_router_options(old: dict, new: dict, body: Body,
                                          logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.get_create_time():
        logger.debug(
            "Ignoring spec.router.routingOptions change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        if old is None:
            old = {}
        if new is None:
            new = {}

        c = ClusterController(cluster)
        c.on_router_routing_option_chahnge(old, new, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.backupSchedules")  # type: ignore
def on_innodbcluster_field_backup_schedules(old: str, new: str, body: Body,
                                          logger: Logger, **kwargs):
    if old == new:
        return

    logger.info("on_innodbcluster_field_backup_schedules")
    cluster = InnoDBCluster(body)

    # Ignore spec changes if the cluster is still being initialized
    # This handler will be called even when the cluster is being initialized as the
    # `old` value will be None and the `new` value will be the schedules that the cluster has.
    # This makes it possible to create them here and not in on_innodbcluster_create().
    # There in on_innodbcluster_create(), only the objects which are critical for the creation
    # of the server should be created.
    # After the cluster is ready we will add the schedules. This also allows to have the schedules
    # created (especially when `enabled`) after the cluster has been created, solving issues with
    # cron job not bein called or cron jobs being created as suspended and then when the cluster is
    # running to be enabled again - which would end to be a 2-step process.
    # The cluster is created after the first instance is up and running. Thus,
    # don't need to take actions in post_create_actions() in the cluster controller
    # but async await for Kopf to call again this handler.
    if not cluster.get_create_time():
        raise kopf.TemporaryError("The cluster is not ready. Will create the schedules once the first instance is up and running", delay=10)

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        backup_objects.update_schedules(cluster.parsed_spec, old, new, logger)


def on_sts_field_update(body: Body, field: str, logger: Logger) -> None:
    cluster = InnoDBCluster(body)

    if not cluster.get_create_time():
        logger.debug(f"Ignoring {field} change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        cluster_objects.reconcile_stateful_set(cluster, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.tlsUseSelfSigned")  # type: ignore
def on_innodbcluster_field_tls_use_self_signed(body: Body,
                                               logger: Logger, **kwargs):
    logger.info("on_innodbcluster_field_tls_use_self_signed")
    on_sts_field_update(body, "spec.tlsUseSelfSigned", logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.tlsSecretName")  # type: ignore
def on_innodbcluster_field_tls_secret_name(body: Body,
                                          logger: Logger, **kwargs):
    logger.info("on_innodbcluster_field_tls_secret_name")
    on_sts_field_update(body, "spec.tlsSecretName", logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.tlsSecretName")  # type: ignore
def on_innodbcluster_field_router_tls_secret_name(body: Body,
                                                  logger: Logger, **kwargs):
    logger.info("on_innodbcluster_field_router_tls_secret_name")
    on_sts_field_update(body, "spec.router.tlsSecretName", logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.tlsCASecretName")  # type: ignore
def on_innodbcluster_field_tls_ca_secret_name(body: Body,
                                              logger: Logger, **kwargs):
    logger.info("on_innodbcluster_field_tls_ca_secret_name")
    on_sts_field_update(body, "spec.tlsCASecretName", logger)

@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.readReplicas")  # type: ignore
def on_innodbcluster_read_replicas_changed(body: Body, old: list, new: list,
                                           logger: Logger, **kwargs):
    logger.info("on_innodbcluster_read_replicas_changed")

    if old == new:
        return

    cluster = InnoDBCluster(body)
    if not cluster.get_create_time():
        raise kopf.TemporaryError("The cluster is not ready. Will retry", delay=30)

    cluster.parsed_spec.validate(logger)

    if old is None:
        old = []
    if new is None:
        new = []

    with ClusterMutex(cluster):
        # Remove read replica sets which were removed
        for rr in old:
            if rr['name'] not in map(lambda nrr: nrr['name'], new):
                cluster_objects.remove_read_replica(cluster, rr['name'])

        # Add or update read replica sets
        for rr in new:
            old_rr = next(filter(lambda orr: orr['name'] == rr['name'], old), None)

            rrspec = cluster.parsed_spec.get_read_replica(rr['name'])
            if rrspec is None:
                # This should never happen except maybe a very short race
                # when user adds it and immediateyl removes or in a retry
                # loop. But in all those cases its removed after adding,
                # thus not creating is fine
                logger.warn(f"Could not find Spec for ReadReplica {rr['name']} in InnoDBCluster")
                continue

            if old_rr == rr:
                # no change
                pass
            elif old_rr:
                # Old Read Replica -> Update
                do_reconcile_read_replica(cluster, rrspec, logger)
            else:
                # New Read Replica -> Create it
                do_create_read_replica(cluster, rrspec, False, "", logger)


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

    print(f"on_pod_create: pod={pod.name} ContainersReady={pod.check_condition('ContainersReady')} Ready={pod.check_condition('Ready')} gate[configured]={pod.get_member_readiness_gate('configured')}")

    configured = pod.get_member_readiness_gate("configured")
    if not configured:
        # TODO add extra diagnostics about why the pod is not ready yet, for
        # example, unbound volume claims, initconf not finished etc
        raise kopf.TemporaryError(f"Sidecar of {pod.name} is not yet configured", delay=30)

    # If we are here all containers have started. This means, that if we are initializing
    # the database from a donor (cloning) the sidecar has already started a seed instance
    # and cloned from the donor into it (see initdb.py::start_clone_seed_pod())
    cluster = pod.get_cluster()

    assert cluster

    with ClusterMutex(cluster, pod):
        first_pod = pod.index == 0 and not cluster.get_create_time()
        if first_pod:
            print("on_pod_create: first pod created")
            cluster_objects.on_first_cluster_pod_created(cluster, logger)

            g_group_monitor.monitor_cluster(
                cluster, on_group_view_change, logger)

        cluster_ctl = ClusterController(cluster)

        cluster_ctl.on_pod_created(pod, logger)

        # Remember how many restarts happened as of now
        g_ephemeral_pod_state.set(pod, "mysql-restarts", pod.get_container_restarts("mysql"), context="on_pod_create")


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
            logger.debug(f"POD EVENT {event}: pod={pod.name} containers_ready={ready} deleting={pod.deleting} phase={pod.phase} member_info={member_info} restarts={mysql_restarts} containers={containers} conditions={conditions}")

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

                    g_ephemeral_pod_state.set(pod, "mysql-restarts", mysql_restarts, context="on_pod_event")

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
    print("on_pod_delete")
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
                print("Last cluster removed being removed!")
                cluster_objects.on_last_cluster_pod_removed(cluster, logger)
    else:
        pod.remove_member_finalizer(body)

        logger.error(f"Owner cluster for {pod.name} does not exist anymore")

# An example of a `when` hook for finding secrets belonging to a IC
#
#def secret_belongs_to_a_cluster_checker(meta, namespace:str, name, logger: Logger, **_) -> bool:
#    clusters = get_all_clusters(namespace)
#    for cluster in clusters:
#        if name in (cluster.parsed_spec.tlsCASecretName,
#                    cluster.parsed_spec.tlsSecretName,
#                    cluster.parsed_spec.router.tlsSecretName):
#            return True
#    return False
#
# Use like the following
#@kopf.on.create("", "v1", "secrets", when=secret_belongs_to_a_cluster_checker) # type: ignore
#@kopf.on.update("", "v1", "secrets", when=secret_belongs_to_a_cluster_checker) # type: ignore
#


def on_ic_labels_and_annotations_change(what: str, body: Body, diff, old, new, logger: Logger) -> None:
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring {what} change for unready cluster")
        return

    # TODO - identify what cluster statuses should allow changes to the size of the cluster

    sts = cluster.get_stateful_set()
    cluster.parsed_spec.validate(logger)
    if sts and diff:
        logger.info(f"on_ic_labels_and_annotations_change: Updating InnoDB Cluster StatefulSet {what}")
        patch = {field[0]: new for op, field, old, new in diff }
        with ClusterMutex(cluster):
            cluster_objects.update_stateful_set_spec(sts, {"spec": {"template": { "metadata" : { what : patch }}}})


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.podLabels")  # type: ignore
def on_innodbcluster_field_pod_labels(body: Body, diff, old, new, logger: Logger, **kwargs):

    on_ic_labels_and_annotations_change("labels", body, diff, old, new, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.podAnnotations")  # type: ignore
def on_innodbcluster_field_pod_annotations(body: Body, diff, old, new, logger: Logger, **kwargs):

    on_ic_labels_and_annotations_change("annotations", body, diff, old, new, logger)


def on_ic_router_labels_and_annotations_change(what: str, body: Body, diff, logger: Logger) -> None:
    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug(f"Ignoring {what} change for unready cluster")
        return

    # TODO - identify what cluster statuses should allow changes to the size of the cluster
    patch = {field[0]: new for op, field, old, new in diff }
    logger.info(f"diff={diff}")
    logger.info(f"patch={patch}")
    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        router_objects.update_labels_or_annotations(what, patch, cluster, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.podLabels")  # type: ignore
def on_innodbcluster_field_router_pod_labels(body: Body, diff, logger: Logger, **kwargs):

    on_ic_router_labels_and_annotations_change("labels", body, diff, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.router.podAnnotations")  # type: ignore
def on_innodbcluster_field_router_pod_annotations(body: Body, new, diff, logger: Logger, **kwargs):

    on_ic_router_labels_and_annotations_change("annotations", body, diff, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.metrics")  # type: ignore
def on_innodbcluster_field_metrics(old: str, new: str, body: Body,
                                   logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug("Ignoring spec.metrics change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        # We have to edit the user account first, else the server might go away
        # whie we are trying to change the user

        # if we want to allow custom usernames we'd have to delete old here
        cluster_ctl = ClusterController(cluster)
        cluster_ctl.on_change_metrics_user(logger)

        cluster_objects.update_objects_for_metrics(cluster, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL,
               field="spec.service")  # type: ignore
def on_innodbcluster_field_service_type(old: str, new: str, body: Body,
                                       logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)
    with ClusterMutex(cluster):
        svc = cluster.get_router_service()
        router_objects.update_service(svc, cluster.parsed_spec, logger)


@kopf.on.field(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL, field="spec.logs")  # type: ignore
def on_innodbcluster_field_logs(old: str, new: str, body: Body, logger: Logger, **kwargs):
    if old == new:
        return

    cluster = InnoDBCluster(body)

    # ignore spec changes if the cluster is still being initialized
    if not cluster.ready:
        logger.debug("Ignoring spec.logs change for unready cluster")
        return

    cluster.parsed_spec.validate(logger)
    with ClusterMutex(cluster):
        cluster_objects.update_objects_for_logs(cluster, logger)
