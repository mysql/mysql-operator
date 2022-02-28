# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import subprocess
import mysqlsh
import sys
import os
import logging
import shutil
from typing import cast
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
    report_host = f'{os.getenv("MY_POD_NAME")}.{cluster.name}-instances.{cluster.namespace}.svc.cluster.local'
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
    datadir = "/var/lib/mysql"

    mysqlsh.globals.shell.options.useWizards = False
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("initmysql")

    name = cast(str, os.getenv("MY_POD_NAME"))
    namespace = cast(str, os.getenv("MY_POD_NAMESPACE"))

    utils.log_banner(__file__, logger)

    logger.info(f"Configuring mysql pod {namespace}/{name}, datadir={datadir}")

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
