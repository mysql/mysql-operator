# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import subprocess
import sys
import os
import json
import logging
import shutil
import argparse
from typing import cast
import mysqlsh
from .controller import fqdn, utils, k8sobject, config
from .controller.api_utils import Edition
from .controller.innodbcluster.cluster_api import MySQLPod, InnoDBCluster
from .controller.kubeutils import k8s_cluster_domain
from .controller.kubeutils import client as api_client, api_core, ApiException

k8sobject.g_component = "initconf"
k8sobject.g_host = os.getenv("HOSTNAME")


mysql = mysqlsh.mysql

def get_secret(secret_name: str, namespace: str, logger: logging.Logger) -> dict:
    if not secret_name:
        raise Exception(f"No secret provided")

    ret = {}
    try:
        secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(secret_name, namespace))
        for k, v in secret.data.items():
            ret[k] = utils.b64decode(v)
    except Exception:
        raise Exception(f"Secret {secret_name} in namespace {namespace} cannot be found")

    return ret



def init_conf(datadir: str, pod: MySQLPod, cluster, logger: logging.Logger):
    """
    Initialize MySQL configuration files and init scripts, which must be mounted
    in /mnt/mycnfdata.
    The source config files must be mounted in /mnt/initconf.

    Init scripts are executed by the mysql container entrypoint when it's
    initializing for the 1st time.
    """
    if pod.instance_type == "read-replica":
        read_replica_name = pod.read_replica_name
        [rr_spec] = filter(lambda rr: rr.name == read_replica_name,
                           cluster.parsed_spec.readReplicas)
        server_id = pod.index + rr_spec.baseServerId
    elif pod.instance_type == "group-member":
        server_id = pod.index + cluster.parsed_spec.baseServerId
    else:
        raise RuntimeError(f"Invalid instance type: {pod.instance_type}")

    report_host = fqdn.pod_fqdn(pod, logger)

    logger.info(
        f"Setting up configurations for {pod.name}  server_id={server_id}  report_host={report_host}")

    srcdir = "/mnt/initconf/"
    destdir = "/mnt/mycnfdata/"
    mycnf_dir = destdir + "my.cnf.d"
    initdb_dir = destdir + "docker-entrypoint-initdb.d"

    os.makedirs(mycnf_dir, exist_ok=True)
    os.makedirs(initdb_dir, exist_ok=True)

    with open(srcdir + "my.cnf.in") as f:
        data = f.read()
        data = data.replace("@@SERVER_ID@@", str(server_id))
        data = data.replace("@@HOSTNAME@@", str(report_host))
        data = data.replace("@@DATADIR@@", datadir)
        with open(destdir + "my.cnf", "w+") as mycnf:
            mycnf.write(data)

    for f in os.listdir(srcdir):
        file = os.path.join(srcdir, f)
        if f.startswith("initdb-"):
            print(f"Copying {file} to {initdb_dir}")
            shutil.copy(file, initdb_dir)
            if f.endswith(".sh"):
                os.chmod(os.path.join(initdb_dir, f), 0o555)
        elif f.endswith(".cnf"):
            print(f"Copying {file} to {mycnf_dir}")
            shutil.copy(file, mycnf_dir)

    logger.info("Configuration done")

def init_meb_restore(pod: MySQLPod, cluster: InnoDBCluster, logger: logging.Logger):
    """Check whether the restore container should restore or not

    The restore container is based on MySQL Server, not Operator, thus has no
    access to Kubernetes API etc. in here we make the decision whether this
    is the first pod of a fresh InnoDB Cluster (i.e. not a recreated -0 for
    an otherwise running cluster) and leave a marker.

    We also use tha API to fetch Secrets with credentials for restoring the
    backup, so that Secret may be deleted afterward"""

    # Remove Marker as safe fallback
    try:
        os.remove('/tmp/meb_restore.json')
    except FileNotFoundError:
        # no problem if the file doesn't exist, this would still raise when
        # failing to remove for permission issues etc which shouldn't happen
        pass

    # Check precoditions

    if not cluster.parsed_spec.initDB or not cluster.parsed_spec.initDB.meb:
        logger.error("No MySQL Enterprise Restore configured.")
        return

    if cluster.parsed_spec.edition != config.Edition.enterprise:
        print("MySQL Enterprise Restore request, but this is not Enterprise Edition")
        sys.exit(1)

    if pod.index:
        logger.info(f"Nothing to do for restore - restore happens only on Pod 0. this is {pod.name}")
        return

    if cluster.get_create_time() is not None:
        logger.info("Nothing to do for restore - this is a restart")
        return

    if pod.instance_type != "group-member":
        logger.info(f"Nothing to do for restore - this is not a group member but {pod.instance_type}")
        return

    # All preconditions are met. We should request a restore.

    logger.info("We got to request a MEB restore")

    mebspec = cluster.parsed_spec.initDB.meb

    if mebspec.oci_credentials:
        credentials = get_secret(mebspec.oci_credentials, cluster.namespace, logger)
    elif mebspec.s3_credentials:
        credentials = get_secret(mebspec.s3_credentials, cluster.namespace, logger)


    meb_init_spec = {
        "spec": cluster.spec["initDB"]["meb"],
        "credentials": credentials
    }

    with open('/tmp/meb_restore.json', 'w') as f:
        json.dump(meb_init_spec, f)


def main(argv):
    # const - when there is an argument without value
    # default - when there is no argument at all
    # nargs = "?" - zero or one arguments
    # nargs = "+" - one or more arguments, returns a list()
    # nargs = 8 - 8 arguments will be consumed
    # nargs = 1 - 1 argument will be consumed, returns a list with one element
    parser = argparse.ArgumentParser(description = "MySQL InnoDB Cluster Instance Sidecar Container")
    parser.add_argument('--logging-level', type = int, nargs="?", default = logging.INFO, help = "Logging Level")
    parser.add_argument('--pod-name', type = str, nargs=1, default=None, help = "Pod Name")
    parser.add_argument('--pod-namespace', type = str, nargs=1, default=None, help = "Pod Namespace")
    parser.add_argument('--datadir', type = str, default = "/var/lib/mysql", help = "Path do data directory")
    args = parser.parse_args(argv)

    datadir = args.datadir

    mysqlsh.globals.shell.options.useWizards = False
    logging.basicConfig(level=args.logging_level,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("initmysql")

    name = args.pod_name[0] # nargs returns a list
    namespace = args.pod_namespace[0] # nargs returns a list

    logger.info(f"Configuring mysql pod {namespace}/{name}, datadir={datadir}")

    utils.log_banner(__file__, logger)

    if logger.level == logging.DEBUG:
        logger.debug(f"Initial contents of {datadir}:")
        subprocess.run(["ls", "-l", datadir])

        logger.debug("Initial contents of /mnt:")
        subprocess.run(["ls", "-lR", "/mnt"])

    try:
        pod = MySQLPod.read(name, namespace)
        cluster = pod.get_cluster()

        init_conf(datadir, pod, cluster, logger)
        init_meb_restore(pod, cluster, logger)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while bootstrapping MySQL: {e}")
        # TODO post event to the Pod and the Cluster object if this is the seed
        return 1

    # TODO support for restoring from clone snapshot or MEB goes in here

    return 0
