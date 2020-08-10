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

from .. import config, consts, utils
import yaml
import base64
from ..kubeutils import api_core, api_apps
import kopf

def prepare_router_service(spec):
    tmpl = f"""
apiVersion: v1
kind: Service
metadata:
  name: {spec.name}
  namespace: {spec.namespace}
  labels:
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
    app: mysqlrouter
    mysql.oracle.com/cluster: {spec.name}
  type: ClusterIP
"""
    return yaml.safe_load(tmpl)

def prepare_router_secrets(spec):
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

def prepare_router_replica_set(spec):
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
    mysql.oracle.com/cluster: {spec.name}
spec:
  replicas: {spec.routers}
  selector:
    matchLabels:
      app: mysqlrouter
      mysql.oracle.com/cluster: {spec.name}
  template:
    metadata:
      labels:
        app: mysqlrouter
        mysql.oracle.com/cluster: {spec.name}
    spec:
      containers:
      - name: router
        image: {spec.routerImage}
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
        - name: MYSQL_ROUTER_USER
          valueFrom:
            secretKeyRef:
              name: {spec.name}-router
              key: routerUsername
        - name: MYSQL_PASSWORD
          valueFrom:
            secretKeyRef:
              name: {spec.name}-router
              key: routerPassword
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


def update_size(cluster, size, logger):
    rs = cluster.get_router_replica_set()
    if rs:
        if size:
            patch = {"spec": {"replicas": size}}
            api_apps.patch_namespaced_replica_set(
                rs.metadata.name, rs.metadata.namespace, body=patch)
        else:
            logger.info(f"Deleting Router ReplicaSet")
            api_apps.delete_namespaced_replica_set(f"{cluster.name}-router", cluster.namespace)
    else:
        if size:
            logger.info(f"Creating Router ReplicaSet with replicas={size}")

            router_replicaset = prepare_router_replica_set(cluster.parsed_spec)
            kopf.adopt(router_replicaset)
            api_apps.create_namespaced_replica_set(namespace=cluster.namespace, body=router_replicaset)
