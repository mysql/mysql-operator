# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import subprocess
import yaml
import time
from utils import auxutil
from utils import kutil
from setup.config import g_ts_cfg

# Operator Test Environment



def wait_operator(ns):
    def check_ready():
        for po in kutil.ls_po(ns, pattern="mysql-operator-.*"):
            if po["STATUS"] == "Running":
                return True
        return False

    i = 0
    while 1:
        if check_ready():
            break
        i += 1
        if i == 1:
            print("Waiting for operator to come up...")
        time.sleep(1)


class BaseEnvironment:
    opt_operator_debug_level: int = 1

    def __init__(self):
        super().__init__()
        self._setup = True
        self._cleanup = True
        self.operator_host_path = None
        self.operator_mount_path = None
        self._mounts = None

    def __enter__(self):
        return self

    def __exit__(self, type, value, tb):
        self.destroy()

    def setup_cluster(self, nodes=None, version=None, registry_cfg_path=None, perform_setup=True,
      mounts=None, custom_dns=None, cleanup=False):
        self._setup = perform_setup
        self._mounts = mounts
        self._cleanup = cleanup

        g_ts_cfg.k8s_context = self.get_context(g_ts_cfg.k8s_cluster)

        if self._setup:
          self.delete_cluster()

          self.start_cluster(nodes, version, registry_cfg_path)

          if custom_dns:
            self.add_custom_dns(custom_dns)

        subprocess.call(["kubectl", f"--context={g_ts_cfg.k8s_context}", "cluster-info"])

    def add_custom_dns(self, custom_dns):
      ote_dir = os.path.dirname(os.path.realpath(__file__))
      subprocess.check_call(f"{ote_dir}/add_custom_dns.sh {custom_dns}", shell=True)

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

    def get_context(self, cluster_name):
        return cluster_name

    def start_cluster(self, nodes, version, registry_cfg_path):
        pass

    def stop_cluster(self):
        pass

    def delete_cluster(self):
        pass

    def deploy_operator(self, deploy_files, override_deployment=True):
        print("Deploying operator...")
        for f in deploy_files:
            args = ["kubectl", f"--context={g_ts_cfg.k8s_context}", "apply", "-f", "-"]
            print(" ".join(args), "<", f)
            y = open(f).read()

            if f.endswith("deploy-operator.yaml"):
                arr = list(yaml.safe_load_all(y))
                operator = arr[-1]
                if override_deployment:
                    # strip last object (the operator Deployment), since we'll
                    # create it separately below
                    arr = arr[:-1]
                    y = yaml.safe_dump_all(arr)

            subprocess.run(args,
                       input=y.encode("utf8"), check=True)


        if self.operator_host_path:
            tmp = f"""
spec:
  template:
    spec:
      containers:
        - name: mysql-operator
          volumeMounts:
            - name: operator-code
              mountPath: "/usr/lib/mysqlsh/python-packages/mysqloperator"
      volumes:
        - name: operator-code
          hostPath:
            path: "{self.operator_host_path}"
            type: Directory
"""
            auxutil.merge_patch_object(operator, next(yaml.safe_load_all(tmp)))

        # TODO change operator image to :latest
        # TODO re-add: "--log-file=",
        patch = f"""
spec:
  template:
    spec:
      containers:
        - name: mysql-operator
          image: "{g_ts_cfg.get_operator_image()}"
          imagePullPolicy: {g_ts_cfg.operator_pull_policy}
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
        if override_deployment:
            auxutil.merge_patch_object(operator, next(yaml.safe_load_all(patch)))
            y = yaml.safe_dump(operator)
            print(y)
            subprocess.run(["kubectl", f"--context={g_ts_cfg.k8s_context}", "apply", "-f", "-"],
                          input=y.encode("utf8"), check=True)

        wait_operator("mysql-operator")


    def prepare_oci_bucket(self):
        bucket = {
            "name": None
        }
        return bucket

    def cleanup_oci_bucket(self):
        pass
