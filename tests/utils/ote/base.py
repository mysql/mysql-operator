# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import subprocess
from setup.config import g_ts_cfg

# Operator Test Environment


class BaseEnvironment:
    opt_operator_debug_level: int = 1

    def __init__(self):
        super().__init__()
        self._setup = True
        self._cleanup = True
        self.operator_host_path = None
        self.operator_mount_path = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.destroy()

    def setup_cluster(self, nodes=None, version=None, registry_cfg_path=None, perform_setup=True, cleanup=False):
        self._setup = perform_setup
        self._cleanup = cleanup

        if self._setup:
          self.delete_cluster()

          self.start_cluster(nodes, version, registry_cfg_path)

        subprocess.call(["kubectl", "cluster-info"])

    def setup_operator(self, deploy_files):
        self.deploy_operator(deploy_files)

    def destroy(self):
        if self._setup and self._cleanup:
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

    def mount_operator_path(self, path):
        self.operator_host_path = os.path.join("/tmp", os.path.basename(path))
        self.operator_mount_path = path

    def start_cluster(self, nodes, version, registry_cfg_path):
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
          image: "{g_ts_cfg.get_operator_image()}"
          imagePullPolicy: {g_ts_cfg.operator_pull_policy}
          args: ["mysqlsh", "--log-level=@INFO", "--pym", "mysqloperator", "operator"]
          env:
            - name: MYSQL_OPERATOR_DEFAULT_REPOSITORY
              value: "{g_ts_cfg.get_image_registry_repository()}"
            - name: MYSQL_OPERATOR_DEBUG
              value: "{self.opt_operator_debug_level}"
            - name: MYSQL_OPERATOR_IMAGE_PULL_POLICY
              value: {g_ts_cfg.operator_pull_policy}
            - name: MYSQL_OPERATOR_DEFAULT_GR_IP_WHITELIST
              value: "{g_ts_cfg.operator_gr_ip_whitelist}"
"""

        if self.operator_host_path:
            y = y.rstrip()
            y += f"""
          volumeMounts:
            - name: operator-code
              mountPath: "/usr/lib/mysqlsh/python-packages/mysqloperator"
      volumes:
        - name: operator-code
          hostPath:
            path: "{self.operator_host_path}"
            type: Directory
"""
        subprocess.run(["kubectl", "apply", "-f", "-"],
                       input=y.encode("utf8"), check=True)

    def prepare_oci_bucket(self):
        bucket = {
            "name": None
        }
        return bucket

    def cleanup_oci_bucket(self):
        pass
