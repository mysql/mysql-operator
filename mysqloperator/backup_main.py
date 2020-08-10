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


import mysqlsh
import sys
import os
import multiprocessing
from .controller import utils, config, shellutils
from .controller import storage_api
from .controller.backup.backup_api import MySQLBackup
from .controller.innodbcluster.cluster_api import InnoDBCluster
import logging

def get_dir_size(d):
    size = 0
    for dirpath, dirnames, filenames in os.walk(d):
        for f in filenames:
            size += os.path.getsize(os.path.join(dirpath, f))
    return size


def execute_dump_instance(backup_source, profile, backupdir, backup_name, logger):
    shell = mysqlsh.globals.shell
    util = mysqlsh.globals.util

    start = utils.isotime()

    options = profile.dumpOptions.copy()
    if "threads" not in options:
        options["threads"] = multiprocessing.cpu_count()

    if profile.storage.ociObjectStorage:
        options["osBucketName"] = profile.storage.ociObjectStorage.bucketName
        options["ociConfigFile"] = "/.oci/config"
        options["ociProfile"] = "DEFAULT"

        if profile.storage.ociObjectStorage.prefix:
            output = os.path.join(profile.storage.ociObjectStorage.prefix, backup_name)
        else:
            output = backup_name
    else:
        output = os.path.join(backupdir, backup_name)

    logger.info(f"dump_instance starting: output={output}  options={options}  source={backup_source['user']}@{backup_source['host']}:{backup_source['port']}")

    try:
        shell.connect(backup_source)
    except mysqlsh.Error as e:
        logger.error(f"Could not connect to {backup_source['host']}:{backup_source['port']}: {e}")
        raise

    try:
        util.dump_instance(output, options)
    except mysqlsh.Error as e:
        logger.error(f"dump_instance failed: {e}")
        raise

    # TODO get backup size and other stats from the dump cmd itself

    if profile.storage.ociObjectStorage:
        tenancy = [line.split("=")[1].strip() for line in open("/.oci/config", "r").readlines() if line.startswith("tenancy")][0]

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

    logger.info(f"dump_instance finished successfully")

    return info


def execute_clone_snapshot(backup_source, profile, backupdir, backup_name, logger):
    pass


def pick_source_instance(cluster, logger):
    mysql = mysqlsh.globals.mysql

    primary = None
    best_secondary = None
    best_secondary_applier_queue_size = None

    for pod in cluster.get_pods():
        if pod.deleting:
            continue
        try:
            with shellutils.connect_dba(pod.endpoint_co, logger, max_tries=3) as dba:
                try:
                    status = dba.get_cluster().member_status({"extended":1})
                except mysqlsh.Error as e:
                    logger.warning(f"Could not get cluster status from {pod}: {e}")
                    continue
                applier_queue_size = dba.session.run_sql(
                    "SELECT COUNT_TRANSACTIONS_REMOTE_IN_APPLIER_QUEUE"
                    " FROM performance_schema.replication_group_member_stats"
                    " WHERE member_id = @@server_uuid").fetch_one()[0]
        except mysqlsh.Error as e:
            logger.warning(f"Could not connect to {pod}: {e}")
            continue

        logger.info(f"Cluster status from {pod} is {status}, applier_queue_size={applier_queue_size}")
        if not status["clusterStatus"].startswith("OK") or status["memberState"] != "ONLINE":
            continue

        if status["memberRole"] == "SECONDARY":
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

    raise Exception(f"No instances available to backup from in cluster {cluster.name}")


def do_backup(backup, job_name, start, backupdir, logger):
    logger.info(f"Starting backup of {backup.namespace}/{backup.parsed_spec.clusterName}  profile={backup.parsed_spec.backupProfileName}  backupdir={backupdir}")

    cluster = backup.get_cluster()

    profile = cluster.parsed_spec.get_backup_profile(backup.parsed_spec.backupProfileName)
    if not profile:
        raise Exception(f"Unknown backup profile {backup.parsed_spec.backupProfileName} in cluster {backup.namespace}/{backup.parsed_spec.clusterName}")

    backup_source = pick_source_instance(cluster, logger)

    if profile.dumpInstance:
        return execute_dump_instance(backup_source, profile.dumpInstance, backupdir, job_name, logger)
    elif profile.cloneSnapshot:
        return execute_clone_snapshot(backup_source, profile.cloneSnapshot, backupdir, job_name, logger)
    else:
        raise Exception(f"Invalid backup method in profile {profile.name}")


def main(argv):
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
    ts = datetime.datetime.fromtimestamp(os.stat(__file__).st_mtime).isoformat()
    logger.info(f"backup  version={config.OPERATOR_VERSION}  timestamp={ts}")

    import subprocess
    subprocess.run(["ls", "-la", "/"])
    subprocess.run(["ls", "-l", "/.oci"])

    ns = argv[1]
    name = argv[2]
    jobname = argv[3]
    if len(argv) > 4:
        backupdir = argv[4]
    else:
        backupdir = None
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
            import time
            time.sleep(60*60)

        return 1

    return 0

