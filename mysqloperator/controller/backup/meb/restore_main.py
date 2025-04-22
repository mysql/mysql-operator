# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import argparse
import json
import logging
import os
import re
import shutil
import sys
import traceback

from . import meb_controller as meb

def _get_storage(mebinfo: dict):
    if 'ociObjectStorage' in mebinfo['spec']['storage']:
        return meb.MebStorageOCIRestore(mebinfo['credentials']['parBaseUrl'])
    elif 's3' in mebinfo['spec']['storage']:
        s3 = mebinfo['spec']['storage']['s3']
        secret = mebinfo['secret']
        return meb.MebStorageS3(s3['objectKeyPrefix'], s3['bucket'],
                                secret['key_id'], secret['secret_access_key'])

    print("No storage config for restore found, aborting")
    sys.exit(1)


def do_restore(datadir: str, cluster_name: str, mebinfo: dict,
               logger: logging.Logger):
    storage = _get_storage(mebinfo)
    mebspec = mebinfo['spec']

    options = []
    if "extraOptions" in mebspec:
        options = mebspec["extraOptions"]

    m = meb.MySQLEnterpriseBackup(
        storage,
        options,
        "/tmp/backup-tmp"
    )

    incrementals = []
    if 'incrementalBackups' in mebspec:
        incrementals = mebspec['incrementalBackups']

    m.restore(mebspec['fullBackup'], incrementals)
    m.cleanup()

    logger.info("Restored full backup %s", mebspec['fullBackup'])
    if 'incrementalBackups' in mebspec:
        logger.info("Restored incremental backups %s", incrementals)


def prepare_binlogs_as_relay_logs_for_pitr(datadir: str,
                                           cluster_name: str,
                                           mebinfo: dict,
                                           logger: logging.Logger):
    storage = _get_storage(mebinfo)
    mebspec = mebinfo['spec']

    options = []
    if "extraOptions" in mebspec:
        options = mebspec["extraOptions"]

    if not "pitr" in mebspec or not "backupFile" in mebspec["pitr"]:
        return

    logger.info("Preparing relay logs for PITR")

    tmpdir = "/tmp/backup-tmp"

    m = meb.MySQLEnterpriseBackup(
        storage,
        options,
        tmpdir
    )

    try:
        m.extract(mebspec['pitr']['backupFile'])

        binlog_base = mebspec["pitr"]["binlogName"] if len(mebspec["pitr"]["binlogName"]) else "binlog"

        pattern = re.compile(rf'^{re.escape(binlog_base)}\.\d{{6}}$')
        binlogs = [l for l in os.listdir(f"{tmpdir}/datadir") if pattern.match(l)]
        binlogs.sort()

        logger.info("Trying to prepare binlogs from backup as relay logs")

        i = 0
        with open(f"{datadir}/{cluster_name}-0-relay-bin-pitr.index", "wt") as relay_index:
            for logfile in binlogs:
                i += 1
                logger.info(f"Preparing for PITR: COPY {tmpdir}/datadir/{logfile.strip()} TO {datadir}/{cluster_name}-0-relay-bin.{i:06}")
                shutil.copy(f"{tmpdir}/datadir/{logfile.strip()}", f"{datadir}/{cluster_name}-0-relay-bin-pitr.{i:06}")
                relay_index.write(f"./{cluster_name}-0-relay-bin-pitr.{i:06}\n")
    finally:
        m.cleanup()

def main(argv):
    try:
        with open("/tmp/meb_restore.json") as restore_file:
            meb_restore_info = json.load(restore_file)
    except FileNotFoundError:
        print("No restore needed, skipping (check initconf for reason)")
        return 0

    parser = argparse.ArgumentParser(description = "MySQL InnoDB Cluster Instance Restore Container")
    parser.add_argument('--logging-level', type = int, nargs="?", default = logging.INFO, help = "Logging Level")
    parser.add_argument('--pod-name', type = str, nargs=1, default=None, help = "Pod Name")
    parser.add_argument('--pod-namespace', type = str, nargs=1, default=None, help = "Pod Namespace")
    parser.add_argument('--cluster-name', type = str, nargs=1, default=None, help = "InniDBCluster Name")
    parser.add_argument('--datadir', type = str, default = "/var/lib/mysql", help = "Path do data directory")
    args = parser.parse_args(argv[1:])

    datadir = args.datadir

    logging.basicConfig(level=args.logging_level,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")
    logger = logging.getLogger("restore")

    name = args.pod_name[0] # nargs returns a list
    namespace = args.pod_namespace[0] # nargs returns a list
    cluster_name = args.cluster_name[0]

    try:
        logger.info(f"Restoreing MySQL Enterprise Backup in Pod {namespace}/{name}, datadir={datadir}")
        do_restore(datadir, cluster_name, meb_restore_info, logger)
        prepare_binlogs_as_relay_logs_for_pitr(datadir, cluster_name, meb_restore_info, logger)
    except Exception as exc:
        traceback.print_exc()
        logger.critical(f"Unhandled exception while restoring: {exc}")
        sys.exit(1)

if __name__ == '__main__':
    main(sys.argv)
