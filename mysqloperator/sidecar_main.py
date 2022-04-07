# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
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
import os
import logging
import time
import asyncio
import kopf

from .controller import utils, mysqlutils, k8sobject
from .controller.innodbcluster import initdb
from .controller.innodbcluster.cluster_api import CloneInitDBSpec, DumpInitDBSpec, InnoDBCluster, MySQLPod
from .controller.kubeutils import api_core, client as api_client
from .controller.innodbcluster import router_objects

if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession

mysql = mysqlsh.mysql


k8sobject.g_component = "sidecar"
k8sobject.g_host = os.getenv("HOSTNAME")

g_cluster_name = None

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
    session.run_sql(
        "CREATE USER IF NOT EXISTS ?@? IDENTIFIED BY ?", [user, host, password])
    session.run_sql("GRANT ALL ON *.* TO ?@? WITH GRANT OPTION", [user, host])
    session.run_sql(
        "GRANT PROXY ON ''@'' TO ?@? WITH GRANT OPTION", [user, host])
    logger.info("Admin account created")


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
                logger.critical(
                    f"Unexpected MySQL error during connection: {e}")
                raise
        i += 1
    else:
        raise Exception(
            "Could not connect to MySQL server after initialization")

    assert mysqlsh.globals.session

    return mysqlsh.globals.session


def initialize(session, datadir: str, pod: MySQLPod, cluster, logger: Logger) -> None:
    session.run_sql("SET sql_log_bin=0")
    create_root_account(session, pod, cluster, logger)
    create_admin_account(session, cluster, logger)
    session.run_sql("SET sql_log_bin=1")

    user, password = cluster.get_admin_account()
    session = connect(user, password, logger)

    configure_for_innodb_cluster(mysqlsh.globals.dba, logger)

    # if this is the 1st pod of the cluster, then initialize it and create default accounts
    if pod.index == 0 and cluster.get_create_time() is None:
        session = populate_db(datadir, session, cluster, pod, logger)

    # # shutdown mysqld to let the definitive container start it back
    # logger.info("Shutting down mysql...")
    # session.run_sql("shutdown")


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
        logger.info(
            f"InnoDB Cluster metadata (version={mdver}) found, skipping configuration...")
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


def reconfigure_tls(enabled: bool, logger: Logger) -> None:
    has_crl = os.path.exists("/etc/mysql-ssl/crl.pem")

    logger.info(f"Ensuring custom TLS certificates are {'enabled' if enabled else 'disabled'} {'(with crl)' if has_crl else ''}")

    session = connect("localroot", "", logger, timeout=None)

    def ensure_sysvar(var, value):
        curval = session.run_sql("show variables like ?", [var]).fetch_one()[1]
        if curval != value:
            logger.debug(f"Changing {var} to {value}")
            session.run_sql(f"set persist {var} = ?", [value])

    # first ensure configured paths are correct
    if enabled:
        ensure_sysvar("ssl_ca", "/etc/mysql-ssl/ca.pem")
        ensure_sysvar("ssl_crl", "/etc/mysql-ssl/crl.pem" if has_crl else "")
        ensure_sysvar("ssl_cert", "/etc/mysql-ssl/tls.crt")
        ensure_sysvar("ssl_key", "/etc/mysql-ssl/tls.key")
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
        ensure_sysvar("group_replication_recovery_ssl_verify_server_cert", "OFF")
        ensure_sysvar("group_replication_ssl_mode", "REQUIRED")
        ensure_sysvar("group_replication_recovery_ssl_ca", "")
        ensure_sysvar("group_replication_recovery_ssl_cert", "")
        ensure_sysvar("group_replication_recovery_ssl_key", "")

    try:
        session.run_sql("ALTER INSTANCE RELOAD TLS")
    except Exception as e:
        logger.error(f"MySQL error reloading TLS certificates: {e}")

        session.close()
        return

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


def reload_tls(logger: Logger) -> None:
    logger.info("Reloading TLS")

    session = connect("localroot", "", logger, timeout=None)
    session.run_sql("ALTER INSTANCE RELOAD TLS")
    session.close()


def on_ca_secret_create_or_change(value: dict, useSelfSigned: bool, router_deployment: Optional[api_client.V1Deployment], logger: Logger):
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
            reconfigure_tls(False if useSelfSigned else True, logger)

            if router_deployment:
                # give time to all other sidecars to reload the TLS and then restart the router deployment from -0
                time.sleep(delay)
                logger.info("Updating router deployment with new TLS data. The deployment should restart")
                router_objects.restart_deployment_for_tls(router_deployment, tls_crt = None, tls_key = None, ca_pem = ca_pem, crl_pem = crl_pem, logger=logger)
            break
        else:
            logger.debug("Waiting for mounted TLS files to refresh...")
            time.sleep(delay)
            # TemporaryError was supposed to get this handler called again, but isn't
            # raise kopf.TemporaryError("TLS CA secret changed, but file didn't refresh yet")
    else:
        raise kopf.PermanentError("Timeout waiting for TLS files to get refreshed")


def on_tls_secret_create_or_change(value: dict, useSelfSigned: bool, router_deployment: Optional[api_client.V1Deployment], logger: Logger):
    logger.info("on_tls_secret_create_or_change")

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
            reconfigure_tls(False if useSelfSigned else True, logger)

            if router_deployment:
                # give time to all other sidecars to reload the TLS and then restart the router deployment from -0
                time.sleep(delay)
                logger.info("Updating router deployment with new TLS data. The deployment should restart")
                router_objects.restart_deployment_for_tls(router_deployment, tls_crt = tls_crt, tls_key = tls_key, ca_pem = None, crl_pem = None, logger=logger)
            break
        else:
            logger.info("Waiting for mounted TLS files to refresh...")
            time.sleep(delay)
            #raise kopf.TemporaryError("TLS certificate secret changed, but file didn't refresh yet")
    else:
        raise kopf.PermanentError("Timeout waiting for TLS files to get refreshed")


@kopf.on.create("", "v1", "secrets") # type: ignore
@kopf.on.update("", "v1", "secrets") # type: ignore
def on_secret_create_or_update(name: str, namespace: str, spec, new, logger: Logger, **kwargs):
    global g_cluster_name

    logger.info(f"on_secret_create_or_update {namespace}/{name}")

    my_namespace = cast(str, os.getenv("MY_POD_NAMESPACE"))

    if namespace == my_namespace:
        my_pod_name = cast(str, os.getenv("MY_POD_NAME"))
        pod = MySQLPod.read(my_pod_name, my_namespace)
        ic = InnoDBCluster.read(my_namespace, g_cluster_name)
        handler = None
        if ic.parsed_spec.tlsCASecretName == name:
            handler = on_ca_secret_create_or_change
        elif ic.parsed_spec.tlsSecretName == name:
            handler = on_tls_secret_create_or_change
        else:
            logger.info(f"Secret {namespace}/{name} doesn't belong to this cluster")

        if handler:
            router_deployment = ic.get_router_deployment() if pod.index == 0 else None
            handler(new, ic.parsed_spec.tlsUseSelfSigned, router_deployment , logger)


@kopf.on.startup()
def configure(settings: kopf.OperatorSettings, logger: Logger, *args, **_):
    logger.info("sidecar: configure()")
    settings.peering.standalone = True


def main(argv):
    global g_cluster_name

    datadir = argv[1] if len(argv) > 1 else "/var/lib/mysql"

    kopf.configure(verbose=True if os.getenv("MYSQL_OPERATOR_DEBUG")=="1" else False)

    mysqlsh.globals.shell.options.useWizards = False
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("sidecar")
    utils.log_banner(__file__, logger)

    name = cast(str, os.getenv("MY_POD_NAME"))
    namespace = cast(str, os.getenv("MY_POD_NAMESPACE"))
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

    if r == 0:
        # refresh TLS settings if we're restarting in case something changed
        reconfigure_tls(False if cluster.parsed_spec.tlsUseSelfSigned else True, logger)

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
