# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import List
from logging import Logger
import yaml
import kopf
from copy import deepcopy
from .backup_api import BackupProfile, BackupSchedule, MySQLBackupSpec
from .. import utils, config, consts
from .. innodbcluster.cluster_api import InnoDBClusterSpec
from .. kubeutils import api_cron_job, k8s_cluster_domain

def prepare_backup_secrets(spec: InnoDBClusterSpec) -> dict:
    """
    Secrets for authenticating backup tool with MySQL.
    """
    backup_user = utils.b64encode(config.BACKUP_USER_NAME)
    backup_pwd = utils.b64encode(utils.generate_password())

    # We use a separate secrets object for the backup, so that we don't need to
    # give access for the main secret to backup instances.
    # No need to namespace it. A namespaced secret will be created by the caller
    tmpl = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {spec.name}-backup
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
    app.kubernetes.io/name: mysql-innodbcluster
    app.kubernetes.io/instance: idc-{spec.name}
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
data:
  backupUsername: {backup_user}
  backupPassword: {backup_pwd}
"""
    return yaml.safe_load(tmpl)


def prepare_backup_job(jobname: str, spec: MySQLBackupSpec) -> dict:
    cluster_domain = k8s_cluster_domain(None)

    # No need to namespace it. A namespaced job will be created by the caller
    tmpl = f"""
apiVersion: batch/v1
kind: Job
metadata:
  name: {jobname}
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
    app.kubernetes.io/name: mysql-innodbcluster-backup-task
    app.kubernetes.io/instance: idc-{spec.name}
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
spec:
  template:
    spec:
      serviceAccountName: {spec.serviceAccountName}
      securityContext:
        runAsUser: 27
        runAsGroup: 27
        fsGroup: 27
      containers:
      - name: operator-backup-job
        image: {spec.operator_image}
        imagePullPolicy: {spec.operator_image_pull_policy}
        command: ["mysqlsh", "--pym", "mysqloperator", "backup",
                  "--command", "execute-backup",
                  "--namespace", "{spec.namespace}",
                  "--backup-object-name", "{spec.name}",
                  "--job-name", "{jobname}",
                  "--backup-dir", "/mnt/storage"
        ]
        securityContext:
          # These can't go to spec.template.spec.securityContext
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
          allowPrivilegeEscalation: false
          privileged: false
          readOnlyRootFilesystem: true
          capabilities:
            drop:
            - ALL
        env:
        - name: MYSQLSH_USER_CONFIG_HOME
          value: /mysqlsh
        - name: MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN
          value: {cluster_domain}
        volumeMounts:
        - name: shellhome
          mountPath: /mysqlsh
      volumes:
      - name: shellhome
        emptyDir: {{}}
      restartPolicy: Never
      terminationGracePeriodSeconds: 60
"""
    job = yaml.safe_load(tmpl)

    metadata = {}
    if spec.backupProfile.podAnnotations:
        metadata['annotations'] = spec.backupProfile.podAnnotations
    if spec.backupProfile.podLabels:
        metadata['labels'] = spec.backupProfile.podLabels

    if len(metadata):
        utils.merge_patch_object(job["spec"]["template"], {"metadata" : metadata })

    spec.add_to_pod_spec(job["spec"]["template"], "operator-backup-job")

    return job


def prepare_mysql_backup_object_by_profile_name(name: str, cluster_name: str, backup_profile_name: str) -> dict:
    # No need to namespace it. A namespaced job will be created by the caller
    tmpl = f"""
apiVersion: {consts.GROUP}/{consts.VERSION}
kind: {consts.MYSQLBACKUP_KIND}
metadata:
  name: {name}
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {cluster_name}
    app.kubernetes.io/name: mysql-innodbcluster-backup-task
    app.kubernetes.io/instance: idc-{cluster_name}
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
spec:
  clusterName: {cluster_name}
  backupProfileName: {backup_profile_name}
  addTimestampToBackupDirectory: false
"""
    return yaml.safe_load(tmpl.replace("\n\n", "\n"))


def prepare_mysql_backup_object_by_profile_object(name: str, cluster_name: str, backup_profile: dict) -> dict:

    pod_labels = backup_profile.get("podLabels")
    if pod_labels:
      labels = f"""
podLabels: {pod_labels}
"""

    pod_annotations = backup_profile.get("podAnnotations")
    if pod_annotations:
      annotations = f"""
podAnnotations: {pod_annotations}
"""

    # No need to namespace it. A namespaced job will be created by the caller

    tmpl = f"""
apiVersion: {consts.GROUP}/{consts.VERSION}
kind: {consts.MYSQLBACKUP_KIND}
metadata:
  name: {name}
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {cluster_name}
    app.kubernetes.io/name: mysql-innodbcluster-backup-task
    app.kubernetes.io/instance: idc-{cluster_name}
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
spec:
  clusterName: {cluster_name}
  backupProfile:
    name: {name}
{utils.indent(labels, 4) if pod_labels else ""}
{utils.indent(annotations, 4) if pod_annotations else ""}
  addTimestampToBackupDirectory: false
"""

    backup_object = yaml.safe_load(tmpl.replace("\n\n", "\n"))

    utils.merge_patch_object(backup_object['spec'],
                             {'backupProfile' : backup_profile}, "spec.backupProfile")

    return backup_object


def backup_job_name(cluster_name, schedule_name: str) -> str:
    return f"{cluster_name}-{schedule_name}{utils.timestamp(dash = False, four_digit_year = False)}"


def schedule_cron_job_name(cluster_name, schedule_name : str) -> str:
    # cb = create backup
    return f"{cluster_name}-{schedule_name}-cb"


def schedule_cron_job_job(namespace, cluster_name, schedule_name : str):
    name = schedule_cron_job_name(cluster_name, schedule_name)
    return api_cron_job.read_namespaced_cron_job(name, namespace)


def patch_cron_template_for_backup_schedule(base: dict, cluster_name: str, schedule_profile: BackupSchedule) -> dict:
    new_object = deepcopy(base)
    new_object["metadata"]["name"] = schedule_cron_job_name(cluster_name, schedule_profile.name)
    new_object["spec"]["suspend"] = not schedule_profile.enabled
    new_object["spec"]["schedule"] = schedule_profile.schedule
    new_object["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["command"].extend(["--schedule-name", schedule_profile.name])

    if schedule_profile.timeZone:
        new_object["spec"]["timeZone"] = schedule_profile.timeZone

    metadata = {}
    if schedule_profile.backupProfile.podAnnotations:
        metadata['annotations'] = schedule_profile.backupProfile.podAnnotations
    if schedule_profile.backupProfile.podLabels:
        metadata['labels'] = schedule_profile.backupProfile.podLabels

    if len(metadata):
        utils.merge_patch_object(new_object["spec"]["jobTemplate"]["spec"]["template"], {"metadata" : metadata })

    return new_object


def get_cron_job_template(spec: InnoDBClusterSpec) -> dict:
    tmpl = f"""
apiVersion: batch/v1
kind: CronJob
metadata:
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
    app.kubernetes.io/name: mysql-innodbcluster
    app.kubernetes.io/instance: idc-{spec.name}
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
spec:
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      backoffLimit: 0
      template:
        spec:
          securityContext:
            runAsUser: 27
            runAsGroup: 27
            fsGroup: 27
          containers:
          - name: operator-backup-job-cron
            image: {spec.operator_image}
            imagePullPolicy: {spec.operator_image_pull_policy}
            command: ["mysqlsh", "--pym", "mysqloperator", "backup",
                      "--command", "create-backup-object",
                      "--namespace", "{spec.namespace}",
                      "--cluster-name", "{spec.name}"
            ]
            securityContext:
              # These can't go to spec.template.spec.securityContext
              # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
              # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
              # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
              # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
              allowPrivilegeEscalation: false
              privileged: false
              readOnlyRootFilesystem: true
              capabilities:
                drop:
                - ALL
            env:
            - name: MYSQLSH_USER_CONFIG_HOME
              value: /mysqlsh
            volumeMounts:
            - name: shellhome
              mountPath: /mysqlsh
          volumes:
          - name: shellhome
            emptyDir: {{}}
          restartPolicy: Never
          terminationGracePeriodSeconds: 60
          serviceAccountName: {spec.serviceAccountName}
"""
    base = yaml.safe_load(tmpl.replace("\n\n", "\n"))

    return base


def compare_schedules(spec: InnoDBClusterSpec, old: dict, new: dict, logger: Logger) -> dict:
    logger.info(f"backup_objects.compare_schedules {spec.namespace}/{spec.name}")
    old_schedules = {}
    if not old is None:
        for old_object in old:
            schedule = BackupSchedule(spec)
            # Don't try to load the profile as it might not exist anymore:
            # If the profile name is changed together with the schedule that references it,
            # then we can't load with the old name from k8s API, as won't exist. All what exists
            # is in new.
            schedule.parse(old_object, "", load_profile=False)
            old_schedules[schedule.name] = schedule

    if old == new:
        return {
              'removed': {},
              'added': {},
              'modified': {},
              'unmodified': old_schedules
            }

    new_schedules = {}
    if not new is None:
        for new_object in new:
            schedule = BackupSchedule(spec)
            schedule.parse(new_object, "")
            new_schedules[schedule.name] = schedule

    removed = {}
    added = {}
    modified = {}
    unmodified = {}

    # Check for modified, non-modified and removed objects
    for old_schedule_name, old_schedule_obj in old_schedules.items():
        if old_schedule_name in new_schedules:
            new_schedule_obj = new_schedules[old_schedule_name]
            if old_schedule_obj == new_schedule_obj:
                unmodified[old_schedule_name] = old_schedule_obj
            else:
                modified[old_schedule_name] = { 'old' : old_schedule_obj, 'new' : new_schedule_obj}
        else:
            removed[old_schedule_name] = old_schedule_obj

    # Now it's time to check if something was added
    for new_schedule_name, new_schedule_obj in new_schedules.items():
        if not (new_schedule_name in old_schedules):
            added[new_schedule_name] = new_schedule_obj


    return {
              'removed': removed,
              'added': added,
              'modified': modified,
              'unmodified': unmodified
            }


def update_schedules(spec: InnoDBClusterSpec, old: dict, new: dict, logger: Logger) -> int:
    logger.info("backup_objects.updates_schedules")
    namespace = spec.namespace
    cluster_name = spec.name

    diff = compare_schedules(spec, old, new, logger)
    logger.info(f"backup_objects.update_schedules: diff={diff}")

    if (len(diff['removed']) == 0 and \
        len(diff['added']) == 0 and \
        len(diff['modified']) == 0):
        logger.info("No backup schedules changes")
        return 0

    if len(diff['removed']):
        logger.info(f"backup_objects.update_schedules: will delete {len(diff['removed'])} backup schedule objects")
        for rm_schedule_name in diff['removed']:
            cj_name = schedule_cron_job_name(cluster_name, rm_schedule_name)
            logger.info(f"backup_objects.update_schedules: deleting schedule {cj_name} in {namespace} ")
            api_cron_job.delete_namespaced_cron_job(cj_name, namespace)

    if len(diff['added']):
        logger.info(f"backup_objects.update_schedules: will add {len(diff['added'])} backup schedule objects")
        cj_template = get_cron_job_template(spec)
        for add_schedule_name, add_schedule_obj in diff['added'].items():
            cj_name = schedule_cron_job_name(cluster_name, add_schedule_name)
            logger.info(f"backup_objects.update_schedules: adding schedule {cj_name} in {namespace}")
            cronjob = patch_cron_template_for_backup_schedule(cj_template, spec.name, add_schedule_obj)
            kopf.adopt(cronjob)
            api_cron_job.create_namespaced_cron_job(namespace=namespace, body=cronjob)

    if len(diff['modified']):
        logger.info(f"backup_objects.update_schedules: will modify {len(diff['modified'])} backup schedule objects")
        cj_template = get_cron_job_template(spec)
        for mod_schedule_name, mod_schedule_objects in diff['modified'].items():
            cj_name = schedule_cron_job_name(cluster_name, mod_schedule_name)
            logger.info(f"backup_objects.update_schedules: modifying schedule {cj_name} in {namespace}")
            cronjob = patch_cron_template_for_backup_schedule(cj_template, spec.name, mod_schedule_objects["new"])
            logger.info(f"backup_objects.update_schedules: {cronjob}")
            api_cron_job.replace_namespaced_cron_job(name=cj_name, namespace=namespace, body=cronjob)

def ensure_schedules_use_current_image(spec: InnoDBClusterSpec, logger: Logger) -> None:
    for schedule in spec.backupSchedules:
        logger.info(f"Checking operator version for backup schedule {spec.namespace}/{spec.name}/{schedule.name}")
        cj = schedule_cron_job_job(spec.namespace, spec.name, schedule.name)
        old_image = cj.spec.job_template.spec.template.spec.containers[0].image
        if old_image != spec.operator_image:
            cj.spec.job_template.spec.template.spec.containers[0].image = spec.operator_image
            cj_name = schedule_cron_job_name(spec.name, schedule.name)
            api_cron_job.replace_namespaced_cron_job(name=cj_name, namespace=spec.namespace, body=cj)
