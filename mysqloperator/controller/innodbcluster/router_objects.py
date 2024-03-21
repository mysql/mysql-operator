# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from shlex import quote
from .cluster_api import InnoDBCluster, InnoDBClusterSpec
from ..kubeutils import client as api_client, ApiException
from .. import config, fqdn, utils, shellutils
import mysqlsh
import yaml
from ..kubeutils import api_apps, api_core, k8s_cluster_domain
import kopf
from logging import Logger
from typing import Optional, Callable


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
    targetPort: {spec.service.get_default_port_number(spec)}
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
  - name: mysql-rw-split
    port: {spec.router_rwsplitport}
    protocol: TCP
    targetPort: {spec.router_rwsplitport}
  - name: router-rest
    port: {spec.router_httpport}
    protocol: TCP
    targetPort: {spec.router_httpport}
  selector:
    component: mysqlrouter
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
  type: {spec.service.type}
"""
    service = yaml.safe_load(tmpl)

    if spec.service.annotations:
        service['metadata']['annotations'] = spec.service.annotations

    if spec.service.labels:
        service['metadata']['labels'] = spec.service.labels | service['metadata']['labels']

    return service


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

def get_bootstrap_and_tls_options(cluster: InnoDBCluster) -> tuple:
    spec = cluster.parsed_spec

    router_tls_exists = False
    ca_and_tls = None
    # Workaround for rotuer bug #33996132
    router_bootstrap_options = ["--conf-set-option=DEFAULT.unknown_config_option=warning"]
    router_bootstrap_options += spec.router.bootstrapOptions
    if not spec.tlsUseSelfSigned:
        ca_and_tls = cluster.get_ca_and_tls()
        ca_file_name = ca_and_tls.get("CA", "ca.pem")
        router_bootstrap_options += [f"--server-ssl-ca=/router-ssl/ca/{ca_file_name}",
            "--server-ssl-verify=VERIFY_IDENTITY",
            f"--ssl-ca=/router-ssl/ca/{ca_file_name}"
            ]
        if cluster.router_tls_exists():
            router_tls_exists = True
            router_bootstrap_options += ["--client-ssl-cert=/router-ssl/key/tls.crt",
                "--client-ssl-key=/router-ssl/key/tls.key"]

    return (" ".join(map(quote, router_bootstrap_options)), router_tls_exists, ca_and_tls)

def prepare_router_deployment(cluster: InnoDBCluster, logger, *,
                              init_only: bool = False) -> dict:
    # Start the router deployment with 0 replicas and only set it to the desired
    # value once the cluster is ONLINE, otherwise the router bootstraps could
    # timeout and fail unnecessarily.

    spec = cluster.parsed_spec

    (router_bootstrap_options, router_tls_exists, ca_and_tls) = get_bootstrap_and_tls_options(cluster)
    router_command = ['mysqlrouter', *spec.router.options]
    router_target = fqdn.idc_service_fqdn(cluster, logger)

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
      serviceAccountName: {spec.serviceAccountName}
      securityContext:
        runAsUser: 999
        runAsGroup: 999
        fsGroup: 999
      containers:
      - name: router
        image: {spec.router_image}
        imagePullPolicy: {spec.router_image_pull_policy}
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
        - name: MYSQL_HOST
          value: {router_target}
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
        volumeMounts:
        - name: tmpdir
          mountPath: /tmp
{utils.indent(spec.extra_router_volume_mounts if router_tls_exists else spec.extra_router_volume_mounts_no_cert, 8)}
        ports:
        - containerPort: {spec.router_rwport}
          name: mysqlrw
        - containerPort: {spec.router_rwxport}
          name: mysqlxrw
        - containerPort: {spec.router_rwsplitport}
          name: mysqlrwsplit
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
      volumes:
      - name: tmpdir
        emptyDir: {{}}
{utils.indent(spec.extra_router_volumes if router_tls_exists else spec.extra_router_volumes_no_cert, 6)}
"""
    deployment = yaml.safe_load(tmpl)

    container = deployment["spec"]["template"]["spec"]["containers"][0]

    container["args"] = router_command

    container["env"].append({
        "name": "MYSQL_ROUTER_BOOTSTRAP_EXTRA_OPTIONS",
        "value": router_bootstrap_options
    })

    metadata = {}
    if spec.router.podAnnotations:
        metadata['annotations'] = spec.router.podAnnotations
    if spec.router.podLabels:
        metadata['labels'] = spec.router.podLabels

    if len(metadata):
        utils.merge_patch_object(deployment["spec"]["template"], {"metadata" : metadata })

    if spec.router.podSpec:
        utils.merge_patch_object(deployment["spec"]["template"]["spec"],
                                 spec.router.podSpec, "spec.router.podSpec")

    # Cache the sha256 of the certs and keys we start it. This will prevent that when
    # the sidecar sees the unhandled secrets it will patch the deployment with the same hashes
    # and this won't restart the deployment. If however the TLS data has changed during IC boot
    # the handler will get the new values, hash them and this will trigger the reboot.
    if ca_and_tls:
        # the annotation keys should be the same as in restart_deployment_for_tls()
        tls_hashes_patch = {"spec": { "template": { "metadata": { "annotations": { }}}}}

        ca_pem = ca_and_tls.get(ca_and_tls.get("CA", "ca.pem"))
        ca_pem_sha256 = utils.sha256(ca_pem) if ca_pem else None
        if ca_pem_sha256:
          tls_hashes_patch['spec']['template']['metadata']['annotations']['mysql.oracle.com/ca.pem.sha256'] = ca_pem_sha256

        crl_pem = ca_and_tls.get('crl.pem')
        crl_pem_sha256 = utils.sha256(crl_pem) if crl_pem else None
        if crl_pem_sha256:
          tls_hashes_patch['spec']['template']['metadata']['annotations']['mysql.oracle.com/crl.pem.sha256'] = crl_pem_sha256

        router_tls_crt = ca_and_tls.get('router_tls.crt')
        router_tls_crt_sha256 = utils.sha256(router_tls_crt) if router_tls_crt else None
        if router_tls_crt_sha256:
          tls_hashes_patch['spec']['template']['metadata']['annotations']['mysql.oracle.com/router_tls.crt.sha256'] = router_tls_crt_sha256

        router_tls_key = ca_and_tls.get('router_tls.key')
        router_tls_key_sha256 = utils.sha256(router_tls_key) if router_tls_key else None
        if router_tls_key_sha256:
          tls_hashes_patch['spec']['template']['metadata']['annotations']['mysql.oracle.com/router_tls.key.sha256'] = router_tls_key_sha256

        utils.merge_patch_object(deployment, tls_hashes_patch)

    return deployment


def update_labels_or_annotations(what: str, what_value: dict, cluster: InnoDBCluster, logger: Logger) -> None:
    deploy = cluster.get_router_deployment()
    # if the size is 0 it might not exist. In this case the proper labels and annotations will be set when eventually created
    if deploy:
        patch = {"spec": {"template": { "metadata" : { what : what_value }}}}
        api_apps.patch_namespaced_deployment(deploy.metadata.name, deploy.metadata.namespace, body=patch)


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

            router_deployment = prepare_router_deployment(cluster, logger)
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

def update_bootstrap_options(dpl: api_client.V1Deployment, cluster: InnoDBCluster, logger: Logger) -> None:
    (router_bootstrap_options, _, _) = get_bootstrap_and_tls_options(cluster)
    patch = [{
        "name": "MYSQL_ROUTER_BOOTSTRAP_EXTRA_OPTIONS",
        "value": router_bootstrap_options
    }]
    update_router_container_template_property(dpl, "env", patch, logger)

def update_options(dpl: api_client.V1Deployment, spec: InnoDBClusterSpec, logger: Logger) -> None:
    router_command = ["mysqlrouter", *spec.router.options]
    update_router_container_template_property(dpl, "args", router_command, logger)

def update_service(svc: api_client.V1Deployment, spec: InnoDBClusterSpec,
                   logger: Logger) -> None:
    body = prepare_router_service(spec)
    print(body)
    api_core.patch_namespaced_service(
        svc.metadata.name, svc.metadata.namespace, body=body)

def get_update_deployment_template_metadata_annotation(dpl: api_client.V1Deployment, annotation_name: str, annotation_value: str) -> str:
    patch = {"spec": {"template": {"metadata": { "annotations": { annotation_name: annotation_value }}}}}
    return patch


def restart_deployment_for_tls(dpl: api_client.V1Deployment, router_tls_crt, router_tls_key, ca_pem, crl_pem: Optional[str], logger: Logger) -> bool:
    logger.info(f"restart_deployment_for_tls \ntrouter_ls_crt is None={router_tls_crt is None} \nrouter_tls_key is None={router_tls_key is None} \nca_pem is None={ca_pem is None} \ncrl_pem is None={crl_pem  is None}")
    logger.info(f"dpl.spec.template.metadata.annotations={dpl.spec.template.metadata.annotations}")

    base = None

    secrets = {'router_tls.crt': router_tls_crt, 'router_tls.key': router_tls_key, 'ca.pem': ca_pem, 'crl.pem': crl_pem}

    for sec_name, sec_value in secrets.items():
        if not sec_value is None:
            ann_name = f"mysql.oracle.com/{sec_name}.sha256"
            new_ann_value = utils.sha256(sec_value)
            patch = None
            if dpl.spec.template.metadata.annotations is None or dpl.spec.template.metadata.annotations.get(ann_name) is None:
                patch = get_update_deployment_template_metadata_annotation(dpl, ann_name, new_ann_value)
            else:
                if dpl.spec.template.metadata.annotations.get(ann_name) != new_ann_value:
                    patch = get_update_deployment_template_metadata_annotation(dpl, ann_name, new_ann_value)
                    logger.info(f"Annotation {ann_name} has a different value")
                else:
                    logger.info(f"Annotation {ann_name} unchanged")

            if not patch is None:
                if base is None:
                    base = patch
                else:
                    utils.merge_patch_object(base, patch)

    if not base is None:
        patch = get_update_deployment_template_metadata_annotation(dpl, 'kubectl.kubernetes.io/restartedAt', utils.isotime())
        utils.merge_patch_object(base, patch)
        logger.info(f"Deployment needs a restart. Patching with {base}")
        update_deployment_spec(dpl, base)
        return True

    logger.info("TLS data hasn't changed. Deployment doesn't need a restart")
    return False


def update_router_account(cluster: InnoDBCluster, on_nonupdated: Optional[Callable], logger: Logger) -> None:
      if not cluster.ready:
          logger.info(f"Cluster {cluster.namespace}/{cluster.name} not ready. Skipping router account update.")
          return

      try:
          user, password = cluster.get_router_account()
      except ApiException as e:
          if e.status == 404:
              # Should not happen, as cluster.ready should be False for a cluster with missing router account
              # In any case handle this case and skip
              logger.warning(f"Could not find router account of {cluster.name} in {cluster.namespace}")
              return
          raise

      updated = False

      for pod in cluster.get_pods():
          if pod.deleting:
              continue
          try:
              with shellutils.DbaWrap(shellutils.connect_dba(pod.endpoint_co, logger, max_tries=3)) as dba:
                  dba.get_cluster().setup_router_account(user, {"update": True})
                  updated = True
                  break

          except mysqlsh.Error as e:
              logger.warning(f"Could not connect to {pod.endpoint_co}: {e}")
              continue

      if not updated and on_nonupdated:
          on_nonupdated()
