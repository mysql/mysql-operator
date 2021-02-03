# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from .cluster_api import InnoDBCluster, InnoDBClusterSpec
from .. import config, utils
import yaml
from ..kubeutils import api_apps
import kopf
from logging import Logger


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
    port: {spec.router_rwport}
    protocol: TCP
    targetPort: {spec.router_rwport}
  - name: mysqlx
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


def prepare_router_replica_set(spec: InnoDBClusterSpec, *,
                               init_only: bool = False) -> dict:
    # Start the router replicaset with 0 replicas and only set it to the desired
    # value once the cluster is ONLINE, otherwise the router bootstraps could
    # timeout and fail unnecessarily.

    # TODO livenessProbe
    # TODO setup http
    tmpl = f"""
apiVersion: apps/v1
kind: ReplicaSet
metadata:
  name: {spec.name}-router
  label:
    tier: mysql
    mysql.oracle.com/cluster: {spec.name}
spec:
  replicas: {spec.router.instances or 0 if not init_only else 0}
  selector:
    matchLabels:
      component: mysqlrouter
      tier: mysql
      mysql.oracle.com/cluster: {spec.name}
  template:
    metadata:
      labels:
        component: mysqlrouter
        tier: mysql
        mysql.oracle.com/cluster: {spec.name}
    spec:
      containers:
      - name: router
        image: {spec.router_image}
        imagePullPolicy: {spec.router_image_pull_policy}
        env:
        - name: MYSQL_HOST
          value: {spec.name}-0.{spec.name}-instances.{spec.namespace}.svc.cluster.local
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
        ports:
        - containerPort: {spec.router_rwport}
          name: mysqlrw
        - containerPort: {spec.router_rwxport}
          name: mysqlxrw
        - containerPort: {spec.router_roport}
          name: mysqlro
        - containerPort: {spec.router_rwxport}
          name: mysqlxro
        - containerPort: {spec.router_httpport}
          name: http
"""
    return yaml.safe_load(tmpl)


def update_size(cluster: InnoDBCluster, size: int, logger: Logger) -> None:
    rs = cluster.get_router_replica_set()
    if rs:
        if size:
            patch = {"spec": {"replicas": size}}
            api_apps.patch_namespaced_replica_set(
                rs.metadata.name, rs.metadata.namespace, body=patch)
        else:
            logger.info(f"Deleting Router ReplicaSet")
            api_apps.delete_namespaced_replica_set(
                f"{cluster.name}-router", cluster.namespace)
    else:
        if size:
            logger.info(f"Creating Router ReplicaSet with replicas={size}")

            router_replicaset = prepare_router_replica_set(cluster.parsed_spec)
            kopf.adopt(router_replicaset)
            api_apps.create_namespaced_replica_set(
                namespace=cluster.namespace, body=router_replicaset)
