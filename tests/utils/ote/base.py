# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import subprocess
import yaml
import time
import requests
import json
from utils import auxutil
from utils import kutil
from setup.config import g_ts_cfg, Config


# Operator Test Environment

def wait_pod(ns, pattern, pod_pretty_name):
    def check_ready():
        for po in kutil.ls_po(ns, pattern=pattern):
            if po["STATUS"] == "Running":
                return True
        return False

    def store_timeout_diagnostics():
        reason = ""
        for po in kutil.ls_po(ns, pattern=pattern):
            pod_name = po["NAME"]
            kutil.store_operator_diagnostics(ns, pod_name)
            reason += f"Timeout waiting for {pod_pretty_name} {ns}/{pod_name}"
        return reason

    Timeout = 600
    i = 0
    while 1:
        if check_ready():
            break
        i += 1
        if i == 1:
            print(f"Waiting for {pod_pretty_name} to come up...")
        if i == Timeout:
            reason = store_timeout_diagnostics()
            raise Exception(reason)
        time.sleep(1)

def wait_operator(ns, deploy_name):
    pattern = f"{deploy_name}-.*"
    wait_pod(ns, pattern, "operator")

def wait_local_path_provisioner(ns, deploy_name):
    pattern = f"{deploy_name}-.*"
    wait_pod(ns, pattern, "local path shared provisioner")


class BaseEnvironment:
    opt_operator_debug_level: int = 0

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

    def setup_cluster(self, nodes=None, node_memory=None, version=None, cfg_path=None, perform_setup=True,
      mounts=None, custom_dns=None, cleanup=False, ip_family=None):
        self._setup = perform_setup
        self._mounts = mounts
        self._cleanup = cleanup

        if not g_ts_cfg.k8s_context:
          g_ts_cfg.k8s_context = self.resolve_context(g_ts_cfg.k8s_cluster)

        if self._setup:
          self.delete_cluster()

          self.start_cluster(nodes, node_memory, version, cfg_path, ip_family)

          if custom_dns:
            self.add_custom_dns(custom_dns)

          if self.can_start_azure():
            self.start_azure()

        if g_ts_cfg.local_path_provisioner_install:
            sc_installed = self.local_path_shared_installed()
            print(f"Storage class installed: {sc_installed}")
            self.check_and_create_local_path_shared_directory(g_ts_cfg.get_local_path_provisioner_shared_path())
            if not sc_installed:
                self.install_local_path_shared_storage_class()

        ret = subprocess.call([g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", "cluster-info"])
        if ret:
          raise Exception(f"cannot get cluster-info for context '{g_ts_cfg.k8s_context}'")

        self.generate_kubectl_info()

    def add_custom_dns(self, custom_dns):
      ote_dir = os.path.dirname(os.path.realpath(__file__))
      script_path = os.path.join(ote_dir, "add_custom_dns.sh")
      shell_cmd = [script_path, g_ts_cfg.kubectl_path, g_ts_cfg.k8s_context, custom_dns]
      subprocess.check_call(shell_cmd)

    def can_start_azure(self):
        return not g_ts_cfg.azure_skip and g_ts_cfg.start_azure and g_ts_cfg.azure_config_file and g_ts_cfg.azure_container_name

    def start_azure(self):
        script_path = os.path.join(g_ts_cfg.get_ci_dir(), "jobs/auxiliary/start-azure.sh")
        shell_cmd = [
            script_path,
            g_ts_cfg.kubectl_path,
            g_ts_cfg.k8s_context,
            g_ts_cfg.get_image(Config.Image.AZURE_STORAGE),
            g_ts_cfg.get_image(Config.Image.AZURE_CLI),
            g_ts_cfg.azure_config_file,
            g_ts_cfg.azure_container_name
        ]
        subprocess.check_call(shell_cmd)

    def local_path_shared_installed(self) -> bool:
        sc_name = "local-path-shared"
        scs = kutil.ls_sc()
        print(f"Installed storage classes: {[sc['NAME'] for sc in scs]}")
        scs = kutil.ls_sc(f"{sc_name}\s*.*")
        if len(scs) > 1:
            raise Exception(f"Multiple StorageClass instances of {sc_name}?\n{scs}")
        return False if len(scs) == 0 else (scs[0]["NAME"].find("(default)") != -1)

    def check_and_create_local_path_shared_directory(self, path):
        if not os.path.exists(path):
            print(f"Creating shared path directory: {path}")
            os.mkdir(path)
        elif not os.path.isdir(path):
            raise Exception(f"{path} exists but is not a directory")

    def install_local_path_shared_storage_class(self):
        resp = requests.get(g_ts_cfg.get_local_path_provisioner_manifest_url())
        arr = list(yaml.safe_load_all(resp.text))
        to_apply = []
        sc_name = "local-path-shared"
        ns = sc_name
        provisioner = "local-path-provisioner-shared"
        provisioner_fqn = f"cluster.local/{provisioner}"
        sa = f"{provisioner}-service-account"
        cluster_role = f"{provisioner}-role"
        cr_binding = f"{provisioner}-bind"
        objects_labels = {
            "app.kubernetes.io/name": "local-path-shared",
            "app.kubernetes.io/instance": provisioner,
        }
        for el in arr:
            if "kind" in el:
                el["metadata"]["labels"] = objects_labels

                if el["kind"].lower() == "namespace":
                    el["metadata"]["name"] = ns
                elif el["kind"].lower() == "serviceaccount":
                    el["metadata"]["name"] = sa
                    el["metadata"]["namespace"] = ns
                elif el["kind"].lower() == "clusterrole":
                    el["metadata"]["name"] = cluster_role
                elif el["kind"].lower() == "clusterrolebinding":
                    el["metadata"]["name"] = cr_binding
                    el["roleRef"]["name"] = cluster_role
                    el["subjects"][0]["name"] = sa
                    el["subjects"][0]["namespace"] = ns
                elif el["kind"].lower() == "deployment":
                    print(el["spec"]["template"]["spec"]["containers"][0]["command"])
                    el["metadata"]["name"] = f"{provisioner}"
                    el["metadata"]["namespace"] = ns
                    labels = {
                        "app.kubernetes.io/name" : "local-path-provisioner",
                        "app.kubernetes.io/instance" : provisioner,
                    }
                    el["spec"]["selector"]["matchLabels"] = labels
                    el["spec"]["template"]["metadata"]["labels"] = labels
                    el["spec"]["template"]["spec"]["serviceAccountName"] = sa
                    el["spec"]["template"]["spec"]["containers"][0]["name"] = provisioner
                    el["spec"]["template"]["spec"]["containers"][0]["command"] = (
                        "local-path-provisioner",
                        "--debug",
                        "start",
                        "--config", "/etc/config/config.json",
                        "--service-account-name", sa,
                        "--provisioner-name", provisioner_fqn,
                        "--configmap-name", "local-path-config",
                    )
                elif el["kind"].lower() == "configmap":
                    el["metadata"]["namespace"] = ns
                    el["data"]["config.json"] = json.dumps({
                        "sharedFileSystemPath": g_ts_cfg.get_local_path_provisioner_shared_path()
                    })
                elif el["kind"].lower() == "storageclass":
                    el["metadata"]["name"] = ns
                    el["provisioner"] = provisioner_fqn
                    el["allowVolumeExpansion"] = True

            to_apply.append(el)

        y = yaml.safe_dump_all(to_apply)
        print(f"Installing local path provisioner in shared mode: {y}")

        args = [g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", "apply", "-f", "-"]
        print(" ".join(args), "<", y)
        subprocess.run(args, input=y.encode("utf8"), check=True)

        wait_local_path_provisioner(ns, provisioner)

        kutil.set_new_default_storage_class(sc_name)

    def generate_kubectl_info(self):
        script_path = os.path.join(g_ts_cfg.get_ci_dir(), "jobs/auxiliary/generate-kubectl-info.sh")
        output_path = os.path.join(g_ts_cfg.work_dir, "kubectl-info.log")
        shell_cmd = [script_path, g_ts_cfg.kubectl_path, g_ts_cfg.k8s_context, output_path]
        subprocess.check_call(shell_cmd)

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

    def resolve_context(self, cluster_name):
        return cluster_name

    def start_cluster(self, nodes, node_memory, version, cfg_path, ip_family):
        pass

    def stop_cluster(self):
        pass

    def delete_cluster(self):
        pass

    def deploy_operator(self, deploy_files, override_deployment=True):
        print("Deploying operator...")
        for f in deploy_files:
            args = [g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", "apply", "-f", "-"]
            print(" ".join(args), "<", f)
            y = open(f).read()

            if f.endswith("deploy-operator.yaml"):
                arr = list(yaml.safe_load_all(y))
                for el in arr:
                    if el["kind"] == "Namespace":
                        custom_labels = g_ts_cfg.get_custom_operator_ns_labels()
                        if len(custom_labels):
                            print(f"Patching namespace {el['metadata']['name']} with custom labels")
                            if "labels" not in el["metadata"]:
                                el["metadata"]["labels"] = {}
                            el["metadata"]["labels"].update(custom_labels)
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
"""
        if override_deployment:
            auxutil.merge_patch_object(operator, next(yaml.safe_load_all(patch)))
            y = yaml.safe_dump(operator)
            print(y)
            subprocess.run([g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", "apply", "-f", "-"],
                          input=y.encode("utf8"), check=True)

        wait_operator(ns="mysql-operator", deploy_name="mysql-operator")


    def prepare_oci_bucket(self):
        bucket = {
            "name": None
        }
        return bucket

    def cleanup_oci_bucket(self):
        pass
