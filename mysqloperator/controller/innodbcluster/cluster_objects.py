# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from logging import Logger, getLogger
import kopf
from typing import List, Dict, Optional
from ..kubeutils import client as api_client
from .. import utils, config, consts
from .cluster_api import InnoDBCluster, AbstractServerSetSpec, InnoDBClusterSpec, ReadReplicaSpec, InnoDBClusterSpecProperties
from .. import fqdn
import yaml
from ..kubeutils import api_core, api_apps, api_customobj, k8s_cluster_domain, ApiException
from . import router_objects
import base64
import os

# TODO replace app field with component (mysqld,router) and tier (mysql)

# This service includes all instances, even those that are not ready


def prepare_cluster_service(spec: AbstractServerSetSpec, logger: Logger) -> dict:
    extra_label = ""
    if type(spec) is InnoDBClusterSpec:
        instance_type = "group-member"
        instances = spec.instances
    elif type(spec) is ReadReplicaSpec:
        instance_type = "read-replica"
        extra_label = f"mysql.oracle.com/read-replica: {spec.name}"
    else:
        raise NotImplementedError(f"Unknown subtype {type(spec)} for creating StatefulSet")
    tmpl = f"""
apiVersion: v1
kind: Service
metadata:
  name: {spec.name}-instances
  namespace: {spec.namespace}
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {spec.cluster_name}
    mysql.oracle.com/instance-type: {instance_type}
    {extra_label}
spec:
  clusterIP: None
  publishNotReadyAddresses: true
  ports:
  - name: mysql
    port: {spec.mysql_port}
    targetPort: {spec.mysql_port}
  - name: mysqlx
    port: {spec.mysql_xport}
    targetPort: {spec.mysql_xport}
  - name: gr-xcom
    port: {spec.mysql_grport}
    targetPort: {spec.mysql_grport}
  selector:
    component: mysqld
    tier: mysql
    mysql.oracle.com/cluster: {spec.cluster_name}
    mysql.oracle.com/instance-type: {instance_type}
    {extra_label}
  type: ClusterIP
"""

    svc = yaml.safe_load(tmpl)
    for subsystem in spec.get_add_to_svc_cbs:
        print(f"\t\tChecking subsystem {subsystem}")
        for add_to_svc_cb in spec.get_add_to_svc_cbs[subsystem]:
            print(f"\t\tAdding {subsystem} SVC bits")
            add_to_svc_cb(svc, logger)

    return svc


def prepare_secrets(spec: InnoDBClusterSpec) -> dict:
    def encode(s):
        return base64.b64encode(bytes(s, "ascii")).decode("ascii")

    admin_user = encode(config.CLUSTER_ADMIN_USER_NAME)
    admin_pwd = encode(utils.generate_password())

    tmpl = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {spec.name}-privsecrets
data:
  clusterAdminUsername: {admin_user}
  clusterAdminPassword: {admin_pwd}
"""
    return yaml.safe_load(tmpl)


def prepare_cluster_pod_disruption_budget(spec: InnoDBClusterSpec) -> dict:
    tmpl = f"""
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: {spec.name}-pdb
spec:
  maxUnavailable: 1
  selector:
    matchLabels:
      component: mysqld
      tier: mysql
      mysql.oracle.com/cluster: {spec.name}
"""
    pdb = yaml.safe_load(tmpl.replace("\n\n", "\n"))

    return pdb


# TODO - check if we need to add a finalizer to the sts and svc (and if so, what's the condition to remove them)
# TODO - check if we need to make readinessProbe take into account innodb recovery times

# TODO - create ServiceAccount ({cluster.name}-sidecar-sa) for the mysql pods and bind it to the mysql-sidecar role

# ## About lifecycle probes:
#
# ### startupProbe
#
# used to let k8s know that the container is still starting up.
#
# * Server startup can take anywhere from a few seconds to several minutes.
# * If the server is initializing for the first time, it will take a few seconds.
# * If the server is restarting after a clean shut down and there's not much data,
#   it will take even less to startup.
# * But if it's restarting after a crash and there's a lot of data, the InnoDB
#   recovery can take a very long time to finish.
# Since we want success to be reported asap, we set the interval to a small value.
# We also set the successThreshold to > 1, so that we can report success once
# every now and then to reset the failure counter.
# NOTE: Currently, the startup probe will never fail the startup. We assume that
# mysqld will abort if the startup fails. Once a method to check whether the
# server is actually frozen during startup, the probe should be updated to stop
# resetting the failure counter and let it actually fail.
#
# ### readinessProbe
#
# used to let k8s know that the container can be marked as ready, which means
# it can accept external connections. We need mysqld to be always accessible,
# so the probe should always succeed as soon as startup succeeds.
# Any failures that happen after it's up don't matter for the probe, because
# we want GR and the operator to control the fate of the container, not the
# probe.
#
# ### livenessProbe
#
# this checks that the server is still healthy. If it fails above the threshold
# (e.g. because of a deadlock), the container is restarted.
#
def prepare_cluster_stateful_set(spec: AbstractServerSetSpec, logger: Logger) -> dict:
    init_mysql_argv = ["mysqld", "--user=mysql"]
#    if config.enable_mysqld_general_log:
#        init_mysql_argv.append("--general-log=1")

    mysql_argv = init_mysql_argv

    # we only need this in initconf, we pass it to all operator images to be
    # on the safe side
    cluster_domain = k8s_cluster_domain(logger)

    fqdn_template = fqdn.idc_service_fqdn_template(spec)

    extra_label = ""
    if type(spec) is InnoDBClusterSpec:
        instance_type = "group-member"
    elif type(spec) is ReadReplicaSpec:
        instance_type = "read-replica"
        extra_label = f"mysql.oracle.com/read-replica: {spec.name}"
        # initial startup no replica, we scale up once the group is running
        # spec.instances therefore will be reduced by the caller!
    else:
        raise NotImplementedError(f"Unknown subtype {type(spec)} for creating StatefulSet")


    # TODO re-add "--log-file=",
    tmpl = f"""
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: {spec.name}
  annotations:
      mysql.oracle.com/fqdn-template: '{fqdn_template}'
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {spec.cluster_name}
    mysql.oracle.com/instance-type: {instance_type}
    {extra_label}
    app.kubernetes.io/name: mysql-innodbcluster
    app.kubernetes.io/instance: mysql-innodbcluster-{spec.name}
    app.kubernetes.io/component: database
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
spec:
  serviceName: {spec.name}-instances
  replicas: {spec.instances}
  podManagementPolicy: Parallel
  selector:
    matchLabels:
      component: mysqld
      tier: mysql
      mysql.oracle.com/cluster: {spec.cluster_name}
      mysql.oracle.com/instance-type: {instance_type}
      {extra_label}
      app.kubernetes.io/name: mysql-innodbcluster-mysql-server
      app.kubernetes.io/instance: mysql-innodbcluster-{spec.name}-mysql-server
      app.kubernetes.io/component: database
      app.kubernetes.io/managed-by: mysql-operator
      app.kubernetes.io/created-by: mysql-operator
  template:
    metadata:
      annotations:
        mysql.oracle.com/fqdn-template: '{fqdn_template}'
      labels:
        component: mysqld
        tier: mysql
        mysql.oracle.com/cluster: {spec.cluster_name}
        mysql.oracle.com/instance-type: {instance_type}
        {extra_label}
        app.kubernetes.io/name: mysql-innodbcluster-mysql-server
        app.kubernetes.io/instance: mysql-innodbcluster-{spec.name}-mysql-server
        app.kubernetes.io/component: database
        app.kubernetes.io/managed-by: mysql-operator
        app.kubernetes.io/created-by: mysql-operator
    spec:
      subdomain: {spec.name}
      readinessGates:
      - conditionType: "mysql.oracle.com/configured"
      - conditionType: "mysql.oracle.com/ready"
      serviceAccountName: {spec.serviceAccountName}
      securityContext:
        runAsUser: 27
        runAsGroup: 27
        fsGroup: 27
        runAsNonRoot: true
      terminationGracePeriodSeconds: 120
      initContainers:
      - name: fixdatadir
        image: {spec.operator_image}
        imagePullPolicy: {spec.sidecar_image_pull_policy}
        command: ["bash", "-c", "chown 27:27 /var/lib/mysql && chmod 0700 /var/lib/mysql"]
        securityContext:
          # make an exception for this one
          runAsNonRoot: false
          runAsUser: 0
          # These can't go to spec.template.spec.securityContext
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
          allowPrivilegeEscalation: false
          privileged: false
          readOnlyRootFilesystem: true
          capabilities:
            add:
            - CHOWN
            - FOWNER
            drop:
            - ALL
        volumeMounts:
        - name: datadir
          mountPath: /var/lib/mysql
        env:
        - name: MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN
          value: {cluster_domain}
        - name: MYSQLSH_CREDENTIAL_STORE_SAVE_PASSWORDS
          value: never
      - name: initconf
        image: {spec.operator_image}
        imagePullPolicy: {spec.sidecar_image_pull_policy}
        # For datadir see the datadir volum mount
        command: ["mysqlsh", "--log-level=@INFO", "--pym", "mysqloperator", "init",
                  "--pod-name", "$(POD_NAME)",
                  "--pod-namespace", "$(POD_NAMESPACE)",
                  "--datadir", "/var/lib/mysql"
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
          # The value is is inherited from the PodSecurityContext but dumb sec checkers might not know that
          runAsNonRoot: true
          capabilities:
            drop:
            - ALL
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN
          value: {cluster_domain}
        - name: MYSQLSH_USER_CONFIG_HOME
          value: /tmp
        - name: MYSQLSH_CREDENTIAL_STORE_SAVE_PASSWORDS
          value: never
        volumeMounts:
        - name: initconfdir
          mountPath: /mnt/initconf
          readOnly: true
        - name: datadir
          mountPath: /var/lib/mysql
        - name: mycnfdata
          mountPath: /mnt/mycnfdata
        - name: initconf-tmp
          mountPath: /tmp
        - name: rootcreds
          readOnly: true
          # rootHost is not obligatory and thus might not exist in the secret
          # Nevertheless K8s won't complain and instead of mounting an empty file
          # will create a directory (/rootcreds/rootHost will be an empty directory)
          # For more information see below the comment regarding rootcreds.
          subPath: rootHost
          mountPath: /rootcreds/rootHost
      - name: initmysql
        image: {spec.mysql_image}
        imagePullPolicy: {spec.mysql_image_pull_policy}
        args: {init_mysql_argv}
        securityContext:
          # These can't go to spec.template.spec.securityContext
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
          allowPrivilegeEscalation: false
          privileged: false
          readOnlyRootFilesystem: true
          # The value is is inherited from the PodSecurityContext but dumb sec checkers might not know that
          runAsNonRoot: true
          capabilities:
            drop:
            - ALL
        env:
        - name: MYSQL_INITIALIZE_ONLY
          value: "1"
        - name: MYSQL_RANDOM_ROOT_PASSWORD
          value: "1"
        - name: MYSQLSH_USER_CONFIG_HOME
          value: /tmp
        volumeMounts:
        - name: datadir
          mountPath: /var/lib/mysql
        - name: rundir
          mountPath: /var/run/mysqld
        - name: mycnfdata
          mountPath: /etc/my.cnf.d
          subPath: my.cnf.d
        - name: mycnfdata
          mountPath: /docker-entrypoint-initdb.d
          subPath: docker-entrypoint-initdb.d
        - name: mycnfdata
          mountPath: /etc/my.cnf
          subPath: my.cnf
        - name: initmysql-tmp
          mountPath: /tmp
        - name: varlibmysqlfiles # The entrypoint of the container `touch`-es 2 files there
          mountPath: /var/lib/mysql-files
      containers:
      - name: sidecar
        image: {spec.operator_image}
        imagePullPolicy: {spec.sidecar_image_pull_policy}
        command: ["mysqlsh", "--pym", "mysqloperator", "sidecar",
                  "--pod-name", "$(POD_NAME)",
                  "--pod-namespace", "$(POD_NAMESPACE)",
                  "--datadir", "/var/lib/mysql"
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
          # The value is is inherited from the PodSecurityContext but dumb sec checkers might not know that
          runAsNonRoot: true
          capabilities:
            drop:
            - ALL
        env:
        - name: POD_NAME
          valueFrom:
            fieldRef:
              fieldPath: metadata.name
        - name: POD_NAMESPACE
          valueFrom:
            fieldRef:
              fieldPath: metadata.namespace
        - name: MYSQL_UNIX_PORT
          value: /var/run/mysqld/mysql.sock
        - name: MYSQLSH_USER_CONFIG_HOME
          value: /mysqlsh
        - name: MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN
          value: {cluster_domain}
        - name: MYSQLSH_CREDENTIAL_STORE_SAVE_PASSWORDS
          value: never
        volumeMounts:
        - name: rundir
          mountPath: /var/run/mysqld
        - name: mycnfdata
          mountPath: /etc/my.cnf.d
          subPath: my.cnf.d
        - name: mycnfdata
          mountPath: /etc/my.cnf
          subPath: my.cnf
        - name: shellhome
          mountPath: /mysqlsh
        - name: sidecar-tmp
          mountPath: /tmp
{utils.indent(spec.extra_sidecar_volume_mounts, 8)}
      - name: mysql
        image: {spec.mysql_image}
        imagePullPolicy: {spec.mysql_image_pull_policy}
        args: {mysql_argv}
        securityContext:
          # These can't go to spec.template.spec.securityContext
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
          # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
          allowPrivilegeEscalation: false
          privileged: false
          readOnlyRootFilesystem: true
          # The value is is inherited from the PodSecurityContext but dumb sec checkers might not know that
          runAsNonRoot: true
          capabilities:
            drop:
            - ALL
        lifecycle:
          preStop:
            exec:
              # 60 is the default value for dba.gtidWaitTimeout
              # see https://dev.mysql.com/doc/mysql-shell/8.0/en/mysql-innodb-cluster-working-with-cluster.html
              command: ["sh", "-c", "sleep 60 && mysqladmin -ulocalroot shutdown"]
        startupProbe:
          exec:
            command: ["/livenessprobe.sh", "8"]
          initialDelaySeconds: 5
          periodSeconds: 3
          failureThreshold: 10000
          successThreshold: 1
          timeout: 2
        readinessProbe:
          exec:
            command: ["/readinessprobe.sh"]
          periodSeconds: 5
          initialDelaySeconds: 10
          failureThreshold: 10000
        livenessProbe:
          exec:
            command: ["/livenessprobe.sh"]
          initialDelaySeconds: 15
          periodSeconds: 15
          failureThreshold: 10
          successThreshold: 1
          timeout: 5
        env:
        - name: MYSQL_UNIX_PORT
          value: /var/run/mysqld/mysql.sock
        - name: MYSQLSH_CREDENTIAL_STORE_SAVE_PASSWORDS
          value: never
{utils.indent(spec.extra_env, 8)}
        ports:
        - containerPort: {spec.mysql_port}
          name: mysql
        - containerPort: {spec.mysql_xport}
          name: mysqlx
        - containerPort: {spec.mysql_grport}
          name: gr-xcom
        volumeMounts:
        - name: datadir
          mountPath: /var/lib/mysql
        - name: rundir
          mountPath: /var/run/mysqld
        - name: mycnfdata
          mountPath: /etc/my.cnf.d
          subPath: my.cnf.d
        - name: mycnfdata
          mountPath: /etc/my.cnf
          subPath: my.cnf
        - name: initconfdir
          mountPath: /livenessprobe.sh
          subPath: livenessprobe.sh
        - name: initconfdir
          mountPath: /readinessprobe.sh
          subPath: readinessprobe.sh
        - name: varlibmysqlfiles # The entrypoint of the container `touch`-es 2 files there
          mountPath: /var/lib/mysql-files
        - name: mysql-tmp
          mountPath: /tmp
{utils.indent(spec.extra_volume_mounts, 8)}
      volumes:
      - name: mycnfdata
        emptyDir: {{}}
      - name: rundir
        emptyDir: {{}}
      - name: varlibmysqlfiles
        emptyDir: {{}}
      - name: initconfdir
        configMap:
          name: {spec.name}-initconf
          defaultMode: 0755
      - name: shellhome
        emptyDir: {{}}
      - name: initconf-tmp
        emptyDir: {{}}
      - name: initmysql-tmp
        emptyDir: {{}}
      - name: mysql-tmp
        emptyDir: {{}}
      - name: sidecar-tmp
        emptyDir: {{}}
      # If we declare it and not use it anywhere as backing for a volumeMount K8s won't check
      # if the volume exists. K8s seems to be lazy in that regard. We don't need the information
      # from this secret directly, as the sidecar of pod 0 will fetch the information using the K8s API
      # However, we won't not to be lazy in checking if the secret exists and make it easier for the
      # administrator to find out if the secret is missing. If we mount it in a init or normal container,
      # the pod # will get stuck into "Ready:0/2 Init:0/3" with
      # Warning  FailedMount  XXs (....)  kubelet  "MountVolume.SetUp failed for volume "rootcreds" : secret ".........." not found" error to be seen in describe.
      - name: rootcreds
        secret:
          secretName: {spec.secretName}
          defaultMode: 0400
{utils.indent(spec.extra_volumes, 6)}
  volumeClaimTemplates:
  - metadata:
      name: datadir
    spec:
      accessModes: [ "ReadWriteOnce" ]
      resources:
        requests:
          storage: 2Gi
"""

    statefulset = yaml.safe_load(tmpl.replace("\n\n", "\n"))

    metadata = {}
    if spec.podAnnotations:
        print("\t\tAdding podAnnotations")
        metadata['annotations'] = spec.podAnnotations
    if spec.podLabels:
        print("\t\tAdding podLabels")
        metadata['labels'] = spec.podLabels

    if len(metadata):
        utils.merge_patch_object(statefulset["spec"]["template"], {"metadata" : metadata })

    if spec.keyring:
        print("\t\tAdding keyring STS bit")
        spec.keyring.add_to_sts_spec(statefulset)

    for subsystem in spec.add_to_sts_cbs:
        print(f"\t\tadd_to_sts_cb: Checking subsystem {subsystem}")
        for add_to_sts_cb in spec.add_to_sts_cbs[subsystem]:
            print(f"\t\tAdding {subsystem} STS bits")
            add_to_sts_cb(statefulset, None, logger)

    if spec.podSpec:
        print("\t\tAdding podSpec")
        utils.merge_patch_object(statefulset["spec"]["template"]["spec"],
                                 spec.podSpec, "spec.podSpec")

    if spec.datadirVolumeClaimTemplate:
        print("\t\tAdding datadirVolumeClaimTemplate")
        utils.merge_patch_object(statefulset["spec"]["volumeClaimTemplates"][0]["spec"],
                                 spec.datadirVolumeClaimTemplate, "spec.volumeClaimTemplates[0].spec")
    return statefulset

def update_stateful_set_size(cluster: InnoDBCluster, rr_spec: ReadReplicaSpec, logger: Logger) -> None:
    sts = cluster.get_read_replica_stateful_set(rr_spec.name)
    if sts:
        patch = {"spec": {"replicas": rr_spec.instances}}
        api_apps.patch_namespaced_stateful_set(
            sts.metadata.name, sts.metadata.namespace, body=patch)


def prepare_service_account(spec: AbstractServerSetSpec) -> dict:
    account = f"""
apiVersion: v1
kind: ServiceAccount
metadata:
  name: {spec.serviceAccountName}
  namespace: {spec.namespace}
{spec.image_pull_secrets}
"""

    account = yaml.safe_load(account)

    return account


def prepare_service_account_patch_for_image_pull_secrets(spec: AbstractServerSetSpec) -> Optional[Dict]:
    if not spec.imagePullSecrets:
        return None
    return {
        "imagePullSecrets" : spec.imagePullSecrets
    }


def prepare_role_binding(spec: AbstractServerSetSpec) -> dict:
    rolebinding = f"""
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: {spec.roleBindingName}
  namespace: {spec.namespace}
subjects:
  - kind: ServiceAccount
    name: {spec.serviceAccountName}
roleRef:
  kind: ClusterRole
  name: mysql-sidecar
  apiGroup: rbac.authorization.k8s.io
"""
    rolebinding = yaml.safe_load(rolebinding)

    return rolebinding


def prepare_additional_configmaps(spec: AbstractServerSetSpec, logger: Logger) -> List[Dict]:
    configmaps = []
    prefix = ''
    for subsystem in spec.get_configmaps_cbs:
        for cb in spec.get_configmaps_cbs[subsystem]:
            if cms := cb(prefix, logger):
              for (cm_name, cm) in cms:
                  if cm:
                      configmaps.append(cm)
    return configmaps


def prepare_component_config_configmaps(spec: AbstractServerSetSpec, logger: Logger) -> List[Dict]:
    configmaps = [
        spec.keyring.get_component_config_configmap_manifest()
    ]

    return configmaps


def prepare_component_config_secrets(spec: AbstractServerSetSpec, logger: Logger) -> List[Dict]:
    secrets = []
    cm = spec.keyring.get_component_config_secret_manifest()
    if cm:
        secrets.append(cm)

    return secrets

def prepare_initconf(cluster: InnoDBCluster, spec: AbstractServerSetSpec, logger: Logger) -> dict:

    with open(os.path.dirname(os.path.abspath(__file__))+'/router-entrypoint-run.sh.tpl', 'r') as entryfile:
        router_entrypoint = "".join(entryfile.readlines())

    liveness_probe = """#!/bin/bash
# Copyright (c) 2020, 2021, Oracle and/or its affiliates.

# Insert 1 success every this amount of failures
# (assumes successThreshold is > 1)
max_failures_during_progress=$1

# Ping the server to see if it's up
mysqladmin -umysqlhealthchecker ping
# If it's up, we succeed
if [ $? -eq 0 ]; then
  exit 0
fi

if [ -z $max_failures_during_progress ]; then
  exit 1
fi

# If the init/startup/InnoDB recovery is still ongoing, we're
# not succeeded nor failed yet, so keep failing and getting time
# extensions until it succeeds.
# We currently rely on the server to exit/abort if the init/startup fails,
# but ideally there would be a way to check whether the server is
# still making progress and not just stuck waiting on a frozen networked
# volume, for example.

if [ -f /fail-counter ]; then
  fail_count=$(($(cat /fail-counter) + 1))
else
  fail_count=1
fi

if [ $fail_count -gt $max_failures_during_progress ]; then
  # Report success to reset the failure counter upstream and get
  # a time extension
  rm -f /fail-counter
  exit 0
else
  # Update the failure counter and fail out
  echo $fail_count > /fail-counter
  exit 1
fi
"""

    readiness_probe = """#!/bin/bash
# Copyright (c) 2020, 2022, Oracle and/or its affiliates.

# Once the container is ready, it's always ready.
if [ -f /tmp/mysql-ready ]; then
  exit 0
fi

# Ping server to see if it is ready
if mysqladmin -umysqlhealthchecker ping; then
  touch /tmp/mysql-ready
  exit 0
else
  exit 1
fi
"""

    has_crl = cluster.tls_has_crl()

    if not spec.tlsUseSelfSigned:
        ca_file_name = cluster.get_ca_and_tls().get("CA", "ca.pem")
    else:
        ca_file_name = ""

    tmpl = f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: {spec.name}-initconf
data:
  initdb-localroot.sql: |
    set sql_log_bin=0;
    # Create socket authenticated localroot@localhost account
    CREATE USER localroot@localhost IDENTIFIED WITH auth_socket AS 'mysql';
    GRANT ALL ON *.* TO localroot@localhost WITH GRANT OPTION;
    GRANT PROXY ON ''@'' TO localroot@localhost WITH GRANT OPTION;
    # Drop the default account created by the docker image
    DROP USER IF EXISTS healthchecker@localhost;
    # Create account for liveness probe
    CREATE USER mysqlhealthchecker@localhost IDENTIFIED WITH auth_socket AS 'mysql';
    set sql_log_bin=1;


  readinessprobe.sh: |
{utils.indent(readiness_probe, 4)}


  livenessprobe.sh: |
{utils.indent(liveness_probe, 4)}

  router-entrypoint-run.sh.tpl: |
{utils.indent(router_entrypoint, 4)}


  my.cnf.in: |
    # Server identity related options (not shared across instances).
    # Do not edit.
    [mysqld]
    server_id=@@SERVER_ID@@
    report_host=@@HOSTNAME@@
    datadir=/var/lib/mysql
    loose_mysqlx_socket=/var/run/mysqld/mysqlx.sock
    socket=/var/run/mysqld/mysql.sock
    local-infile=1

    [mysql]
    socket=/var/run/mysqld/mysql.sock

    [mysqladmin]
    socket=/var/run/mysqld/mysql.sock

    !includedir /etc/my.cnf.d


  00-basic.cnf: |
    # Basic configuration.
    # Do not edit.
    [mysqld]
    plugin_load_add=auth_socket.so
    loose_auth_socket=FORCE_PLUS_PERMANENT
    skip_log_error
    log_error_verbosity=3

  01-group_replication.cnf: |
    # GR and replication related options
    # Do not edit.
    [mysqld]
    log_bin={spec.name}
    enforce_gtid_consistency=ON
    gtid_mode=ON
    skip_replica_start=1

  02-ssl.cnf: |
    # SSL configurations
    # Do not edit.
    [mysqld]
    {"# " if spec.tlsUseSelfSigned else ""}ssl-ca=/etc/mysql-ssl/{ca_file_name}
    {"# " if not has_crl else ""}ssl-crl=/etc/mysql-ssl/crl.pem
    {"# " if spec.tlsUseSelfSigned else ""}ssl-cert=/etc/mysql-ssl/tls.crt
    {"# " if spec.tlsUseSelfSigned else ""}ssl-key=/etc/mysql-ssl/tls.key

    loose_group_replication_recovery_use_ssl=1
    {"# " if spec.tlsUseSelfSigned else ""}loose_group_replication_recovery_ssl_verify_server_cert=1

    {"# " if spec.tlsUseSelfSigned else ""}loose_group_replication_recovery_ssl_ca=/etc/mysql-ssl/{ca_file_name}
    #{"# " if not has_crl else ""}loose_group_replication_recovery_ssl_crl=/etc/mysql-ssl/crl.pem
    {"# " if spec.tlsUseSelfSigned else ""}loose_group_replication_recovery_ssl_cert=/etc/mysql-ssl/tls.crt
    {"# " if spec.tlsUseSelfSigned else ""}loose_group_replication_recovery_ssl_key=/etc/mysql-ssl/tls.key

  99-extra.cnf: |
    # Additional user configurations taken from spec.mycnf in InnoDBCluster.
    # Do not edit directly.
{utils.indent(spec.mycnf, 4) if spec.mycnf else ""}
"""

    cm = yaml.safe_load(tmpl)

    prefix = 5
    for subsystem in spec.add_to_initconf_cbs:
        for add_to_initconf_cb in spec.add_to_initconf_cbs[subsystem]:
          add_to_initconf_cb(cm, f"{prefix:02d}-", logger)
          prefix = prefix + 1

    return cm

def prepare_metrics_service_monitors(spec: AbstractServerSetSpec, logger: Logger) -> List[Dict]:
    monitors = []
    prefix = ''
    for subsystem in spec.get_svc_monitor_cbs:
        for cb in spec.get_svc_monitor_cbs[subsystem]:
            (monitor_name, monitor) = cb(logger)
            if monitor:
                monitors.append(monitor)

    return monitors


def update_stateful_set_spec(sts : api_client.V1StatefulSet, patch: dict) -> None:
    api_apps.patch_namespaced_stateful_set(
        sts.metadata.name, sts.metadata.namespace, body=patch)

def update_mysql_image(sts: api_client.V1StatefulSet, cluster: InnoDBCluster,
                       spec: AbstractServerSetSpec,
                       patcher,
                       logger: Logger) -> None:
    """Update MySQL Server image

    This will also update the sidecar container to the current operator version,
    so that a single rolling upgrade covers both and we don't require a restart
    for upgrading sidecar.
    """
    logger.info("update_mysql_image")
    # Operators <= 8.0.32-2.0.8 don't set this environment variable, we have to make sure it is there
    cluster_domain_env = [{
        "name": "MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN",
        "value": k8s_cluster_domain(logger)
    }]

    patch = {"spec": {"template":
                      {"spec": {
                          "containers": [
                               {"name": "sidecar",
                                "image": spec.operator_image,
                                "env": cluster_domain_env
                               },
                               {"name": "mysql",
                                "image": spec.mysql_image,
                                "env": cluster_domain_env
                               },
                          ],
                          "initContainers": [
                              {"name": "fixdatadir",
                               "image": spec.operator_image,
                               "env": cluster_domain_env
                                },
                              {"name": "initconf",
                               "image": spec.operator_image,
                               "env": cluster_domain_env
                              },
                              {"name": "initmysql",
                               "image": spec.mysql_image,
                               "env": cluster_domain_env
                              },
                          ]}
                       }}}

    # TODO [compat8.3.0] remove this when compatibility pre 8.3.0 isn't needed anymore
    keyring_update = spec.keyring.upgrade_to_component(sts, spec, logger)

    if keyring_update:
        logger.info("Need to upgrade keyring from plugin to component")
        (cm, key_sts_patch) = keyring_update
        utils.merge_patch_object(patch["spec"]["template"], key_sts_patch)

        kopf.adopt(cm)
        patcher.create_configmap(spec.namespace, cm["metadata"]["name"], cm, on_apiexception_generic_handler)
        #api_core.create_namespaced_config_map(spec.namespace, cm)

        initconf_patch = [{"op": "remove", "path": "/data/03-keyring-oci.cnf"}]
        #try:
        #    api_core.patch_namespaced_config_map(f"{spec.cluster_name}-initconf",
        #                                            spec.namespace, initconf_patch)
        #except ApiException as exc:
        #    # This might happen during a retry or some other case where it was
        #    # removed already
        #    logger.info(f"Failed to remove keyring config from initconf, ignoring: {exc}")
        patcher.patch_configmap(spec.namespace, f"{spec.cluster_name}-initconf", initconf_patch, on_apiexception_404_handler)

    cm = prepare_initconf(cluster, spec, logger)
    patcher.patch_configmap(spec.namespace, cm['metadata']['name'], cm, on_apiexception_generic_handler)
    #api_core.patch_namespaced_config_map(
    #    cm['metadata']['name'], sts.metadata.namespace, body=cm)

    patcher.patch_sts(patch)
#    update_stateful_set_spec(sts, patch)


def update_operator_image(sts: api_client.V1StatefulSet, spec: InnoDBClusterSpec) -> None:
    patch = {"spec": {"template":
                      {"spec": {
                          "containers": [
                               {"name": "sidecar", "image": spec.operator_image}
                          ],
                          "initContainers": [
                              {"name": "fixdatadir", "image": spec.operator_image},
                              {"name": "initconf", "image": spec.operator_image}
                          ]}
                       }}}
    update_stateful_set_spec(sts, patch)


def update_pull_policy(sts: api_client.V1StatefulSet, spec: InnoDBClusterSpec, logger: Logger) -> dict:
    patch = {"spec": {"template":
                      {"spec": {
                          "initContainers": [
                              {"name": "initconf", "imagePullPolicy": spec.sidecar_image_pull_policy},
                              {"name": "initmysql", "imagePullPolicy": spec.mysql_image_pull_policy}
                          ],
                          "containers": [
                               {"name": "sidecar", "imagePullPolicy": spec.sidecar_image_pull_policy},
                               {"name": "mysql", "imagePullPolicy": spec.mysql_image_pull_policy}
                          ]}
                       }}}
    return patch

def update_template_property(sts: api_client.V1StatefulSet, property_name: str, property_value: str, logger: Logger) -> None:
    patch = {"spec": {"template": {"spec": { property_name: property_value }}}}
    update_stateful_set_spec(sts, patch)


def update_objects_for_subsystem(subsystem: InnoDBClusterSpecProperties,
                                 cluster: InnoDBCluster,
                                 patcher: 'InnoDBClusterObjectModifier',
                                 logger: Logger) -> None:
    logger.info(f"update_objects_for_subsystem: {subsystem}")

    sts = cluster.get_stateful_set()
    svc = cluster.get_service()

    spec = cluster.parsed_spec

    if subsystem in spec.get_configmaps_cbs:
        print(f"\t\tWalking over get_configmaps_cbs len={len(spec.get_configmaps_cbs[subsystem])}")
        #TODO: This won't delete old CMs but only replace old ones, if are still in use, with new content
        #      or create new ones. The solution is to use tuple returning like get_svc_monitor_cbs, where
        #      the cm name will be returned as first tuple element and second will be just None. This will
        #      signal that this CM should be removed, as not in use anymore.
        for get_configmap_cb in spec.get_configmaps_cbs[subsystem]:
            prefix = ''
            new_configmaps = get_configmap_cb(prefix, logger)
            if not new_configmaps:
                continue
            for (cm_name, new_cm) in new_configmaps:
                current_cm = cluster.get_configmap(cm_name)
                if current_cm:
                    if not new_cm:
                        print(f"\t\t\tDeleting CM {cluster.namespace}/{cm_name}")
                        #patcher.delete_configmap(cluster.namespace, cm_name, on_apiexception_404_handler)
                        cluster.delete_configmap(cm_name)
                        continue

                    data_differs = current_cm.data != new_cm["data"]
                    if data_differs:
                        print(f"\t\t\tReplacing CM {cluster.namespace}/{cm_name}")
                        current_cm.data = new_cm["data"]
                        #patcher.replace_configmap(cluster.namespace, cm_name, current_cm, on_apiexception_404_handler)
                        api_core.replace_namespaced_config_map(cm_name, cluster.namespace, body=current_cm)
                else:
                    print(f"\t\t\tNo such cm exists. Creating {cluster.namespace}/{new_cm}")
                    kopf.adopt(new_cm)
                    #patcher.create_configmap(cluster.namespace, new_cm['metadata']['name'], new_cm, on_apiexception_generic_handler)
                    api_core.create_namespaced_config_map(cluster.namespace, new_cm)

    if subsystem in spec.add_to_sts_cbs:
        print(f"\t\tCurrent container count: {len(sts.spec.template.spec.containers)}")
        print(f"\t\tWalking over add_to_sts_cbs len={len(spec.add_to_sts_cbs[subsystem])}")
        changed = False
        sts.spec = spec_to_dict(sts.spec)
        for add_to_sts_cb in spec.add_to_sts_cbs[subsystem]:
            changed = True
            print("\t\t\tPatching STS")
            add_to_sts_cb(sts, patcher, logger)
        if changed:
            new_container_names = [c["name"] for c in patcher.get_sts_path('/spec/template/spec/containers') if c["name"] not in ["mysql", "sidecar"]]
            print(f"\t\t\tNew containers: {new_container_names}")
            new_volumes_names = [c["name"] for c in patcher.get_sts_path('/spec/template/spec/volumes')]
            print(f"\t\t\tNew volumes: {new_volumes_names}")
            new_volume_mounts = [(c["name"], c["volumeMounts"]) for c in patcher.get_sts_path('/spec/template/spec/containers') if c["name"] not in ["mysql", "sidecar"]]
            print(f"\t\t\tNew volume mounts: {new_volume_mounts}")

            # There might be configmap changes, which when mounted will change the server, so we rollover
            # For fine grained approache the get_configmap should return whether there are such changes that require
            # a restart. With a restart, for example, the Cluster1LFSGeneralLogEnableDisableEnable test will hang
            restart_patch = {"spec":{"template":{"metadata":{"annotations":{"kubectl.kubernetes.io/restartedAt":utils.isotime()}}}}}
            patcher.patch_sts(restart_patch)
            #patcher.submit_patches(restart_sts=True)

        print(f"\t\t\tSTS {'patched' if changed else 'unchanged. No rollover upgrade!'}")

    if subsystem in spec.get_add_to_svc_cbs:
        print(f"\t\tWalking over get_add_to_svc_cbs len={len(spec.get_add_to_svc_cbs[subsystem])}")
        changed = False
        for add_to_svc_cb in spec.get_add_to_svc_cbs[subsystem]:
            changed = True
            print("\t\t\tPatching SVC")
            add_to_svc_cb(svc, logger)
        if changed:
            api_core.replace_namespaced_service(svc.metadata.name, svc.metadata.namespace, svc)

        print(f"\t\t\tSVC {'patched' if changed else 'unchanged'}")

    if subsystem in spec.get_svc_monitor_cbs:
      for subsystem in spec.get_svc_monitor_cbs:
          for cb in spec.get_svc_monitor_cbs[subsystem]:
              (monitor_name, monitor) = cb(logger)
              # monitor could be empty, this means - delete old monitor with monitor_name
              print(f"\t\t\tChecking for old ServiceMonitor {monitor_name}")
              if cluster.get_service_monitor(monitor_name):
                  print(f"\t\t\tRemoving old ServiceMonitor {monitor_name}")
                  try:
                      api_customobj.delete_namespaced_custom_object("monitoring.coreos.com", "v1", cluster.namespace,
                                                                    "servicemonitors", monitor_name)
                  except Exception as exc:
                      print(f"\t\t\tPrevious ServiceMonitor {monitor_name} was not removed. Reason: {exc}")
              if monitor:
                  kopf.adopt(monitor)
                  print(f"\t\t\tCreating ServiceMonitor {monitor} ...")
                  try:
                      api_customobj.create_namespaced_custom_object("monitoring.coreos.com", "v1", cluster.namespace,
                                                                    "servicemonitors", monitor)
                  except Exception as exc:
                      # This might be caused by Prometheus Operator missing
                      # we won't fail for that
                      print(f"\t\t\tServiceMonitor {monitor_name} NOT created!")
                      print(exc)
                      cluster.warn(action="CreateCluster", reason="CreateResourceFailed", message=f"{exc}")
              else:
                  print(f"\t\t\tNew ServiceMonitor {monitor_name} will not be created. Monitoring disabled.")


def update_objects_for_logs(cluster: InnoDBCluster, patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
    subsystem = InnoDBClusterSpecProperties.LOGS.value
    update_objects_for_subsystem(subsystem, cluster, patcher, logger)

def update_objects_for_metrics(cluster: InnoDBCluster, patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
    subsystem = InnoDBClusterSpecProperties.METRICS.value
    update_objects_for_subsystem(subsystem, cluster, patcher, logger)


def remove_read_replica(cluster: InnoDBCluster, name: str):
    try:
        api_core.delete_namespaced_config_map(f"{cluster.name}-{name}-initconf", cluster.namespace)
    except Exception as exc:
        print(f"ConfigMap for ReadReplica {name} was not removed. This is usually ok. Reason: {exc}")

    try:
        api_core.delete_namespaced_service(f"{cluster.name}-{name}-instances", cluster.namespace)
    except Exception as exc:
        print(f"Service for ReadReplica {name} was not removed. This is usually ok. Reason: {exc}")

    try:
        api_apps.delete_namespaced_stateful_set(f"{cluster.name}-{name}", cluster.namespace)
    except Exception as exc:
        print(f"StatefulSet for ReadReplica  {name} was not removed. This is usually ok. Reason: {exc}")


def on_first_cluster_pod_created(cluster: InnoDBCluster, logger: Logger) -> None:
    # Add finalizer to the cluster object to prevent it from being deleted
    # until the last pod is properly deleted.
    cluster.add_cluster_finalizer()


def on_last_cluster_pod_removed(cluster: InnoDBCluster, logger: Logger) -> None:
    # Remove cluster finalizer because the last pod was deleted, this lets
    # the cluster object to be deleted too
    logger.info(
        f"Last pod for cluster {cluster.name} was deleted, removing cluster finalizer...")
    cluster.remove_cluster_finalizer()


from enum import Enum
from typing import Callable, cast
from .. import kubeutils

class PatchTarget(Enum):
    STS = "STS"
    DEPLOY = "DEPLOYMENT"
    CM = "CONFIGMAP"

class ApiCommandType(Enum):
    PATCH_STS = "PATCH_STS"
    PATCH_DEPLOY = "PATCH_DEPLOY"
    CREATE_CM = "CREATE_CM"
    DELETE_CM = "DELETE_CM"
    REPLACE_CM = "REPLACE_CM"
    PATCH_CM = "PATCH_CM"

OnApiExceptionHandler = Callable[[ApiException, Logger], None]

def on_apiexception_404_handler(exc: ApiException, logger: Logger):
    if exc.status == 404:
        logger.warning("Object not found! Exception: {exc}")
        return
    raise exc

def on_apiexception_generic_handler(exc: ApiException, logger: Logger):
    logger.warning("ApiException: {exc}")


class ApiCommand:
    def __init__(self,
                 type: ApiCommandType,
                 namespace: str,
                 name: str,
                 body: Optional[dict] = None,
                 on_api_exception: Optional[OnApiExceptionHandler] = None):
        self.type = type
        self.namespace = namespace
        self.name = name
        self.body = body
        self.on_api_exception = on_api_exception

    def run(self, logger: Logger) -> Optional[api_client.V1Status]:
        try:
            if self.type == ApiCommandType.CREATE_CM:
                status = cast(api_client.V1Status,
                              api_core.create_namespaced_config_map(self.namespace, self.body))
            elif self.type == ApiCommandType.DELETE_CM:
                delete_body = api_client.V1DeleteOptions(grace_period_seconds=0)
                status = cast(api_client.V1Status,
                              api_core.delete_namespaced_config_map(self.name, self.namespace, body=delete_body))
                return status
            elif self.type == ApiCommandType.REPLACE_CM:
                status = cast(api_client.V1Status,
                              api_core.replace_namespaced_config_map(self.name, self.namespace, body=self.body))
            elif self.type == ApiCommandType.PATCH_CM:
                status = cast(api_client.V1Status,
                              api_core.patch_namespaced_config_map(self.name, self.namespace, self.body))
        except kubeutils.ApiException as exc:
            if self.on_api_exception is not None:
                self.on_api_exception(exc, logger)
            else:
                raise

        return status


def snail_to_camel(s: str) -> str:
    if s.find("_") == -1:
        return s
    # Special case for '_exec'
    # For some reason for preStop with `exec` when dict-ified we get `pre_stop`` with `_exec`
    #  'lifecycle': {
    #    'post_start': None,
    #     'pre_stop': {'_exec': {'command': ['sh', '-c', 'sleep 60 && mysqladmin -ulocalroot shutdown']},
    # If we don't handle that it becomes `Exec`
    if len(s) and s[0] == "_":
        s = s[1:]

    words = s.split("_")
    ret = words[0] + "".join(word.title() for word in words[1:])
    return ret

def item_snail_to_camel(item):
    if isinstance(item, dict):
        # k8s API will return some fields as None, like
        # spec.containers[1].readinessProbe` : Required value: must specify a handler type
        # spec.containers[1].startupProbe: Required value: must specify a handler type
        # So we strip here the None values. Might hit somewhere where None is legit but for now it works!
        return {snail_to_camel(key):item_snail_to_camel(value) for key, value in item.items() if value is not None}
    if isinstance(item, list):
        return [item_snail_to_camel(value) for value in item]
    return item

def spec_to_dict(spec) -> dict:
    return item_snail_to_camel(spec.to_dict())

def strategic_merge(original, patch):
    if isinstance(original, dict) and isinstance(patch, dict):
        return merge_dicts(original, patch)
    elif isinstance(original, list) and isinstance(patch, list):
        return original + patch
    return patch

def merge_dicts(original, patch):
    for key, value in patch.items():
        if key in original:
            original[key] = strategic_merge(original[key], value)
        else:
            original[key] = value
    return original


class InnoDBClusterObjectModifier:
    def __init__(self, cluster: InnoDBCluster, logger: Logger):
        self.server_sts_patch = {}
        self.sts_changed = False
        self.sts_template_changed = False
        self.deploy_changed = False
        self.router_deploy_patch = {}
        self.cluster = cluster
        self.logger = logger
        self.commands: list[ApiCommand] = []
        self.sts = self.cluster.get_stateful_set()
        self.sts.spec = spec_to_dict(self.sts.spec)
        self.sts_spec_changed = False

    def _apply_server_sts_patch_to_sts_spec_if_needed(self):
        if len(self.server_sts_patch):
            # update with accumulated patches before overwriting
            self.logger.info(f"Applying accumulated patches {self.server_sts_patch['spec']} to sts.spec")
            utils.merge_patch_object(self.sts.spec, self.server_sts_patch["spec"], none_deletes=True)
            self.sts_spec_changed = True
            self.server_sts_patch = {}

    def _get_or_patch_sts_path(self, path: str, patch: Optional[dict] = None):
        self.logger.info(f"get_sts_path: patch_path={path}\n")
        # patches could be cached in self.server_sts_patch, so apply them, if any, before returning parts of self.sts.spec
        self._apply_server_sts_patch_to_sts_spec_if_needed()
        base = self.sts.spec
        # first is leading backslash, then is 'spec', so we skip
        path_elements = path.split("/")[2:]
        if len(path) > 1:
            for path_element in path_elements[0:-1]:
                #self.logger.info(f"{path_element} in base = {path_element in base}\n")
                assert path_element in base
                base = base[path_element]
            if patch is not None:
                base[path_elements[-1]] = patch
                self.logger.info(f"get_sts_path: after patching self.sts.spec={self.sts.spec}")
        return base[path_elements[-1]]

    def get_sts_path(self, path: str):
        return self._get_or_patch_sts_path(path, None)

    def patch_sts(self, patch: dict) -> None:
        self.sts_changed = True
        if "template" in patch:
            self.sts_template_changed = True
        self.logger.info(f"Accumulating patch={patch}\n")
        # cache the patches without merging into self.sts.spec
        # in case there is no call to patch_sts_overwrite then we won't "replace"
        # the existing sts object but "patch" it
        # if an sts_overwrite happens, we have to apply the patches to the self.sts.spec before overwriting
        utils.merge_patch_object(self.server_sts_patch, patch, none_deletes=True)

    def patch_sts_overwrite(self, patch: dict, patch_path: str) -> None:
        self.sts_changed = True
        if "template" in patch:
            self.sts_template_changed = True

        self.sts_spec_changed = True
        self._get_or_patch_sts_path(patch_path, patch)
        return

        if len(self.server_sts_patch):
            # update with accumulated patches before overwriting
            self.logger.info(f"Applying accumulated patches before applying overwriting patch patch={self.server_sts_patch['spec']}")
            #self.logger.info(f"STS.spec before apply={self.sts.spec}")
            utils.merge_patch_object(self.sts.spec, self.server_sts_patch["spec"], none_deletes=True)
            #self.logger.info(f"STS.spec after  apply={self.sts.spec}")
            self.server_sts_patch = {}

        #self.logger.info(f"patch_sts_overwrite: patch_path={patch_path} patch={patch}\n")

        base = self.sts.spec
        # first is leading backslash, then is spec, so we skip
        patch_path_elements = patch_path.split("/")[2:]
        if len(patch_path) > 1:
            for patch_path_element in patch_path_elements[0:-1]:
                #self.logger.info(f"{patch_path_element} in base = {patch_path_element in base}")
                assert patch_path_element in base
                base = base[patch_path_element]
            #  base[]
            #self.logger.info(f"\nExchanging {base[patch_path_elements[-1]]} \nwith\n{patch}")
            base[patch_path_elements[-1]] = patch
            self.logger.info(f"\n\nself.sts.spec={self.sts.spec}\n\n")


    def patch_deploy(self, patch: dict) -> None:
        self.deploy_changed = True
        self.logger.info(f"patch={patch}")
        utils.merge_patch_object(self.router_deploy_patch, patch, none_deletes=True)

    def create_configmap(self, namespace: str, name: str, body: dict, on_api_exception: Optional[OnApiExceptionHandler]) -> None:
        self.commands.append(ApiCommand(ApiCommandType.CREATE_CM, namespace, name, body, on_api_exception))

    def delete_configmap(self, namespace: str, name: str, on_api_exception: Optional[OnApiExceptionHandler]) -> None:
        self.commands.append(ApiCommand(ApiCommandType.DELETE_CM, namespace, name, None, on_api_exception))

    def replace_configmap(self, namespace: str, name: str, body: dict, on_api_exception: Optional[OnApiExceptionHandler]) -> None:
        self.commands.append(ApiCommand(ApiCommandType.REPLACE_CM, namespace, name, body, on_api_exception))

    def patch_configmap(self, namespace: str, name: str, patch: dict, on_api_exception: Optional[OnApiExceptionHandler]) -> None:
        self.commands.append(ApiCommand(ApiCommandType.PATCH_CM, namespace, name, patch, on_api_exception))

    def submit_patches(self) -> None:
        self.logger.info(f"InnoDBClusterObjectModifier::submit_patches sts_changed={self.sts_changed} sts_spec_changed={self.sts_spec_changed} len(router_deploy_patch)={len(self.router_deploy_patch)} len(commands)={len(self.commands)}")
        if (self.sts_changed or len(self.router_deploy_patch) or len(self.commands)):
              if len(self.commands):
                  for command in self.commands:
                      command.run(self.logger)
              if self.sts_changed:
                  if self.sts_spec_changed:
                      # this should apply server_sts_patch over self.sts.spec and empty self.server_sts_patch
                      # in the next step we will `replace` the STS and not `patch` it
                      # Only if the self.sts.spec is not touched should be server_sts_patch be applied, as otherwise
                      # changes to self.sts.spec will be skipped/forgotten
                      self._apply_server_sts_patch_to_sts_spec_if_needed()

                  if len(self.server_sts_patch):
                      self.logger.info(f"Patching STS.spec with {self.server_sts_patch}")
                      if self.sts_template_changed:
                          restart_patch = {"spec":{"template":{"metadata":{"annotations":{"kubectl.kubernetes.io/restartedAt":utils.isotime()}}}}}
                          utils.merge_patch_object(self.server_sts_patch, restart_patch)
                      api_apps.patch_namespaced_stateful_set(self.sts.metadata.name, self.sts.metadata.namespace, body=self.server_sts_patch)
                      self.server_sts_patch = {}
                  else:
                      self.logger.info(f"Replacing STS.spec with {self.sts.spec}")
                      # only if template has been changed. It could be that only scale up/down (spec['replica'] changed) has happened
                      # in that case if we set an annotation in the template the whole STS will be rolled over and this is not
                      # what is wanted. If replica count is increased only new pods are needed, respectively when replica is decreased.
                      # if replica is up and we set the annotation actually what will happen is rollover update and then scale up - we disturb the cluster
                      # (and tests will fail)
                      if self.sts_template_changed:
                          if not "annotations" in self.sts.spec["template"]["metadata"] or self.sts.spec["template"]["metadata"]["annotations"] is None:
                              self.sts.spec["template"]["metadata"]["annotations"] = {}
                          self.sts.spec["template"]["metadata"]["annotations"]["kubectl.kubernetes.io/restartedAt"] = utils.isotime()
                      api_apps.replace_namespaced_stateful_set(self.sts.metadata.name, self.sts.metadata.namespace, body=self.sts)
              if len(self.router_deploy_patch) and (deploy:= self.cluster.get_router_deployment()):
                  self.logger.info(f"Patching Deployment with {self.router_deploy_patch}")
                  router_objects.update_deployment_spec(deploy, self.router_deploy_patch)
