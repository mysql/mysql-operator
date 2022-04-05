# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from .cluster_api import InnoDBCluster, InnoDBClusterSpec
from ..kubeutils import client as api_client, ApiException
from .. import config, utils
import yaml
from ..kubeutils import api_apps
import kopf
from logging import Logger
from typing import Optional


def prepare_router_service(spec: InnoDBClusterSpec) -> dict:
    tmpl = f"""
apiVersion: v1
kind: Service
metadata:
  name: {spec.name}
  namespace: {spec.namespace}
  labels:
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
spec:
  ports:
  - name: mysql
    port: {spec.mysql_port}
    protocol: TCP
    targetPort: {spec.router_rwport}
  - name: mysqlx
    port: {spec.mysql_xport}
    protocol: TCP
    targetPort: {spec.router_rwxport}
  - name: mysql-alternate
    port: {spec.router_rwport}
    protocol: TCP
    targetPort: {spec.router_rwport}
  - name: mysqlx-alternate
    port: {spec.router_rwxport}
    protocol: TCP
    targetPort: {spec.router_rwxport}
  - name: mysql-ro
    port: {spec.router_roport}
    protocol: TCP
    targetPort: {spec.router_roport}
  - name: mysqlx-ro
    port: {spec.router_roxport}
    protocol: TCP
    targetPort: {spec.router_roxport}
  selector:
    component: mysqlrouter
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
  type: ClusterIP
"""
    return yaml.safe_load(tmpl)


def prepare_router_secrets(spec: InnoDBClusterSpec) -> dict:
    router_user = utils.b64encode(config.ROUTER_METADATA_USER_NAME)
    router_pwd = utils.b64encode(utils.generate_password())

    # We use a separate secrets object for the router, so that we don't need to
    # give access for the main secret to router instances.
    tmpl = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {spec.name}-router
data:
  routerUsername: {router_user}
  routerPassword: {router_pwd}
"""
    return yaml.safe_load(tmpl)


def prepare_router_deployment(cluster: InnoDBCluster, *,
                              init_only: bool = False) -> dict:
    # Start the router deployment with 0 replicas and only set it to the desired
    # value once the cluster is ONLINE, otherwise the router bootstraps could
    # timeout and fail unnecessarily.

    spec = cluster.parsed_spec

    router_tls_exists = False
    # Workaround fro rotuer bug #33996132
    router_bootstrap_options = ["--conf-set-option=DEFAULT.unknown_config_option=warning"]
    if not spec.tlsUseSelfSigned:
        router_bootstrap_options += ["--server-ssl-ca=/router-ssl/ca.pem",
            "--server-ssl-verify=VERIFY_IDENTITY",
            "--ssl-ca=/router-ssl/ca.pem"
            ]
        if cluster.router_tls_exists():
            router_tls_exists = True
            router_bootstrap_options += ["--client-ssl-cert=/router-ssl/tls.crt",
                "--client-ssl-key=/router-ssl/tls.key"]

    tmpl = f"""
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {spec.name}-router
  label:
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
    app.kubernetes.io/name: mysql-innodbcluster
    app.kubernetes.io/instance: mysql-innodbcluster-{spec.name}-router
    app.kubernetes.io/component: router
    app.kubernetes.io/managed-by: mysql-operator
    app.kubernetes.io/created-by: mysql-operator
spec:
  replicas: {spec.router.instances or 1 if not init_only else 0}
  selector:
    matchLabels:
      component: mysqlrouter
      tier: mysql
      mysql.oracle.com/cluster: {spec.name}
      app.kubernetes.io/name: mysql-router
      app.kubernetes.io/instance: mysql-innodbcluster-{spec.name}-router
      app.kubernetes.io/component: router
      app.kubernetes.io/managed-by: mysql-operator
      app.kubernetes.io/created-by: mysql-operator
  template:
    metadata:
      labels:
        component: mysqlrouter
        tier: mysql
        mysql.oracle.com/cluster: {spec.name}
        app.kubernetes.io/name: mysql-router
        app.kubernetes.io/instance: mysql-innodbcluster-{spec.name}-router
        app.kubernetes.io/component: router
        app.kubernetes.io/managed-by: mysql-operator
        app.kubernetes.io/created-by: mysql-operator
    spec:
{utils.indent(spec.service_account_name, 6)}
{utils.indent(spec.image_pull_secrets, 6)}
      securityContext:
        allowPrivilegeEscalation: false
        privileged: false
        readOnlyRootFilesystem: true
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
        capabilities:
          # Check mysql/packaging/deb-in/extra/apparmor-profile-router.in for the caps needed
          add:
          - "CHOWN"
          - "DAC_OVERRIDE"
          - "FOWNER"
          - "SETGID"
          - "SETUID"
          - "SYS_RESOURCE"
          drop:
          - "AUDIT_CONTROL"
          - "AUDIT_READ"
          - "AUDIT_WRITE"
          - "BLOCK_SUSPEND"
# CAP_BPF was introduced in Linux 5.8 which could be too new for some K8s installations
#          - "BPF"
# CAP_CHECKPOINT_RESTORE was introduced in Linux 5.9 which could be too new for some K8s installations
#          - "CHECKPOINT_RESTORE"
          - "DAC_READ_SEARCH"
          - "FSETID"
          - "IPC_LOCK"
          - "IPC_OWNER"
          - "KILL"
          - "LEASE"
          - "LINUX_IMMUTABLE"
          - "MAC_ADMIN"
          - "MAC_OVERRIDE"
          - "MKNOD"
          - "NET_ADMIN"
          - "NET_BIND_SERVICE"
          - "NET_BROADCAST"
          - "NET_RAW"
# CAP_PERFMON was introduced in Linux 5.8 which could be too new for some K8s installations
#          - "PERFMON"
          - "SETFCAP"
          - "SETPCAP"
          - "SYS_ADMIN"
          - "SYS_BOOT"
          - "SYS_CHROOT"
          - "SYS_MODULE"
          - "SYS_NICE"
          - "SYS_PACCT"
          - "SYS_PTRACE"
          - "SYS_RAWIO"
          - "SYS_TIME"
          - "SYS_TTY_CONFIG"
          - "SYSLOG"
          - "WAKE_ALARM"
      containers:
      - name: router
        image: {spec.router_image}
        imagePullPolicy: {spec.router_image_pull_policy}
        env:
        - name: MYSQL_HOST
          value: {spec.name}-instances.{spec.namespace}.svc.cluster.local
        - name: MYSQL_PORT
          value: "3306"
        - name: MYSQL_USER
          valueFrom:
            secretKeyRef:
              name: {spec.name}-router
              key: routerUsername
        - name: MYSQL_PASSWORD
          valueFrom:
            secretKeyRef:
              name: {spec.name}-router
              key: routerPassword
        - name: MYSQL_CREATE_ROUTER_USER
          value: "0"
        - name: MYSQL_ROUTER_BOOTSTRAP_EXTRA_OPTIONS
          value: "{' '.join(router_bootstrap_options)}"
        volumeMounts: {'[]' if not spec.extra_router_volume_mounts else ''}
{utils.indent(spec.extra_router_volume_mounts, 8)}
        ports:
        - containerPort: {spec.router_rwport}
          name: mysqlrw
        - containerPort: {spec.router_rwxport}
          name: mysqlxrw
        - containerPort: {spec.router_roport}
          name: mysqlro
        - containerPort: {spec.router_roxport}
          name: mysqlxro
        - containerPort: {spec.router_httpport}
          name: http
        readinessProbe:
          exec:
            command:
            - cat
            - /tmp/mysqlrouter/mysqlrouter.conf
        livenessProbe:
          failureThreshold: 3
          httpGet:
            path: /api/20190715/swagger.json
            port: http
            scheme: HTTPS
          periodSeconds: 10
          successThreshold: 1
          timeoutSeconds: 1

      volumes: {'[]' if not spec.extra_router_volumes else ''}
{utils.indent(spec.extra_router_volumes if router_tls_exists else spec.extra_router_volumes_no_cert, 6)}
"""
    deployment = yaml.safe_load(tmpl)
    if spec.router.podSpec:
        utils.merge_patch_object(deployment["spec"]["template"]["spec"],
                                 spec.router.podSpec, "spec.router.podSpec")

    return deployment

def get_size(cluster: InnoDBCluster) -> int:
    deploy = cluster.get_router_deployment()
    if deploy:
        return deploy.spec.replicas
    return None

def update_size(cluster: InnoDBCluster, size: int, logger: Logger) -> None:
    deploy = cluster.get_router_deployment()
    if deploy:
        if size:
            patch = {"spec": {"replicas": size}}
            api_apps.patch_namespaced_deployment(
                deploy.metadata.name, deploy.metadata.namespace, body=patch)
        else:
            logger.info(f"Deleting Router Deployment")
            api_apps.delete_namespaced_deployment(
                f"{cluster.name}-router", cluster.namespace)
    else:
        if size:
            logger.info(f"Creating Router Deployment with replicas={size}")

            router_deployment = prepare_router_deployment(cluster)
            kopf.adopt(router_deployment)
            api_apps.create_namespaced_deployment(
                namespace=cluster.namespace, body=router_deployment)


def update_deployment_spec(dpl: api_client.V1Deployment, patch: dict) -> None:
    api_apps.patch_namespaced_deployment(
        dpl.metadata.name, dpl.metadata.namespace, body=patch)


def update_router_container_template_property(dpl: api_client.V1Deployment,
                                              property_name: str, property_value: str,
                                              logger: Logger) -> None:
    patch = {"spec": {"template":
                      {"spec": {
                          "containers": [
                               {"name": "router", property_name: property_value}
                          ]
                        }
                      }
                    }
            }
    update_deployment_spec(dpl, patch)


def propagate_router_field_change_to_sts(cluster: InnoDBCluster, field: str, logger: Logger) -> None:
    pass


def update_router_image(dpl: api_client.V1Deployment, spec: InnoDBClusterSpec, logger: Logger) -> None:
    update_router_container_template_property(dpl, "image", spec.router_image, logger)


def update_router_version(cluster: InnoDBCluster, logger: Logger) -> None:
    dpl = cluster.get_router_deployment()
    if dpl:
        return update_router_image(dpl, cluster.parsed_spec, logger)


def update_pull_policy(dpl: api_client.V1Deployment, spec: InnoDBClusterSpec, logger: Logger) -> None:
    # NOTE: We are using spec.mysql_image_pull_policy and not spec.router_image_pull_policy
    #       (both are decorated), becase the latter will read the value from the Router Deployment
    #       and thus the value will be constant. We are using the former to push the value down
    update_router_container_template_property(dpl, "imagePullPolicy", spec.mysql_image_pull_policy, logger)


def update_deployment_template_spec_property(dpl: api_client.V1Deployment, property_name: str, property_value: str) -> None:
    patch = {"spec": {"template": {"spec": { property_name: property_value }}}}
    update_deployment_spec(dpl, patch)


def get_update_deployment_template_metadata_annotation(dpl: api_client.V1Deployment, annotation_name: str, annotation_value: str) -> str:
    patch = {"spec": {"template": {"metadata": { "annotations": { annotation_name: annotation_value }}}}}
    return patch


def restart_deployment_for_tls(dpl: api_client.V1Deployment, tls_crt, tls_key, ca_pem, crl_pem: Optional[str], logger: Logger) -> None:
    logger.info(f"restart_deployment_for_tls \ntls_crt is None={tls_crt is None} \ntls_key is None={tls_key is None} \nca_pem is None={ca_pem is None} \ncrl_pem is None={crl_pem  is None}")
    logger.info(f"dpl.spec.template.metadata.annotations={dpl.spec.template.metadata.annotations}")

    base = None

    secrets = {'tls.crt': tls_crt, 'tls.key': tls_key, 'ca.pem': ca_pem, 'crl.pem': crl_pem}

    for sec_name, sec_value in secrets.items():
        if not sec_value is None:
            ann_name = f"mysql.oracle.com/{sec_name}.sha256"
            new_ann_value = utils.sha256(sec_value)
            patch = None
            if dpl.spec.template.metadata.annotations is None or dpl.spec.template.metadata.annotations.get(ann_name) is None:
                patch = get_update_deployment_template_metadata_annotation(dpl, ann_name, new_ann_value)
            elif dpl.spec.template.metadata.annotations.get(ann_name) != new_ann_value:
                patch = get_update_deployment_template_metadata_annotation(dpl, ann_name, new_ann_value)

            if not patch is None:
                if base is None:
                    base = patch
                else:
                    utils.merge_patch_object(base, patch)

    if not base is None:
        patch = get_update_deployment_template_metadata_annotation(dpl, 'kubectl.kubernetes.io/restartedAt', utils.isotime())
        utils.merge_patch_object(base, patch)
        logger.info(f"restart_deployment_for_tls patching with {base}")
        update_deployment_spec(dpl, base)
