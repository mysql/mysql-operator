# Copyright (c) 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import argparse
import json
import logging

from .controller import shellutils

from .controller.innodbcluster.cluster_api import InnoDBCluster
from .controller.innodbcluster.cluster_controller import ClusterController

def main(argv):
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("FAILOVER")

    parser = argparse.ArgumentParser(description = "MySQL InnoDB Cluster Failover")
    parser.add_argument('--cluster-name', type=str, default="", help="Name of the new primary cluster")
    parser.add_argument('--namespace', type=str, default = "", help = "Namespace")
    parser.add_argument('--force', default=False, action=argparse.BooleanOptionalAction, help="Force it")
    parser.add_argument('--timeout', type=int, default=None)
    parser.add_argument('--invalidate-replica-clusters', type=str, default=None)

    args = parser.parse_args(argv)

    cluster = InnoDBCluster.read(args.namespace, args.cluster_name)

    pod = cluster.get_pod(0)
    pod.logger = logger

    options = {}

    if args.timeout:
        options["timeout"] = args.timeout

    if args.invalidate_replica_clusters:
        invalidations = args.invalidate_replica_clusters.split(",")
        options["invalidateReplicaClusters"] = invalidations

    logger.info(f"Trying to switch over to {args.cluster_name} . Options {options}")

    with shellutils.DbaWrap(shellutils.connect_to_pod_dba(pod, logger)) as dba:
        cs = dba.get_cluster_set()
        logger.info(f"Before CSet={json.dumps(cs.status(), indent=4)}")
        if args.force:
           cs.force_primary_cluster(args.cluster_name, options)
        else:
            cs.set_primary_cluster(args.cluster_name, options)
        logger.info(f"After CSet={json.dumps(cs.status(), indent=4)}")

    controller = ClusterController(cluster)
    try:
        logger.info("Probing cluster status after the change")
        controller.probe_status(logger)
    except Exception as exc:
        printing(f"{exc}")
