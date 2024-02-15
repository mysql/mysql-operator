# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from logging import Logger, getLogger
import kopf
from typing import List, Dict, Optional
from ..kubeutils import client as api_client, ApiException
from .. import utils, config, consts
from .cluster_api import InnoDBCluster, AbstractServerSetSpec, InnoDBClusterSpec, ReadReplicaSpec, InnoDBClusterSpecProperties
from . import cluster_controller
from .. import fqdn
import yaml
from ..kubeutils import api_core, api_apps, api_customobj, k8s_cluster_domain
import base64

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
        print(f"\t\tadd_to_sts_cb: Checking subsystem {subsystem}")
        for add_to_svc_cb in spec.get_add_to_svc_cbs[subsystem]:
            print(f"\t\tAdding {subsystem} bits")
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
      terminationGracePeriodSeconds: 120
      initContainers:
      - name: fixdatadir
        image: {spec.operator_image}
        imagePullPolicy: {spec.sidecar_image_pull_policy}
        command: ["bash", "-c", "chown 27:27 /var/lib/mysql && chmod 0700 /var/lib/mysql"]
        securityContext:
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
          capabilities:
            drop:
            - ALL
        env:
        - name: MYSQL_INITIALIZE_ONLY
          value: "1"
        - name: MYSQL_ROOT_PASSWORD
          valueFrom:
            secretKeyRef:
              name: {spec.secretName}
              key: rootPassword
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
        print("\t\tAdding keyring bits")
        spec.keyring.add_to_sts_spec(statefulset)

    for subsystem in spec.add_to_sts_cbs:
        print(f"\t\tadd_to_sts_cb: Checking subsystem {subsystem}")
        for add_to_sts_cb in spec.add_to_sts_cbs[subsystem]:
            print(f"\t\tAdding {subsystem} bits")
            add_to_sts_cb(statefulset, logger)

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

    monitor = f"""
apiVersion: monitoringcoreos.com/v1
kind: ServiceMonitor
metadata:
  name: {cluster.name}
spec:
  selector:
    matchLabels:
      mysql.oracle.com/cluster: {cluster.name}
      tier: mysql
  endpoints:
  - port: metrics
    path: /metrics
"""
    monitor = yaml.safe_load(monitor)

    if cluster.parsed_spec.metrics and cluster.parsed_spec.metrics.monitor_spec:
        utils.merge_patch_object(monitor["spec"],
                                 cluster.parsed_spec.metrics.monitor_spec,
                                 "spec.metrics.monitorSpec")

    return monitor


def reconcile_stateful_set(cluster: InnoDBCluster, logger: Logger) -> None:
    logger.info("reconcile_stateful_set")
    patch = prepare_cluster_stateful_set(cluster.parsed_spec, logger)

    logger.info(f"reconcile_stateful_set: patch={patch}")
    api_apps.patch_namespaced_stateful_set(
        cluster.name, cluster.namespace, body=patch)


def update_stateful_set_spec(sts : api_client.V1StatefulSet, patch: dict) -> None:
    api_apps.patch_namespaced_stateful_set(
        sts.metadata.name, sts.metadata.namespace, body=patch)


def update_mysql_image(sts: api_client.V1StatefulSet, cluster: InnoDBCluster,
                       spec: AbstractServerSetSpec, logger: Logger) -> None:
    """Update MySQL Server image

    This will also update the sidecar container to the current operator version,
    so that a single rolling upgrade covers both and we don't require a restart
    for upgrading sidecar.
    """

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
        (cm, key_sts_patch) = keyring_update
        utils.merge_patch_object(patch["spec"]["template"], key_sts_patch)

        kopf.adopt(cm)
        api_core.create_namespaced_config_map(spec.namespace, cm)

        initconf_patch = [{"op": "remove", "path": "/data/03-keyring-oci.cnf"}]
        try:
            api_core.patch_namespaced_config_map(f"{spec.cluster_name}-initconf",
                                                    spec.namespace, initconf_patch)
        except ApiException as exc:
            # This might happen during a retry or some other case where it was
            # removed already
            logger.info(f"Failed to remove keyring config from initconf, ignoring: {exc}")

    cm = prepare_initconf(cluster, spec, logger)
    api_core.patch_namespaced_config_map(
        cm['metadata']['name'], sts.metadata.namespace, body=cm)

    update_stateful_set_spec(sts, patch)


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


def update_pull_policy(sts: api_client.V1StatefulSet, spec: InnoDBClusterSpec, logger: Logger) -> None:
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
    update_stateful_set_spec(sts, patch)

def update_template_property(sts: api_client.V1StatefulSet, property_name: str, property_value: str, logger: Logger) -> None:
    patch = {"spec": {"template": {"spec": { property_name: property_value }}}}
    update_stateful_set_spec(sts, patch)


def update_objects_for_subsystem(subsystem: InnoDBClusterSpecProperties,
                                 cluster: InnoDBCluster,
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
                        cluster.delete_configmap(cm_name)
                        continue

                    data_differs = current_cm.data != new_cm["data"]
                    if data_differs:
                        print(f"\t\t\tReplacing CM {cluster.namespace}/{cm_name}")
                        current_cm.data = new_cm["data"]
                        api_core.replace_namespaced_config_map(cm_name, cluster.namespace, body=current_cm)
                else:
                    print(f"\t\t\tNo such cm exists. Creating {cluster.namespace}/{new_cm}")
                    kopf.adopt(new_cm)
                    api_core.create_namespaced_config_map(cluster.namespace, new_cm)

    if subsystem in spec.add_to_sts_cbs:
        print(f"\t\tCurrent container count: {len(sts.spec.template.spec.containers)}")
        print(f"\t\tWalking over add_to_sts_cbs len={len(spec.add_to_sts_cbs[subsystem])}")
        changed = False
        for add_to_sts_cb in spec.add_to_sts_cbs[subsystem]:
            changed = True
            print("\t\t\tPatching STS")
            add_to_sts_cb(sts, logger)
        if changed:
            print(f"\t\t\tNew container count: {len(sts.spec.template.spec.containers)}")
            if not hasattr(sts.spec.template.metadata, "annotations") or sts.spec.template.metadata.annotations is None:
                setattr(sts.spec.template.metadata, "annotations", {})
            sts.spec.template.metadata.annotations["kubectl.kubernetes.io/restartedAt"] = utils.isotime()
            api_apps.replace_namespaced_stateful_set(sts.metadata.name, sts.metadata.namespace, body=sts)

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


def update_objects_for_logs(cluster: InnoDBCluster, logger: Logger) -> None:
    subsystem = InnoDBClusterSpecProperties.LOGS.value
    update_objects_for_subsystem(subsystem, cluster, logger)

def update_objects_for_metrics(cluster: InnoDBCluster, logger: Logger) -> None:
    subsystem = InnoDBClusterSpecProperties.METRICS.value
    update_objects_for_subsystem(subsystem, cluster, logger)


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
