# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


import mysqlsh
import sys
import os
import multiprocessing
from .controller import utils, config, shellutils
from .controller import storage_api
from .controller.backup.backup_api import MySQLBackup
from .controller.innodbcluster.cluster_api import InnoDBCluster
import logging
from typing import Optional

BACKUP_OCI_USER_NAME = "OCI_USER_NAME"
BACKUP_OCI_FINGERPRINT = "OCI_FINGERPRINT"
BACKUP_OCI_TENANCY = "OCI_TENANCY"
BACKUP_OCI_REGION = "OCI_REGION"
BACKUP_OCI_PASSPHRASE = "OCI_PASSPHRASE"
OCI_CONFIG_NAME = "OCI_CONFIG_NAME"
OCI_API_KEY_NAME = "OCI_API_KEY_NAME"
OCI_CONFIG_FILE_NAME = "config"


def get_dir_size(d):
    size = 0
    for dirpath, dirnames, filenames in os.walk(d):
        for f in filenames:
            size += os.path.getsize(os.path.join(dirpath, f))
    return size


def execute_dump_instance(backup_source, profile, backupdir, backup_name, logger : logging.Logger):
    shell = mysqlsh.globals.shell
    util = mysqlsh.globals.util

    start = utils.isotime()

    options = profile.dumpOptions.copy()
    if "threads" not in options:
        options["threads"] = multiprocessing.cpu_count()

    if profile.storage.ociObjectStorage:
        oci_config = create_oci_config_file_from_envs(os.environ, logger)
        options["osBucketName"] = profile.storage.ociObjectStorage.bucketName
        options["ociConfigFile"] = oci_config["config"]
        options["ociProfile"] = oci_config["profile"]
        logger.info(f"options={options}")
        if profile.storage.ociObjectStorage.prefix:
            output = os.path.join(
                profile.storage.ociObjectStorage.prefix, backup_name)
        else:
            output = backup_name
    else:
        output = os.path.join(backupdir, backup_name)

    logger.info(
        f"dump_instance starting: output={output}  options={options}  source={backup_source['user']}@{backup_source['host']}:{backup_source['port']}")

    try:
        shell.connect(backup_source)
    except mysqlsh.Error as e:
        logger.error(
            f"Could not connect to {backup_source['host']}:{backup_source['port']}: {e}")
        raise

    try:
        util.dump_instance(output, options)
    except mysqlsh.Error as e:
        logger.error(f"dump_instance failed: {e}")
        raise

    # TODO get backup size and other stats from the dump cmd itself

    if profile.storage.ociObjectStorage:
        tenancy = [line.split("=")[1].strip() for line in open(
            options["ociConfigFile"], "r").readlines() if line.startswith("tenancy")][0]

        info = {
            "method": "dump-instance/oci-bucket",
            "source": f"{backup_source['user']}@{backup_source['host']}:{backup_source['port']}",
            "bucket": profile.storage.ociObjectStorage.bucketName,
            "ociTenancy": tenancy
        }
    elif profile.storage.persistentVolumeClaim:
        fsinfo = os.statvfs(backupdir)
        gb_avail = (fsinfo.f_frsize * fsinfo.f_bavail) / (1024*1024*1024)
        backup_size = get_dir_size(output) / (1024*1024*1024)
        info = {
            "method": "dump-instance/volume",
            "source": f"{backup_source['user']}@{backup_source['host']}:{backup_source['port']}",
            "spaceAvailable": f"{gb_avail:.4}G",
            "size": f"{backup_size:.4}G"
        }
    else:
        assert False

    logger.info(f"dump_instance finished successfully")

    return info


def execute_clone_snapshot(backup_source, profile, backupdir: Optional[str], backup_name: str, logger: logging.Logger) -> dict:
    ...


def pick_source_instance(cluster, logger: logging.Logger):
    mysql = mysqlsh.mysql

    primary = None
    best_secondary = None
    best_secondary_applier_queue_size = None

    for pod in cluster.get_pods():
        if pod.deleting:
            continue
        try:
            with shellutils.DbaWrap(shellutils.connect_dba(pod.endpoint_co, logger, max_tries=3)) as dba:
                try:
                    tmp = dba.get_cluster().status({"extended": 1})["defaultReplicaSet"]
                    cluster_status = tmp["status"]
                    self_uuid = dba.session.run_sql("select @@server_uuid").fetch_one()[0]
                    member_status = [x for x in tmp["topology"].values() if x["memberId"] == self_uuid][0]
                except mysqlsh.Error as e:
                    logger.warning(
                        f"Could not get cluster status from {pod}: {e}")
                    continue
                applier_queue_size = dba.session.run_sql(
                    "SELECT COUNT_TRANSACTIONS_REMOTE_IN_APPLIER_QUEUE"
                    " FROM performance_schema.replication_group_member_stats"
                    " WHERE member_id = @@server_uuid").fetch_one()[0]
        except mysqlsh.Error as e:
            logger.warning(f"Could not connect to {pod}: {e}")
            continue

        logger.info(
            f"Cluster status from {pod} is {cluster_status}, member_status={member_status} applier_queue_size={applier_queue_size}")
        if not cluster_status.startswith("OK") or member_status["memberState"] != "ONLINE":
            continue

        if member_status["memberRole"] == "SECONDARY":
            if not best_secondary or applier_queue_size < best_secondary_applier_queue_size:
                best_secondary = pod.endpoint_co
                best_secondary_applier_queue_size = applier_queue_size

                if applier_queue_size == 0:
                    break
        else:
            primary = pod.endpoint_co

    if best_secondary:
        return best_secondary
    elif primary:
        return primary

    raise Exception(
        f"No instances available to backup from in cluster {cluster.name}")


def do_backup(backup : MySQLBackup, job_name: str, start, backupdir: Optional[str], logger: logging.Logger) -> dict:
    logger.info(
        f"Starting backup of {backup.namespace}/{backup.parsed_spec.clusterName}  profile={backup.parsed_spec.backupProfileName}  backupdir={backupdir}")

    cluster = backup.get_cluster()

    profile = backup.get_profile()

    backup_source = pick_source_instance(cluster, logger)

    if profile.dumpInstance:
        return execute_dump_instance(backup_source, profile.dumpInstance, backupdir, job_name, logger)
    elif profile.cloneSnapshot:
        return execute_clone_snapshot(backup_source, profile.cloneSnapshot, backupdir, job_name, logger)
    else:
        raise Exception(f"Invalid backup method in profile {profile.name}")

def create_oci_config_file_from_envs(env_vars: dict,  logger : logging.Logger) -> dict:
    backup_oci_user_name = env_vars.get(BACKUP_OCI_USER_NAME)
    backup_oci_fingerprint = env_vars.get(BACKUP_OCI_FINGERPRINT)
    backup_oci_tenancy = env_vars.get(BACKUP_OCI_TENANCY)
    backup_oci_region = env_vars.get(BACKUP_OCI_REGION)
    backup_oci_passphrase = env_vars.get(BACKUP_OCI_PASSPHRASE)
    oci_config_name = env_vars.get(OCI_CONFIG_NAME)
    oci_api_key_name = env_vars.get(OCI_API_KEY_NAME)

    if backup_oci_user_name is None:
        raise Exception(f"No env var {BACKUP_OCI_USER_NAME} passed")
    elif not backup_oci_user_name:
        raise Exception(f"Empty value for {BACKUP_OCI_USER_NAME} passed")

    if backup_oci_fingerprint is None:
        raise Exception(f"No env var {BACKUP_OCI_FINGERPRINT} passed")
    elif not backup_oci_fingerprint:
        raise Exception(f"Empty value for {BACKUP_OCI_FINGERPRINT} passed")

    if backup_oci_tenancy is None:
        raise Exception(f"No env var {BACKUP_OCI_TENANCY} passed")
    elif not backup_oci_tenancy:
        raise Exception(f"Empty value for {BACKUP_OCI_TENANCY} passed")

    if backup_oci_region is None:
        raise Exception(f"No env var {BACKUP_OCI_REGION} passed")
    elif not backup_oci_region:
        raise Exception(f"Empty value for {BACKUP_OCI_REGION} passed")

    if backup_oci_passphrase is None:
        raise Exception(f"No env var {BACKUP_OCI_PASSPHRASE} passed")

    if oci_config_name is None:
        raise Exception(f"No env var {OCI_CONFIG_NAME} passed")
    elif not oci_config_name:
        raise Exception(f"Empty value for {OCI_CONFIG_NAME} passed")
    elif os.path.isfile(oci_config_name):
        raise Exception(f"{oci_api_key_name} already exists, won't overwrite")

    if oci_api_key_name is None:
        raise Exception(f"No env var {OCI_API_KEY_NAME} passed")
    elif not oci_api_key_name:
        raise Exception(f"Empty value for {OCI_API_KEY_NAME} passed")
    elif not os.path.isfile(oci_api_key_name):
        raise Exception(f"{oci_api_key_name} is not a file")

    import configparser
    config_profile = "DEFAULT"
    config = configparser.ConfigParser()
    config[config_profile] = {
        "user" : backup_oci_user_name,
        "fingerprint" : backup_oci_fingerprint,
        "tenancy": backup_oci_tenancy,
        "region": backup_oci_region,
        "passphrase": backup_oci_passphrase,
        "key_file" : oci_api_key_name,
    }

    with open(oci_config_name, 'w') as configfile:
        config.write(configfile)

    return {
        "config": oci_config_name,
        "profile" : config_profile,
    }


def main(argv):
    import time
    import datetime

    debug = False

    logging.basicConfig(level=logging.DEBUG,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")

    # suppress logging from other libs
    for name in ['kubernetes']:
        logger = logging.getLogger(name)
        logger.propagate = debug
        if not debug:
            logger.handlers[:] = [logging.NullHandler()]

    logger = logging.getLogger("backup")
    ts = datetime.datetime.fromtimestamp(
        os.stat(__file__).st_mtime).isoformat()
    logger.info(f"backup  version={config.OPERATOR_VERSION}  timestamp={ts}")

    import subprocess
    subprocess.run(["ls", "-la", "/"])
    subprocess.run(["ls", "-l", "/.oci"])

    ns = argv[1]
    name = argv[2]
    jobname = argv[3]
    backupdir = os.environ.get('DUMP_MOUNT_PATH', None)
    logger.info(f"backupdir={backupdir}")
    if len(argv) > 4:
        backupdir = argv[4]

    start = utils.isotime()
    backup = MySQLBackup.read(name=name, namespace=ns)
    try:
        backup.set_started(jobname, start)

        info = do_backup(backup, jobname, start, backupdir, logger)

        backup.set_succeeded(jobname, start, utils.isotime(), info)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Backup failed with an exception: {e}")
        backup.set_failed(jobname, start, utils.isotime(), e)

        if debug:
            logger.info(f"Waiting for 1h...")
            time.sleep(60*60)

        return 1

    return 0
