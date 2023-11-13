# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
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
    config_path = None
    config_path_is_tmp = False

    def __del__(self):
        if self.config_path_is_tmp:
            os.remove(self.config_path)

    def load_images(self, images):
        loaded = []
        for img, is_latest in images:
            md = open(img+".txt")
            image_id = md.readline().strip()
            image_repo_tag = md.readline().strip()
            self.load_image(image_repo_tag, image_id)

    def load_image(self, repo_tag, id):
        print(f"Loading image {repo_tag} ({id})")
        cmd = [g_ts_cfg.env_binary_path, "image", "import", repo_tag, "-c", g_ts_cfg.k8s_cluster]
        subprocess.check_call(cmd, shell=True)

    def resolve_context(self, cluster_name):
        return f"k3d-{cluster_name}"

    def start_cluster(self, nodes, node_memory, version, cfg_path, ip_family):
        args = [g_ts_cfg.env_binary_path, "cluster", "create", g_ts_cfg.k8s_cluster, "--timeout", "5m", "--no-lb"]

        if nodes and nodes > 1:
            # agents are additional nodes, by default there is single server node (see also k3d option
            # --servers for more details)
            args.append(f"--agents={nodes - 1}")

        if node_memory:
            args.append(f"--servers-memory={node_memory}m")
            args.append(f"--agents-memory={node_memory}m")

        if version:
            args.append(f"--image={version}")

        if cfg_path:
            self.config_path = cfg_path
        elif g_ts_cfg.image_registry:
            self.config_path = self.prepare_registry_cfg()
            self.config_path_is_tmp = True

        if self.config_path:
            args.extend(["--registry-config", self.config_path])

        if self.operator_mount_path:
            args += ["--volume", f"{self.operator_mount_path}:{self.operator_host_path}"]
        if self._mounts:
            for mount in self._mounts:
                args += ["--volume", mount]

        args += self.add_proxy_env("HTTP_PROXY")
        args += self.add_proxy_env("HTTPS_PROXY")
        args += self.add_proxy_env("NO_PROXY")

        print(f"starting cluster: {args}")
        subprocess.check_call(args)

        # connect network of the cluster to the local image registry
        if g_ts_cfg.image_registry:
            subprocess.call(["docker", "network", "connect", g_ts_cfg.k8s_context, g_ts_cfg.image_registry_host])

    def add_proxy_env(self, envar):
        if envar in os.environ:
            return ["--env", f'{envar}={os.getenv(envar)}@', "--env", f'{envar.lower()}={os.getenv(envar)}@']
        return []

    def stop_cluster(self):
        args = [g_ts_cfg.env_binary_path, "cluster", "stop", g_ts_cfg.k8s_cluster]
        subprocess.check_call(args)

        if g_ts_cfg.image_registry:
            args = ["docker", "network", "disconnect", g_ts_cfg.k8s_context, g_ts_cfg.image_registry_host]
            subprocess.call(args)

    def delete_cluster(self):
        args = [g_ts_cfg.env_binary_path, "cluster", "delete", g_ts_cfg.k8s_cluster]
        subprocess.check_call(args)

        if g_ts_cfg.image_registry:
            args = ["docker", "network", "rm", g_ts_cfg.k8s_context]
            subprocess.call(args)


    def prepare_registry_cfg(self):
        cfg_template = f"""mirrors:
  "docker.io":
    endpoint:
      - http://$registry
  "ghcr.io":
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
            print(f"k3d registry file: {path}")
            print(contents)
        return path
