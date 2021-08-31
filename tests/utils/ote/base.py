# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import subprocess
from mysqloperator.controller import config

# Operator Test Environment


class BaseEnvironment:
    opt_operator_debug_level: int = 1

    def __init__(self):
        super().__init__()
        self._setup = True
        self._cleanup = True
        self._registry = None
        self._opeator_image = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.destroy()

    def setup_cluster(self, nodes=None, version=None, perform_setup=True, skip_cleanup=False):
        self._setup = perform_setup
        self._cleanup = not skip_cleanup

        if self._setup:
          self.delete_cluster()

          self.start_cluster(nodes, version)

        subprocess.call(["kubectl", "cluster-info"])

    def setup_operator(self, registry, deploy_files):
        self._registry = registry

        self.deploy_operator(deploy_files)

    def destroy(self):
        if self._cleanup:
            self.stop_cluster()
            self.delete_cluster()

    def cache_images(self, image_dir, images):
        versions = {}
        latest = {}
        print("Loading docker images...")

        # find latest version of each image
        for img in images:
            repo, _, ver = img.rpartition(":")
            if versions.get(repo, "0") < ver:
                versions[repo] = ver
                latest[repo] = img

        image_list = []

        for img in images:
            repo = img.rpartition(":")[0]
            if "/" in repo:
                name = repo.rpartition("/")[-1]
            else:
                name = repo

            imgname = img.rpartition("/")[-1]
            is_latest = img in latest.values()

            image_list.append((os.path.join(image_dir, imgname), is_latest))

        self.load_images(image_list)

    def load_images(self, image_list):
        pass

    def start_cluster(self, nodes, version):
        pass

    def stop_cluster(self):
        pass

    def delete_cluster(self):
        pass

    def deploy_operator(self, deploy_files):
        print("Deploying operator...")
        for f in deploy_files:
            args = ["kubectl", "create", "-f", f]
            print(" ".join(args))
            subprocess.call(args)
        # TODO change operator image to :latest
        # TODO re-add: "--log-file=",
        y = f"""
apiVersion: v1
kind: Namespace
metadata:
  name: mysql-operator
---
apiVersion: v1
kind: ServiceAccount
metadata:
  name: mysql-operator-sa
  namespace: mysql-operator
---
apiVersion: apps/v1
kind: Deployment
metadata:
  name: mysql-operator
  namespace: mysql-operator
  labels:
    version: "1.0"
spec:
  replicas: 1
  selector:
    matchLabels:
      name: mysql-operator
  template:
    metadata:
      labels:
        name: mysql-operator
    spec:
      serviceAccountName: mysql-operator-sa
      containers:
        - name: mysql-operator
          image: "mysql/mysql-shell:{config.DEFAULT_OPERATOR_VERSION_TAG}"
          imagePullPolicy: Never
          args: ["mysqlsh", "--log-level=@INFO", "--pym", "mysqloperator", "operator"]
          env:
            - name: MYSQL_OPERATOR_DEBUG
              value: "{self.opt_operator_debug_level}"
            - name: MYSQL_OPERATOR_IMAGE_PULL_POLICY
              value: Never
            - name: MYSQL_OPERATOR_DEFAULT_GR_IP_WHITELIST
              value: "172.17.0.0/8"
"""
        subprocess.run(["kubectl", "create", "-f", "-"],
                       input=y.encode("utf8"), check=True)

    def prepare_oci_bucket(self):
        bucket = {
            "name": None
        }
        return bucket

    def cleanup_oci_bucket(self):
        pass
