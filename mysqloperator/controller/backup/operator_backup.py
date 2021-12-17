# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from kubernetes.client.rest import ApiException
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
    logger.info(f"Initializing MySQL Backup job name={name} namespace={namespace}")

    backup = MySQLBackup(body)

    jobname = name

    if backup.parsed_spec.addTimestampToBackupDirectory:
        jobname = jobname + "-" + utils.timestamp()

    job = backup_objects.prepare_backup_job(jobname, backup.parsed_spec)

    kopf.adopt(job)

    try:
        api_batch.create_namespaced_job(namespace, body=job)
    except ApiException as exc:
        print(f"Exception {exc} when calling create_namespaced_job({consts.GROUP}, {consts.VERSION}, {namespace}, {consts.MYSQLBACKUP_PLURAL} body={body}")
        raise kopf.PermanentError(f"Exception {exc} when calling create_namespaced_job({consts.GROUP}, {consts.VERSION}, {namespace}, {consts.MYSQLBACKUP_PLURAL} body={body}")

    return 0

# TODO create a job to delete the data when the job is deleted
