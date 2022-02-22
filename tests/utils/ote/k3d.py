# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from .base import BaseEnvironment
import os
from string import Template
import subprocess
from tempfile import mkstemp
from setup.config import g_ts_cfg


class K3dEnvironment(BaseEnvironment):
    name = "k3d"

    def load_images(self, images):
        loaded = []
        for img, is_latest in images:
            md = open(img+".txt")
            image_id = md.readline().strip()
            image_repo_tag = md.readline().strip()
            self.load_image(image_repo_tag, image_id)

    def load_image(self, repo_tag, id):
        print(f"Loading image {repo_tag} ({id})")
        cmd = f"k3d image import {repo_tag} -c {g_ts_cfg.k8s_cluster}"
        print(cmd)
        subprocess.check_call(cmd, shell=True)

    def get_context(self, cluster_name):
        return f"k3d-{cluster_name}"

    def start_cluster(self, nodes, version, registry_cfg_path):
        assert version is None

        args = ["k3d", "cluster", "create", g_ts_cfg.k8s_cluster, "--timeout", "5m"]
        if g_ts_cfg.image_registry:
            if not registry_cfg_path:
                registry_cfg_path = self.prepare_registry_cfg()
            args.extend(["--registry-config", registry_cfg_path])

        if self.operator_mount_path:
            args += ["--volume", f"{self.operator_mount_path}:{self.operator_host_path}"]
        if self._mounts:
            for mount in self._mounts:
                args += ["--volume", mount]

        args += self.add_proxy_env("HTTP_PROXY")
        args += self.add_proxy_env("HTTPS_PROXY")
        args += self.add_proxy_env("NO_PROXY")

        subprocess.check_call(args)

        # connect network of the cluster to the local image registry
        if g_ts_cfg.image_registry:
            subprocess.call(["docker", "network", "connect", g_ts_cfg.k8s_context, g_ts_cfg.image_registry_host])

    def add_proxy_env(self, envar):
        if envar in os.environ:
            return ["--env", f'{envar}={os.getenv(envar)}@', "--env", f'{envar.lower()}={os.getenv(envar)}@']
        return []

    def stop_cluster(self):
        args = ["k3d", "cluster", "stop", g_ts_cfg.k8s_cluster]
        subprocess.check_call(args)

    def delete_cluster(self):
        args = ["k3d", "cluster", "delete", g_ts_cfg.k8s_cluster]
        subprocess.check_call(args)

    def prepare_registry_cfg(self):
        cfg_template = f"""mirrors:
  "docker.io":
    endpoint:
      - http://$registry
  "$registry":
    endpoint:
      - http://$registry
"""
        data = {
            'registry': g_ts_cfg.image_registry
        }

        tmpl = Template(cfg_template)
        contents = tmpl.substitute(data)
        fd, path = mkstemp(prefix="k3d-registry", suffix=".yaml")
        with os.fdopen(fd, 'w') as f:
            f.write(contents)
        return path
