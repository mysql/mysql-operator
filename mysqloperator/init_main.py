# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import subprocess
import sys
import os
import logging
import shutil
import argparse
from typing import cast
import mysqlsh
from .controller import utils, k8sobject
from .controller.innodbcluster.cluster_api import MySQLPod

k8sobject.g_component = "initconf"
k8sobject.g_host = os.getenv("HOSTNAME")


mysql = mysqlsh.mysql


def init_conf(datadir, pod, cluster, logger):
    """
    Initialize MySQL configuration files and init scripts, which must be mounted
    in /mnt/mycnfdata.
    The source config files must be mounted in /mnt/initconf.

    Init scripts are executed by the mysql container entrypoint when it's
    initializing for the 1st time.
    """
    server_id = pod.index + cluster.parsed_spec.baseServerId
    report_host = f"{pod.name}.{cluster.name}-instances.{cluster.namespace}.svc.cluster.local"
    logger.info(
        f"Setting up configurations for {pod.name}  server_id={server_id}  report_host={report_host}")

    srcdir = "/mnt/initconf/"
    destdir = "/mnt/mycnfdata/"

    os.makedirs(destdir + "my.cnf.d", exist_ok=True)
    os.makedirs(destdir + "docker-entrypoint-initdb.d", exist_ok=True)

    with open(srcdir + "my.cnf.in") as f:
        data = f.read()
        data = data.replace("@@SERVER_ID@@", str(server_id))
        data = data.replace("@@HOSTNAME@@", str(report_host))
        data = data.replace("@@DATADIR@@", datadir)
        with open(destdir + "my.cnf", "w+") as mycnf:
            mycnf.write(data)

    for f in os.listdir(srcdir):
        if f.startswith("initdb-"):
            shutil.copy(os.path.join(srcdir, f), destdir +
                        "docker-entrypoint-initdb.d")
            if f.endswith(".sh"):
                os.chmod(os.path.join(
                    destdir + "docker-entrypoint-initdb.d", f), 0o555)
        elif f.endswith(".cnf"):
            shutil.copy(os.path.join(srcdir, f), destdir + "my.cnf.d")

    logger.info(f"Configuration done")


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

    logger.debug(f"Initial contents of {datadir}:")
    subprocess.run(["ls", "-l", datadir])

    logger.debug("Initial contents of /mnt:")
    subprocess.run(["ls", "-lR", "/mnt"])

    try:
        pod = MySQLPod.read(name, namespace)
        cluster = pod.get_cluster()

        init_conf(datadir, pod, cluster, logger)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while bootstrapping MySQL: {e}")
        # TODO post event to the Pod and the Cluster object if this is the seed
        return 1

    # TODO support for restoring from clone snapshot or MEB goes in here

    return 0
