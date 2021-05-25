# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from ..innodbcluster.cluster_api import InnoDBClusterSpec
from .backup_api import MySQLBackupSpec
import yaml
from .. import utils, config, consts


def prepare_backup_secrets(spec: InnoDBClusterSpec) -> dict:
    """
    Secrets for authenticating backup tool with MySQL.
    """
    backup_user = utils.b64encode(config.BACKUP_USER_NAME)
    backup_pwd = utils.b64encode(utils.generate_password())

    # We use a separate secrets object for the backup, so that we don't need to
    # give access for the main secret to backup instances.
    tmpl = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {spec.name}-backup
data:
  backupUsername: {backup_user}
  backupPassword: {backup_pwd}
"""
    return yaml.safe_load(tmpl)


def prepare_backup_job(jobname: str, spec: MySQLBackupSpec) -> dict:
    tmpl = f"""
apiVersion: batch/v1
kind: Job
metadata:
  name: {jobname}
  namespace: {spec.namespace}
spec:
  template:
    spec:
      containers:
      - name: shell
        image: {spec.shell_image}
        imagePullPolicy: {spec.shell_image_pull_policy}
        command: ["mysqlsh", "--pym", "mysqloperator", "backup", "{spec.namespace}", "{spec.name}", "{jobname}", "/mnt/storage"]
{utils.indent(spec.image_pull_secrets, 6)}
{utils.indent(spec.service_account_name, 6)}
      restartPolicy: Never
"""

    job = yaml.safe_load(tmpl)

    spec.add_to_pod_spec(job["spec"]["template"], "shell")

    return job
