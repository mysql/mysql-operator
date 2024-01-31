# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# Bootstrap sequence:

# 1 - initconf (initContainer)
# 1.1 - initializes MySQL configuration files
#
# 2 - initmysql (initContainer) - via docker container entrypoint script
# 2.1 - initializes MySQL datadir
# 2.2 - create default root account
# 2.3 - create localroot
#
# 3 - mysql - via container entrypoint:
# 3.1 - start mysqld
#
# 4 - sidecar:
# 4.1 - configureInstance()
# 4.2 - initialize db (clone, loadDump etc)
# 4.3 - restart (optional)
# 4.4 - mark as ready
# # TODO move some stuff to initdatadir?

# Initializes a MySQL Pod to be used in an InnoDBCluster managed by Kubernetes
#
# This performs the following tasks and runs instead of the regular
# initialization done by the Docker image entry point:
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

import base64
from logging import Logger
from typing import Optional, TYPE_CHECKING, Tuple, cast
import mysqlsh
import sys
import os
import logging
import time
import asyncio
import argparse
import kopf
from threading import Lock

from .controller import utils, mysqlutils, k8sobject
from .controller.api_utils import Edition
from .controller.innodbcluster import initdb
from .controller.innodbcluster.cluster_api import CloneInitDBSpec, DumpInitDBSpec, InnoDBCluster, MySQLPod
from .controller.kubeutils import api_core, client as api_client
from .controller.innodbcluster import router_objects
from .controller.plugins import install_enterprise_plugins, install_enterprise_encryption, install_keyring_udf

if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession

mysql = mysqlsh.mysql


k8sobject.g_component = "sidecar"
k8sobject.g_host = os.getenv("HOSTNAME")

g_cluster_name = None
g_pod_index = 0
g_pod_name = None
g_pod_namespace = None
g_tls_change_underway = False
g_ca_change_underway = False
g_ca_tls_change_underway_lock = Lock()

g_ready_gate = False
g_ready_gate_lock = Lock()

# The time it takes for mysqld to restart after a clone can be very long,
# because it has to apply redo logs. OTOH we monitor the error log to see
# if there's any activity happening, so the timeout is for activity to happen
# not the total time it takes for the server to start.
CLONE_RESTART_TIMEOUT = 60*10

# Path to a file created to indicate server bootstrap was done
BOOTSTRAP_DONE_FILE = "/var/run/mysql/bootstrap-done"


def create_local_accounts(session, logger):
    """
    Creates:
    - a localroot@localhost account with auth_socket authentication.
    """
    sql = [
        "SET sql_log_bin=0;",
        "CREATE USER IF NOT EXISTS localroot@localhost IDENTIFIED WITH auth_socket AS 'daemon';",
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
    logger.info("Instance configured")


def wipe_old_innodb_cluster(session, logger):
    # drop innodb cluster accounts
    try:
        rows = session.run_sql(
            "select attributes->>'$.recoveryAccountUser', attributes->>'$.recoveryAccountHost' from mysql_innodb_cluster_metadata.v2_instances").fetch_all()
        for user, host in rows:
            if user and host:
                logger.info(f"Dropping user {user}@{host}")
                session.run_sql("drop user if exists ?@?", [user, host])
    except mysqlsh.Error as e:
        if e.code in (mysql.ErrorCode.ER_BAD_DB_ERROR, mysql.ErrorCode.ER_NO_SUCH_TABLE):
            pass
        else:
            logger.error(
                f"Could not query for old InnoDB Cluster accounts: {e}")
            raise

    # drop metadata schema if there's one
    logger.info("Dropping cloned mysql_innodb_cluster_metadata schema")
    session.run_sql("drop schema if exists mysql_innodb_cluster_metadata")


def populate_with_clone(datadir: str, session: 'ClassicSession', cluster: InnoDBCluster, clone_spec: CloneInitDBSpec, pod: MySQLPod, logger: Logger):
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

    logger.info(f"Starting at {start_time}")
    # TODO monitor clone from a thread and dump progress
    # initdb.monitor_clone(session, start_time, logger)

    initdb.start_clone_seed_pod(
        session, cluster, pod, clone_spec, logger)

    logger.info("Waiting for mysqld to be restarted/shutdown by clone")

    logger.info("Restarting mysqld back up, this may take a while")

    # root credentials for the new instance are supposed to match in donor
    try:
        user, host, password = get_root_account_info(cluster)
    except Exception as e:
        pod.error(action="InitDB", reason="InvalidArgument", message=f"{e}")
        raise

    logger.info(f"Connecting as {user}@{host}")
    session = connect(user, password, logger)

    initdb.finish_clone_seed_pod(session, cluster, logger)

    # create local accounts again since the donor may not have them
    create_local_accounts(session, logger)

    # reset password of the IC admin account
    admin_user, admin_pass = cluster.get_admin_account()
    logger.info(f"Resetting password for {admin_user}@%")
    session.run_sql("SET PASSWORD FOR ?@'%'=?", [admin_user, admin_pass])

    # recreate metrics user if needed
    create_metrics_account(session, cluster, logger)

    wipe_old_innodb_cluster(session, logger)

    return session


def populate_with_dump(datadir: str, session: 'ClassicSession', cluster: InnoDBCluster, init_spec: DumpInitDBSpec, pod: MySQLPod, logger: Logger):
    logger.info(f"Initializing mysql from a dump...")

    initdb.load_dump(session, cluster, pod, init_spec, logger)

    # create local accounts again since the donor may not have them
    create_local_accounts(session, logger)

    # reset password of the IC admin account
    admin_user, admin_pass = cluster.get_admin_account()
    logger.info(f"Resetting password for {admin_user}@%")
    session.run_sql("SET PASSWORD FOR ?@'%'=?", [admin_user, admin_pass])

    wipe_old_innodb_cluster(session, logger)

    return session


def populate_db(datadir, session, cluster, pod, logger: Logger) -> 'ClassicSession':
    """
    Populate DB from source specified in the cluster spec.
    Also creates main root account specified by user.

    mysqld may get restarted by clone.
    """
    if cluster.parsed_spec.initDB:
        if cluster.parsed_spec.initDB.clone:
            logger.info("Populate with clone")
            return populate_with_clone(datadir, session, cluster, cluster.parsed_spec.initDB.clone, pod, logger)
        elif cluster.parsed_spec.initDB.dump:
            logger.info("Populate with dump")
            return populate_with_dump(datadir, session, cluster, cluster.parsed_spec.initDB.dump, pod, logger)
        else:
            logger.warning(
                "spec.initDB ignored because no supported initialization parameters found")

    create_root_account(session, pod, cluster, logger)

    return session


def get_root_account_info(cluster: InnoDBCluster) -> Tuple[str, str, str]:
    secrets = cluster.get_user_secrets()
    if secrets:
        user = secrets.data.get("rootUser")
        host = secrets.data.get("rootHost")
        password = secrets.data.get("rootPassword", None)
        if not password:
            raise Exception(
                f"rootPassword missing in secret {cluster.parsed_spec.secretName}")
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

    raise Exception(
        f"Could not get secret '{cluster.parsed_spec.secretName}' with for root account information for {cluster.namespace}/{cluster.name}")


def create_root_account(session: 'ClassicSession', pod: MySQLPod, cluster: InnoDBCluster, logger: Logger) -> None:
    """
    Create general purpose root account (owned by user) as specified by user.
    """
    try:
        user, host, password = get_root_account_info(cluster)
    except Exception as e:
        pod.error(action="InitDB", reason="InvalidArgument", message=f"{e}")
        raise

    if user == "root" and host == "localhost":
        # Nothing to do here, password was already set by the container
        pass
    else:
        logger.info(f"Creating root account {user}@{host}")
        session.run_sql(
            "CREATE USER IF NOT EXISTS ?@? IDENTIFIED BY ?", [user, host, password])
        session.run_sql(
            "GRANT ALL ON *.* TO ?@? WITH GRANT OPTION", [user, host])
        session.run_sql(
            "GRANT PROXY ON ''@'' TO ?@? WITH GRANT OPTION", [user, host])
        # Drop the default root account and keep the new one only
        session.run_sql("DROP USER IF EXISTS root@localhost")


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
    session.run_sql("CREATE USER IF NOT EXISTS ?@? IDENTIFIED BY ?", [user, host, password])
    session.run_sql("GRANT ALL ON *.* TO ?@? WITH GRANT OPTION", [user, host])
    session.run_sql("GRANT PROXY ON ''@'' TO ?@? WITH GRANT OPTION", [user, host])
    logger.info("Admin account created")


def create_metrics_account(session: 'ClassicSession', cluster: InnoDBCluster, logger: Logger):
    """
    Create a user for metrics, if needed
    """
    if not cluster.parsed_spec.metrics or not cluster.parsed_spec.metrics.enable:
        return

    host = "localhost"
    user = cluster.parsed_spec.metrics.dbuser_name
    max_connections = cluster.parsed_spec.metrics.dbuser_max_connections
    grants = cluster.parsed_spec.metrics.dbuser_grants

    logger.info(f"Creating account {user}@{host}")
    # binlog has to be disabled for this, because we need to create the account
    # independently in all instances (so that metrics are available even on later config failure),
    # which would cause diverging GTID sets
    mysqlutils.setup_metrics_user(session, user, grants, max_connections)
    logger.info("Metrics account created")


def connect(user: str, password: str, logger: Logger, timeout: Optional[int] = 60) -> 'ClassicSession':
    shell = mysqlsh.globals.shell

    i = 0
    while timeout is None or i < timeout:
        try:
            shell.connect(
                {"user": user, "password": password, "scheme": "mysql"})
            break
        except mysqlsh.Error as e:
            if mysqlutils.is_client_error(e.code):
                logger.info(f"Connect attempt #{i} failed: {e}")
                time.sleep(2)
            else:
                logger.critical(f"Unexpected MySQL error during connection: {e}")
                raise
        i += 1
    else:
        raise Exception("Could not connect to MySQL server after initialization")

    assert mysqlsh.globals.session

    return mysqlsh.globals.session


def initialize(session, datadir: str, pod: MySQLPod, cluster: InnoDBCluster, logger: Logger) -> None:
    session.run_sql("SET sql_log_bin=0")
    create_root_account(session, pod, cluster, logger)
    create_admin_account(session, cluster, logger)
    create_metrics_account(session, cluster, logger)
    session.run_sql("SET sql_log_bin=1")

    user, password = cluster.get_admin_account()
    session = connect(user, password, logger)

    configure_for_innodb_cluster(mysqlsh.globals.dba, logger)

    if pod.index == 0 and cluster.get_create_time() is None:
        # if this is the 1st pod of the cluster, then initialize it and create default accounts
        session = populate_db(datadir, session, cluster, pod, logger)

    session.run_sql("SET sql_log_bin=0")
    old_read_only = session.run_sql("SELECT @@super_read_only").fetch_one()[0]
    session.run_sql("SET GLOBAL super_read_only=0")

    try:
        # Some commands like INSTALL [PLUGIN|COMPONENT] are not being
        # replicated we run them on any restart, those have to be idempotent

        # With enterprise edition activate enterprise plugins
        if cluster.parsed_spec.edition == Edition.enterprise:
            install_enterprise_plugins(cluster.parsed_spec.version, session, logger)

        # If a Keyring setup is requested install keyring UDFs
        if "keyring" in cluster.spec:
            print(f"KEYRING: {cluster.spec['keyring']}")
            install_keyring_udf(cluster.parsed_spec.version, session, logger)
    finally:
        session.run_sql("SET GLOBAL super_read_only=?", [old_read_only])
        session.run_sql("SET sql_log_bin=1")


def metadata_schema_version(session: 'ClassicSession', logger: Logger) -> Optional[str]:
    try:
        r = session.run_sql(
            "select * from mysql_innodb_cluster_metadata.schema_version").fetch_one()
        return r[0]
    except Exception as e:
        logger.debug(f"Metadata check failed: {e}")
        return None


def bootstrap(pod: MySQLPod, datadir: str, logger: Logger) -> int:
    """
    Prepare MySQL instance for InnoDB cluster.

    This function must be idempotent, because the sidecar container can get
    restarted in many different scenarios. It's also possible that the
    whole pod gets deleted and recreated while its underlying PV is reused.
    In that case, the Pod will look brand new (so we can't rely on any data
    stored in the Pod object), but the instance will be already prepared and
    not be in the expected initial state with initial defaults.

    Returns 1 if bootstrapped, 0 if already configured and -1 on error
    """
    name = pod.name
    namespace = pod.namespace

    # Check if the Pod is already configured according to itself
    gate = pod.get_member_readiness_gate("configured")
    if gate:
        logger.info(f"MySQL server was already initialized configured={gate}")
        return 0

    # Connect using localroot and check if the metadata schema already exists

    # note: we may have to wait for mysqld to startup, since the sidecar and
    # mysql containers are started at the same time.
    session = connect("localroot", "", logger, timeout=None)

    mdver = metadata_schema_version(session, logger)
    if mdver:
        logger.info(f"InnoDB Cluster metadata (version={mdver}) found, skipping configuration...")
        pod.update_member_readiness_gate("configured", True)
        return 0

    # Check if the datadir already existed

    logger.info(
        f"Configuring mysql pod {namespace}/{name}, configured={gate} datadir={datadir}")

    try:
        initialize(session, datadir, pod, pod.get_cluster(), logger)

        pod.update_member_readiness_gate("configured", True)

        logger.info("Configuration finished")
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while bootstrapping MySQL: {e}")
        # TODO post event to the Pod and the Cluster object if this is the seed
        return -1

    session.close()

    return 1

def ensure_correct_tls_sysvars(pod: MySQLPod, session, enabled: bool, caller: str, logger: Logger) -> None:
    has_crl = os.path.exists("/etc/mysql-ssl/crl.pem")

    logger.info(f"Ensuring custom TLS certificates are {'enabled' if enabled else 'disabled'} {'(with crl)' if has_crl else ''} caller={caller}")

    def ensure_sysvar(var, value):
        logger.info(f"\tChecking if {var} is [{value}]")
        res = session.run_sql("SHOW VARIABLES LIKE ?", [var])
        row = res.fetch_one()
        if row:
            curval = row[1]
            if curval != value:
                logger.info(f"\t{var} is [{curval}] persisting to [{value}]")
                session.run_sql(f"SET PERSIST {var} = ?", [value])
        else:
            raise kopf.PermanentError(f"Variable {var} not found!")

    # first ensure configured paths are correct
    if enabled:
        ensure_sysvar("ssl_ca", "/etc/mysql-ssl/ca.pem")
        ensure_sysvar("ssl_crl", "/etc/mysql-ssl/crl.pem" if has_crl else "")
        ensure_sysvar("ssl_cert", "/etc/mysql-ssl/tls.crt")
        ensure_sysvar("ssl_key", "/etc/mysql-ssl/tls.key")
        if pod.instance_type == "group-member":
            ensure_sysvar("group_replication_recovery_ssl_verify_server_cert", "ON")
            ensure_sysvar("group_replication_ssl_mode", "VERIFY_IDENTITY")
            ensure_sysvar("group_replication_recovery_ssl_ca", "/etc/mysql-ssl/ca.pem")
            ensure_sysvar("group_replication_recovery_ssl_cert", "/etc/mysql-ssl/tls.crt")
            ensure_sysvar("group_replication_recovery_ssl_key", "/etc/mysql-ssl/tls.key")
    else:
        ensure_sysvar("ssl_ca", "ca.pem")
        ensure_sysvar("ssl_crl", "")
        ensure_sysvar("ssl_cert", "server-cert.pem")
        ensure_sysvar("ssl_key", "server-key.pem")
        if pod.instance_type == "group-member":
            ensure_sysvar("group_replication_recovery_ssl_verify_server_cert", "OFF")
            ensure_sysvar("group_replication_ssl_mode", "REQUIRED")
            ensure_sysvar("group_replication_recovery_ssl_ca", "")
            ensure_sysvar("group_replication_recovery_ssl_cert", "")
            ensure_sysvar("group_replication_recovery_ssl_key", "")


def reconfigure_tls(pod: MySQLPod, enabled: bool, caller: str, logger: Logger) -> None:

    session = connect("localroot", "", logger, timeout=None)

    ensure_correct_tls_sysvars(pod, session, enabled, caller, logger)

    try:
        logger.info("Reloading TLS")
        session.run_sql("ALTER INSTANCE RELOAD TLS")
    except Exception as e:
        logger.error(f"MySQL error reloading TLS certificates: {e}")
    finally:
        session.close()


def check_secret_mounted(secrets: dict, paths: list, logger: Logger) -> bool:
    logger.info("check_secret_mounted")

    for path in paths:
        secret_name = path.split("/")[-1]
        secret_value = secrets[secret_name]
        if not secret_value is None:
            if os.path.exists(path):
                dataf = open(path).read()
                if dataf != secret_value:
                    logger.info(f"check_secret_mounted: No match for {secret_name}")
                    return False
                logger.info(f"check_secret_mounted: {secret_name} matches")
            else:
                logger.info(f"check_secret_mounted: Path to secret {secret_name} doesn't exist")
                return False
        else:
            logger.info(f"check_secret_mounted: Not checking {path}, expected None value")

    logger.info(f"check_secret_mounted: Success")
    return True


def on_ca_secret_create_or_change(value: dict, useSelfSigned: bool, router_deployment: Optional[api_client.V1Deployment], logger: Logger) -> None:
    global g_pod_name
    global g_pod_namespace

    logger.info("on_ca_secret_create_or_change")

    ca_pem = utils.b64decode(value['data']['ca.pem']) if 'ca.pem' in value['data'] else None
    crl_pem = utils.b64decode(value['data']['crl.pem']) if 'crl.pem' in value['data'] else None
    secrets = {'ca.pem': ca_pem, 'crl.pem': crl_pem}

    max_time = 7 * 60
    delay = 5
    for _ in range(max_time//delay):
        if check_secret_mounted(secrets,
                                ["/etc/mysql-ssl/ca.pem",
                                 "/etc/mysql-ssl/crl.pem"],
                                logger):
            logger.info(f"TLS CA file change detected, reloading TLS configurations")
            pod = MySQLPod.read(g_pod_name, g_pod_namespace)
            reconfigure_tls(pod, False if useSelfSigned else True, "on_ca_secret_create_or_change", logger)

            if router_deployment:
                # give time to all other sidecars to reload the TLS and then restart the router deployment from -0
                time.sleep(delay)
                router_objects.restart_deployment_for_tls(router_deployment, router_tls_crt = None, router_tls_key = None, ca_pem = ca_pem, crl_pem = crl_pem, logger=logger)
            break
        else:
            logger.debug("Waiting for mounted TLS files to refresh...")
            time.sleep(delay)
            # TemporaryError was supposed to get this handler called again, but isn't
            # raise kopf.TemporaryError("TLS CA secret changed, but file didn't refresh yet")
    else:
        raise kopf.PermanentError("Timeout waiting for TLS files to get refreshed")


def on_tls_secret_create_or_change(value: dict, useSelfSigned: bool, router_deployment: Optional[api_client.V1Deployment], logger: Logger) -> None:
    global g_pod_name
    global g_pod_namespace

    logger.info("on_tls_secret_create_or_change")

    pod = MySQLPod.read(g_pod_name, g_pod_namespace)

    tls_crt = utils.b64decode(value['data']['tls.crt']) if 'tls.crt' in value['data'] else None
    tls_key = utils.b64decode(value['data']['tls.key']) if 'tls.key' in value['data'] else None
    secrets = {'tls.crt': tls_crt, 'tls.key': tls_key}

    max_time = 7 * 60
    delay = 5
    for _ in range(max_time//delay):
        if check_secret_mounted(secrets,
                                ["/etc/mysql-ssl/tls.crt",
                                 "/etc/mysql-ssl/tls.key"],
                                logger):
            logger.info(f"TLS certificate file change detected, reloading TLS configurations")
            reconfigure_tls(pod, False if useSelfSigned else True, "on_tls_secret_create_or_change", logger)
            break
        else:
            logger.info("Waiting for mounted TLS files to refresh...")
            time.sleep(delay)
            #raise kopf.TemporaryError("TLS certificate secret changed, but file didn't refresh yet")
    else:
        raise kopf.PermanentError("Timeout waiting for TLS files to get refreshed")


def on_router_tls_secret_create_or_change(value: dict, useSelfSigned: bool, router_deployment: Optional[api_client.V1Deployment], logger: Logger) -> None:
    logger.info("on_router_tls_secret_create_or_change")

    if router_deployment:
        router_tls_crt = utils.b64decode(value['data']['tls.crt']) if 'tls.crt' in value['data'] else None
        router_tls_key = utils.b64decode(value['data']['tls.key']) if 'tls.key' in value['data'] else None
        router_objects.restart_deployment_for_tls(router_deployment, router_tls_crt = router_tls_crt, router_tls_key = router_tls_key, ca_pem = None, crl_pem = None, logger=logger)


def secret_belongs_to_the_cluster_checker(namespace:str, name, **_) -> bool:
    # This should be always true but some precaution won't hurt
    # It is true when the sidecar runs as standalone operator. If for some reason it doesn't
    # that it will listen to all namespaces and then this won't hold true all the time
    if namespace == g_pod_namespace:
        ic = InnoDBCluster.read(namespace, g_cluster_name)
        return name in (ic.parsed_spec.tlsCASecretName, ic.parsed_spec.tlsSecretName, ic.parsed_spec.router.tlsSecretName)
    return False


@kopf.on.create("", "v1", "secrets", when=secret_belongs_to_the_cluster_checker) # type: ignore
@kopf.on.update("", "v1", "secrets", when=secret_belongs_to_the_cluster_checker) # type: ignore
def on_secret_create_or_update(name: str, namespace: str, spec, new, logger: Logger, **kwargs):
    # g_cluster_name
    # g_pod_index
    global g_tls_change_underway
    global g_ca_change_underway
    global g_ca_tls_change_underway_lock
    global g_ready_gate
    global g_ready_gate_lock

    logger.info(f"on_secret_create_or_update: name={name} pod_index={g_pod_index}")

    try:
        g_ready_gate_lock.acquire()
        if not g_ready_gate:
            logger.info("Cached value of gate[ready] is false, re-checking")
            ready = MySQLPod.read(g_pod_name, g_pod_namespace).get_member_readiness_gate("ready")
            if not ready:
                raise kopf.TemporaryError(f"Pod not ready - not yet part of the IC. Will retry", delay=15)
            g_ready_gate = True
            logger.info("Readiness gate 'ready' is true. Handling event.")
    finally:
        g_ready_gate_lock.release()

    ic = InnoDBCluster.read(namespace, g_cluster_name)
    tls_changed = False
    ca_changed = False
    handler = None
    router_deployment = None
    # In case the same secret is used for CA and TLS, and router TLS, then the order
    # here is very important. on_ca_secret_create_or_change() does what
    # on_tls_secret_create_or_change() does and restarts the deployment on top
    # So, either this order of checks or three separate if-statements.
    if ic.parsed_spec.tlsCASecretName == name:
        logger.info(f"on_secret_create_or_update: tlsCASecretName")
        g_ca_tls_change_underway_lock.acquire()
        try:
            if g_tls_change_underway:
                raise kopf.TemporaryError(f"TLS change underway. Wait to finish. {name}", delay=12)
            g_ca_change_underway = True
            ca_changed = True
        finally:
            g_ca_tls_change_underway_lock.release()

        handler = on_ca_secret_create_or_change
        router_deployment = ic.get_router_deployment() if g_pod_index == 0 else None
    elif ic.parsed_spec.tlsSecretName == name:
        logger.info(f"on_secret_create_or_update: tlsSecretName")
        g_ca_tls_change_underway_lock.acquire()
        try:
            if g_ca_change_underway:
                raise kopf.TemporaryError(f"CA change underway. Wait to finish. {name}", delay=14)
            g_tls_change_underway = True
            tls_changed = True
        finally:
            g_ca_tls_change_underway_lock.release()

        handler = on_tls_secret_create_or_change
    elif ic.parsed_spec.router.tlsSecretName == name:
        logger.info(f"on_secret_create_or_update: router.tlsSecretName")
        try:
            g_ca_tls_change_underway_lock.acquire()
            if g_ca_change_underway:
                raise kopf.TemporaryError(f"CA change underway. Wait to finish. {name}", delay=16)
            else:
                handler = on_router_tls_secret_create_or_change
                router_deployment = ic.get_router_deployment() if g_pod_index == 0 else None
        finally:
            g_ca_tls_change_underway_lock.release()

    if handler:
        try:
            handler(new, ic.parsed_spec.tlsUseSelfSigned, router_deployment , logger)
        finally:
            if ca_changed:
                g_ca_tls_change_underway_lock.acquire()
                g_ca_change_underway = False
                g_ca_tls_change_underway_lock.release()
            if tls_changed:
                g_ca_tls_change_underway_lock.acquire()
                g_tls_change_underway = False
                g_ca_tls_change_underway_lock.release()


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, logger: Logger, *args, **_):
    logger.info("sidecar: configure()")
    settings.peering.standalone = True


def main(argv):
    global g_cluster_name
    global g_pod_index
    global g_pod_name
    global g_pod_namespace

    # const - when there is an argument without value
    # default - when there is no argument at all
    # nargs = "?" - zero or one arguments
    # nargs = "+" - one or more arguments, returns a list()
    # nargs = 8 - 8 arguments will be consumed
    # nargs = 1 - 1 argument will be consumed, returns a list with one element
    parser = argparse.ArgumentParser(description = "MySQL InnoDB Cluster Instance Sidecar Container")
    parser.add_argument('--debug',  type = int, nargs="?", const = 1, default = 0, help = "Debug")
    parser.add_argument('--logging-level', type = int, nargs="?", default = logging.INFO, help = "Logging Level")
    parser.add_argument('--pod-name', type = str, default = "", help = "Pod Name")
    parser.add_argument('--pod-namespace', type = str, default = "", help = "Pod Namespace")
    parser.add_argument('--datadir', type = str, nargs=1, help = "Path do data directory")
    args = parser.parse_args(argv)

    datadir = args.datadir

    kopf.configure(verbose=True if args.debug != 0 else False)

    mysqlsh.globals.shell.options.useWizards = False
    logging.basicConfig(level=args.logging_level,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("sidecar")
    utils.log_banner(__file__, logger)

    g_pod_namespace = args.pod_namespace
    g_pod_name = args.pod_name

    name = args.pod_name
    namespace = args.pod_namespace
    pod = MySQLPod.read(name, namespace)
    logger.info(f"My pod is {name} in {namespace}")

    logger.info("Bootstrapping")
    r = bootstrap(pod, datadir, logger)
    if r < 0:
        logger.info(f"Bootstrap error {r}")
        return abs(r)

    cluster = pod.get_cluster()
    cluster.log_tls_info(logger)

    g_cluster_name = cluster.name
    g_pod_index = pod.index

    if r == 0:
        # refresh TLS settings if we're restarting in case something changed
        reconfigure_tls(pod, False if cluster.parsed_spec.tlsUseSelfSigned else True, "main", logger)

    logger.info("Starting Operator request handler...")
    try:
        loop = asyncio.get_event_loop()

        loop.run_until_complete(kopf.operator(namespace=namespace))
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.critical(f"Unhandled exception while bootstrapping MySQL: {e}")
        # TODO post event to the Pod and the Cluster object if this is the seed
        return 1
