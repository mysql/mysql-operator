# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


from .. import consts, kubeutils, config, utils
from ..kubeutils import api_core, api_batch
from ..innodbcluster.cluster_api import InnoDBCluster
from .backup_api import MySQLBackup
from . import backup_objects
import kopf
from logging import Logger


@kopf.on.create(consts.GROUP, consts.VERSION,
                consts.MYSQLBACKUP_PLURAL)  # type: ignore
def on_mysqlbackup_create(name: str, namespace: str, spec: dict, body: dict, logger: Logger, **kwargs):
    logger.info(
        f"Initializing MySQL Backup job name={name} namespace={namespace}")

    backup = MySQLBackup(body)

    jobname = name+"-"+utils.timestamp()

    job = backup_objects.prepare_backup_job(jobname, backup.parsed_spec)

    kopf.adopt(job)

    api_batch.create_namespaced_job(namespace, body=job)

    # Copy the backup profile contents to the backup object
    # TODO


# TODO create a job to delete the data when the job is deleted
