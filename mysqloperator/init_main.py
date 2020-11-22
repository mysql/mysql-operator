# Copyright (c) 2020, Oracle and/or its affiliates.
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

import subprocess
import mysqlsh
import sys
import os
import logging
import time
import shutil
from .controller import utils, config
from .controller.innodbcluster.cluster_api import MySQLPod

mysql = mysqlsh.mysql


def init_conf(datadir, pod, cluster, logger):
    """
    Initialize MySQL configuration files and init scripts, which must be mounted
    in /mnt/mycnfdata.
    The source config files must be mounted in /mnt/initconf.

    The config files are them symlinked to /etc to be used by mysqld in the rest
    of the script. The main container should directly mount them in their final
    locations.

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

    if os.path.exists("/etc/my.cnf"):
        logger.info("Replacing /etc/my.cnf, old contents were:")
        logger.info(open("/etc/my.cnf").read())
        os.remove("/etc/my.cnf")
    os.symlink(destdir + "my.cnf", "/etc/my.cnf")

    if os.path.exists("/etc/my.cnf.d"):
        os.rmdir("/etc/my.cnf.d")
    os.symlink(destdir + "my.cnf.d", "/etc/my.cnf.d")

    logger.info(f"Configuration done")


def main(argv):
    datadir = argv[1] if len(argv) > 1 else "/var/lib/mysql"

    mysqlsh.globals.shell.options.useWizards = False
    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("initmysql")

    name = os.getenv("MY_POD_NAME")
    namespace = os.getenv("MY_POD_NAMESPACE")

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
