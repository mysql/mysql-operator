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
spec:
  template:
    spec:
      containers:
      - name: shell
        image: {config.MYSQL_SHELL_IMAGE}:{config.DEFAULT_SHELL_VERSION_TAG}
        imagePullPolicy: IfNotPresent
        command: ["mysqlsh", "-f", "/usr/lib/mysqlsh/kubernetes/backup.py", "{spec.namespace}", "{spec.name}", "{jobname}", "/mnt/storage"]
      restartPolicy: Never
"""

    job = yaml.safe_load(tmpl)

    spec.add_to_pod_spec(job["spec"]["template"], "shell")

    return job
