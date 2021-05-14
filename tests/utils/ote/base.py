# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
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

import os
import subprocess
from mysqloperator.controller import config

# Operator Test Environment


class BaseEnvironment:
    opt_operator_debug_level: int = 1

    def __init__(self):
        super().__init__()
        self._cleanup = True
        self._registry = None
        self._opeator_image = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.destroy()

    def setup_cluster(self, nodes=None, version=None, skip_cleanup=False):
        self._cleanup = not skip_cleanup

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
          image: "mysql/mysql-shell:{config.DEFAULT_SHELL_VERSION_TAG}"
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
