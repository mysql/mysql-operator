# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import mysqlsh
import asyncio
import kopf
import os
import time
import logging

from .controller import k8sobject, config as myconfig
from .controller.kubeutils import ApiException, api_apps, k8s_cluster_domain
from .controller.operator_topology import (
    TOPOLOGY_ANNOTATION,
    OperatorTopology,
    OperatorTopologyError,
    deployment_name as resolved_deployment_name,
    deployment_namespace as resolved_deployment_namespace,
    resolve_operator_deployment_topology,
)


k8sobject.g_component = "operator"
k8sobject.g_host = os.getenv("HOSTNAME")


def get_operator_deployment(namespace: str, deployment_name: str):
    return api_apps.read_namespaced_deployment(deployment_name, namespace)


def get_operator_deployments():
    return api_apps.list_deployment_for_all_namespaces().items


def get_configured_operator_replicas(deployment) -> int:
    replicas = deployment.spec.replicas
    return replicas if replicas is not None else 1


def persist_operator_topology_annotation(
    namespace: str,
    deployment_name: str,
    topology: OperatorTopology,
) -> None:
    api_apps.patch_namespaced_deployment(
        deployment_name,
        namespace,
        {
            "metadata": {
                "annotations": {
                    TOPOLOGY_ANNOTATION: topology.to_annotation_value(),
                }
            }
        },
    )


def _format_topology_conflict_message(
    current_topology: OperatorTopology,
    other_ref: str,
    other_topology: OperatorTopology,
) -> str:
    overlapping_namespaces = current_topology.overlapping_namespaces(other_topology)
    if overlapping_namespaces:
        return (
            f"Operator topology conflicts with existing operator {other_ref}; "
            f"overlapping namespaces: {', '.join(overlapping_namespaces)}."
        )

    return (
        f"Operator topology conflicts with existing operator {other_ref}; "
        "a global operator overlaps every namespace."
    )


def ensure_operator_topology_consistency(
    current_topology: OperatorTopology,
    namespace: str,
    deployment_name: str,
) -> None:
    deployment = get_operator_deployment(namespace, deployment_name)

    try:
        resolved_self = resolve_operator_deployment_topology(deployment)
    except OperatorTopologyError as exc:
        raise RuntimeError(str(exc)) from exc

    if resolved_self is None:
        raise RuntimeError(
            f"Deployment {namespace}/{deployment_name} is not recognized as a MySQL operator Deployment."
        )

    if resolved_self.needs_bootstrap_annotation:
        if resolved_self.topology != current_topology:
            raise RuntimeError(
                f"Deployment {resolved_self.ref} is missing {TOPOLOGY_ANNOTATION} and "
                f"can only bootstrap as {resolved_self.topology.describe()}, but the current "
                f"configuration resolves to {current_topology.describe()}."
            )

        persist_operator_topology_annotation(
            namespace,
            deployment_name,
            current_topology,
        )
        logging.info(
            "Persisted %s on Deployment %s using %s bootstrap.",
            TOPOLOGY_ANNOTATION,
            resolved_self.ref,
            resolved_self.source,
        )
    elif resolved_self.topology != current_topology:
        raise RuntimeError(
            f"Deployment {resolved_self.ref} persisted {TOPOLOGY_ANNOTATION} as "
            f"{resolved_self.topology.describe()}, but the current configuration "
            f"resolves to {current_topology.describe()}."
        )

    for other_deployment in get_operator_deployments():
        other_namespace = resolved_deployment_namespace(other_deployment)
        other_name = resolved_deployment_name(other_deployment)
        if other_namespace == namespace and other_name == deployment_name:
            continue

        try:
            resolved_other = resolve_operator_deployment_topology(other_deployment)
        except OperatorTopologyError as exc:
            raise RuntimeError(str(exc)) from exc

        if resolved_other is None:
            continue

        if current_topology.overlaps(resolved_other.topology):
            raise RuntimeError(
                _format_topology_conflict_message(
                    current_topology,
                    resolved_other.ref,
                    resolved_other.topology,
                )
            )


def ensure_standalone_safe_deployment(standalone: bool, namespace: str, deployment_name: str) -> None:
    try:
        deployment = get_operator_deployment(namespace, deployment_name)
    except ApiException as exc:
        logging.error(
            "Standalone operator requires a safe Deployment configuration, but could not read Deployment %s/%s: %s",
            namespace,
            deployment_name,
            exc,
        )
        raise

    replicas = get_configured_operator_replicas(deployment)
    if replicas != 1:
        raise RuntimeError(
            f"Standalone operator requires exactly one configured replica; "
            f"Deployment {namespace}/{deployment_name} has replicas={replicas}."
        )

    strategy = deployment.spec.strategy
    strategy_type = getattr(strategy, "type", None) or "RollingUpdate"
    if strategy_type == "Recreate":
        return

    rolling_update = getattr(strategy, "rolling_update", None)
    max_surge = getattr(rolling_update, "max_surge", None) if rolling_update else None
    max_unavailable = getattr(rolling_update, "max_unavailable", None) if rolling_update else None

    if strategy_type == "RollingUpdate" and str(max_surge) == "0" and str(max_unavailable) == "1":
        return

    raise RuntimeError(
        f"Standalone operator requires a safe Deployment strategy; Deployment {namespace}/{deployment_name} "
        f"must use strategy.type=Recreate or RollingUpdate with maxSurge=0 and maxUnavailable=1."
    )


def get_kopf_namespaces(namespaces: list[str] | None) -> list[str]:
    # Kopf's namespace observer expects an iterable even for cluster-wide mode.
    return namespaces or []


def main(argv):
    mysqlsh.globals.shell.options.useWizards = False
    # https://dev.mysql.com/doc/mysql-shell/8.0/en/mysql-shell-application-log.html
    mysqlsh.globals.shell.options.logLevel = 4 # 8 for tracing
    mysqlsh.globals.shell.options.verbose = 0
    #mysqlsh.globals.shell.options.logSql= "all"

    myconfig.config_from_env()

    # this will register operator event handlers. Don't remove even if it seems unused!!
    from .controller import operator  # noqa: F401

    kopf.configure(verbose=True if myconfig.debug >= 1 else False)

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")

    # populate cached value
    k8s_cluster_domain(logging)

    # Priority defines the priority/weight of this instance of the operator for
    # kopf peering. If there are multiple operator instances in the cluster,
    # only the one with the highest priority will actually be active.
    # Standalone operator is an operator without peering. Here we use clusterwide, which is False by default
    # Non-standalone operators are
    # 1. cluster wide, then they use ClusterKopfPeering, which is a global k8s cluster object
    # 2. namespace bound, then the use KopfPeering, which is a namespace bound object
    # We need a default for OPERATOR_PEERING_NAME, as pre-9.7.0 operator deployments don't have that set
    #
    # OPERATOR_PEERING_NAME should have the same value as the name in the ClusterKopfPeering if clusterwide else in KopfPeering
    peering_name = None
    standalone = os.getenv("OPERATOR_STANDALONE", "").lower() in ["1", "true"]
    current_topology = OperatorTopology.from_env(
        os.getenv("OPERATOR_NAMESPACES", ""),
        standalone,
    )
    namespaces_list = list(current_topology.namespaces) if not current_topology.is_global else None
    operator_deploy_name = os.getenv("OPERATOR_DEPLOYMENT_NAME", "mysql-operator")
    operator_ns = os.getenv("POD_NAMESPACE", "mysql-operator")
    clusterwide = current_topology.is_global
    watched = f"[{', '.join(current_topology.namespaces)}]" if current_topology.namespaces else "[]"
    logging.info(f"Operator {operator_deploy_name} serving from {operator_ns} namespace. Watching {watched} namespaces")
    logging.info("Operator topology resolved as %s", current_topology.describe())

    ensure_operator_topology_consistency(
        current_topology,
        operator_ns,
        operator_deploy_name,
    )

    if standalone:
        ensure_standalone_safe_deployment(standalone, operator_ns, operator_deploy_name)
        logging.info("Standalone operator, no peering")
    else:
        peering_name = os.getenv("OPERATOR_PEERING_NAME", "mysql-operator") # must be the same as the identified in ClusterKopfPeering
        scope = 'cluster' if clusterwide else 'namespaced'
        logging.info(f"Operator will use {peering_name} {scope} peering")

    loop = asyncio.get_event_loop()
    loop.run_until_complete(kopf.operator(
        clusterwide=clusterwide,
        namespaces=get_kopf_namespaces(namespaces_list),
        priority=int(time.time()*1000000),
        peering_name=peering_name,
        standalone=standalone
    ))

    return 0


if __name__ == "__main__":
    main([])
