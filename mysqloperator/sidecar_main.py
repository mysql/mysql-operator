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

# Bootstrap sequence:

# initContainer
# 1 - initializes MySQL configuration files
#
# MySQL container entrypoint:
# 2 - initialize datadir
# 3 - create root
# 4 - start
#
# Shell Sidecar:
# 5 - create localroot
# 6 - configureInstance()
# 7 - initialize db (clone, loadDump etc)
# 8 - restart (optional)
# 9 - mark as ready


# Initializes a MySQL Pod to be used in an InnoDBCluster managed by Kubernetes
#
# This performs the following tasks and runs instead of the regular
# initialization done by the Docker image entry point:
#  - create localroot user
#  - initialize datadir
#  - configureInstance() and create InnoDB Cluster admin account
#  - if this is the seed pod:
#     - populate database from what's specified in initDB
#     - create remote root user
#
# This process will start and restart mysqld as needed, but mysqld will be
# stopped when it exits. It is meant to be called from a initContainer, if
# this script fails, the Pod creation fails.
#
# This script is called whenever the Pod is created, including when the Pod
# is deleted and recreated soon after by the StatefulSet. When a Pod is
# recreated, the volumes may still contain data from the previous run of the
# same Pod of the same StatefulSet (?).
#

import subprocess
import mysqlsh
import sys
import os
import logging
import time
import shutil
from .controller import utils, mysqlutils, config
from .controller.innodbcluster import initdb
from .controller.innodbcluster.cluster_api import MySQLPod

mysql = mysqlsh.globals.mysql

# The time it takes for mysqld to restart after a clone can be very long,
# because it has to apply redo logs. OTOH we monitor the error log to see
# if there's any activity happening, so the timeout is for activity to happen
# not the total time it takes for the server to start.
CLONE_RESTART_TIMEOUT=60*10

def create_local_accounts(session, logger):
    """
    Creates:
    - a localroot@localhost account with auth_socket authentication.
    """
    sql = [
        "SET sql_log_bin=0;",
        "CREATE USER IF NOT EXISTS 'localroot'@localhost IDENTIFIED WITH auth_socket AS 'root';",
        "GRANT ALL ON *.* TO 'localroot'@localhost WITH GRANT OPTION;",
        "GRANT PROXY ON ''@'' TO 'localroot'@localhost WITH GRANT OPTION;",
        "SET sql_log_bin=1;"
    ]
    logger.info("Creating local accounts")
    for s in sql:
        try:
            session.run_sql(s)
        except Exception as e:
            logger.error(f"Error executing {s}: {e}")
            raise Exception("Error creating local accounts")


def configure_for_innodb_cluster(dba, logger):
    """
    Configure instance for InnoDB Cluster.
    """
    options = {}
    logger.info("Configuring instance for InnoDB Cluster")
    dba.configure_instance(None, options)


def wipe_old_innodb_cluster(session, logger):
    # drop innodb cluster accounts
    try:
        rows = session.run_sql("select attributes->>'$.recoveryAccountUser', attributes->>'$.recoveryAccountHost' from mysql_innodb_cluster_metadata.v2_instances").fetch_all()
        for user, host in rows:
            if user and host:
                logger.info(f"Dropping user {user}@{host}")
                session.run_sql("drop user if exists ?@?", [user, host])
    except mysqlsh.Error as e:
        if e.code in (mysql.ErrorCode.ER_BAD_DB_ERROR, mysql.ErrorCode.ER_NO_SUCH_TABLE):
            pass
        else:
            logger.error(f"Could not query for old InnoDB Cluster accounts: {e}")
            raise

    # drop metadata schema if there's one
    logger.info("Dropping cloned mysql_innodb_cluster_metadata schema")
    session.run_sql("drop schema if exists mysql_innodb_cluster_metadata")


def populate_with_clone(datadir, session, cluster, pod, logger):
    """
    Initialize the DB using clone.
    Server may be restarted multiple times but will be back up on return.
    After the clone:
    - If the donor had the metadata schema, it will be dropped.
    - Local accounts will be created if missing.
    - root password will be reset if needed
    """
    logger.info(f"Initializing mysql via clone...")

    start_time = session.run_sql("select now(6)").fetch_one()[0]

    # TODO monitor clone from a thread and dump progress
    # initdb.monitor_clone(session, start_time, logger)

    initdb.start_clone_seed_pod(session, cluster, pod, cluster.parsed_spec.initDB.clone, logger)

    logger.info("Waiting for mysqld to be restarted/shutdown by clone")

    logger.info("Restarting mysqld back up, this may take a while")

    # root credentials for the new instance are supposed to match in donor
    user, host, password = get_root_account_info(cluster)

    logger.info(f"Connecting as {user}")
    session = connect(user, password, logger)

    initdb.finish_clone_seed_pod(session, cluster, logger)

    # create local accounts again since the donor may not have them
    create_local_accounts(session, logger)

    # reset password of the IC admin account
    admin_user, admin_pass = cluster.get_admin_account()
    logger.info(f"Resetting password for {admin_user}@%")
    session.run_sql("SET PASSWORD FOR ?@'%'=?", [admin_user, admin_pass])

    wipe_old_innodb_cluster(session, logger)

    return session


def populate_with_dump(datadir, session, cluster, pod, logger):
    logger.info(f"Initializing mysql from a dump...")

    initdb.load_dump(session, cluster, pod, cluster.parsed_spec.initDB.dump, logger)

    wipe_old_innodb_cluster(session, logger)

    return session


def populate_db(datadir, session, cluster, pod, logger):
    """
    Populate DB from source specified in the cluster spec.
    Also creates main root account specified by user.

    mysqld may get restarted by clone.
    """
    if cluster.parsed_spec.initDB:
        if cluster.parsed_spec.initDB.clone:
            return populate_with_clone(datadir, session, cluster, pod, logger)
        elif cluster.parsed_spec.initDB.dump:
            return populate_with_dump(datadir, session, cluster, pod, logger)
        else:
            logger.warning("spec.initDB ignored because no supported initialization parameters found")

    # TODO move this to operator
    create_root_account(session, cluster, logger)

    return session


def get_root_account_info(cluster):
    secrets = cluster.get_user_secrets()
    if secrets: 
        user = secrets.data.get("rootUser")
        host = secrets.data.get("rootHost")
        password = secrets.data.get("rootPassword", None)
        if not password:
            raise Exception(f"rootPassword missing in secret {secrets.metadata['name']}")
        if user:
            user = utils.b64decode(user)
        else:
            user = "root"
        if host:
            host = utils.b64decode(host)
        else:
            host = "%"
        password = utils.b64decode(password)

        return user, host, password

    return None


def create_root_account(session, cluster, logger):
    """
    Create general purpose root account (owned by user) as specified by user.
    """
    info = get_root_account_info(cluster)
    if info:
        user, host, password = info

        if user != "root" or host != "localhost":
            session.run_sql("CREATE USER IF NOT EXISTS ?@? IDENTIFIED BY ?", [user, host, password])
            session.run_sql("GRANT ALL ON *.* TO ?@? WITH GRANT OPTION", [user, host])
            session.run_sql("GRANT PROXY ON ''@'' TO ?@? WITH GRANT OPTION", [user, host])
        if user == "root":
            session.run_sql("SET PASSWORD FOR root@localhost=?", [password])
            if host != "localhost":
                session.run_sql("SET PASSWORD FOR root@?=?", [host, password])


def create_admin_account(session, cluster, logger):
    """
    Create a super-user to be used by the operator.
    """
    host = "%"
    user, password = cluster.get_admin_account()
    logger.info(f"Creating account {user}@{host}")
    # binlog has to be disabled for this, because we need to create the account
    # independently in all instances (so that we can run configure on them),
    # which would cause diverging GTID sets
    session.run_sql("SET sql_log_bin=0")
    session.run_sql("CREATE USER IF NOT EXISTS ?@? IDENTIFIED BY ?", [user, host, password])
    session.run_sql("GRANT ALL ON *.* TO ?@? WITH GRANT OPTION", [user, host])
    session.run_sql("GRANT PROXY ON ''@'' TO ?@? WITH GRANT OPTION", [user, host])
    session.run_sql("SET sql_log_bin=1")



def connect(user, password, logger, timeout=60):
    shell = mysqlsh.globals.shell

    i = 0
    while timeout is None or i < timeout:
        try:
            shell.connect({"user":user, "password":password, "socket":"/var/run/mysql/mysql.sock"})
            break
        except mysqlsh.Error as e:
            if mysqlutils.is_client_error(e.code) or e.code == mysql.ErrorCode.ER_ACCESS_DENIED_ERROR:
                logger.info(f"Connect attempt #{i} failed: {e}")
                time.sleep(2)
            else:
                logger.critical(f"Unexpected MySQL error during connection: {e}")
                raise
        i += 1
    else:
        raise Exception("Could not connect to MySQL server after initialization")

    return mysqlsh.globals.session


def initialize(session, datadir, pod, cluster, logger):
    create_local_accounts(session, logger)

    create_admin_account(session, cluster, logger)

    user, password = cluster.get_admin_account()
    session = connect(user, password, logger)

    configure_for_innodb_cluster(mysqlsh.globals.dba, logger)

    # if this is the 1st pod of the cluster, then initialize it and create default accounts
    if pod.index == 0 and cluster.get_create_time() is None:
       session = populate_db(datadir, session, cluster, pod, logger)

    # # shutdown mysqld to let the definitive container start it back
    # logger.info("Shutting down mysql...")
    # session.run_sql("shutdown")


def helper(pod, cluster, logger):
    while True:
        import time
        time.sleep(1)


def main(argv):
    datadir = argv[1] if len(argv) > 1 else "/var/lib/mysql"

    mysqlsh.globals.shell.options.useWizards = False
    logging.basicConfig(level=logging.DEBUG, 
            format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
            datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("sidecar")
    name = os.getenv("MY_POD_NAME")
    namespace = os.getenv("MY_POD_NAMESPACE")
    pod = MySQLPod.read(name, namespace)
    cluster = pod.get_cluster()

    utils.log_banner(__file__, logger)

    # wait for mysqld to startup, since the sidecar and mysql containers are
    # started at the same time
    logger.info("Connecting to MySQL...")
    session = connect("root", "initpass", logger, timeout=None)

    logger.info(f"Bootstrapping mysql pod {namespace}/{name}, datadir={datadir}")

    try:
        initialize(session, datadir, pod, cluster, logger)
        logger.info("Bootstrap finished")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while bootstrapping MySQL: {e}")
        # TODO post event to the Pod and the Cluster object if this is the seed
        return 1

    logger.info("Waiting for Operator requests...")
    try:
        helper(pod, cluster, logger)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while bootstrapping MySQL: {e}")
        # TODO post event to the Pod and the Cluster object if this is the seed
        return 1
