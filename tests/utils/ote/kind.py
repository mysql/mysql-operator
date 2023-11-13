# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from .base import BaseEnvironment
import os
import subprocess
from tempfile import mkstemp
from utils import kutil
from setup.config import g_ts_cfg

class KindEnvironment(BaseEnvironment):
    name = "kind"
    cluster_config_path = None
    cluster_config_path_is_tmp = False

    def __del__(self):
        if self.cluster_config_path_is_tmp:
            os.remove(self.cluster_config_path)

    def resolve_context(self, cluster_name):
        return f"{self.name}-{cluster_name}"

    def start_cluster(self, nodes, node_memory, version, cfg_path, ip_family):
        if cfg_path:
            self.cluster_config_path = cfg_path
        else:
            cfgBuilder = KindConfigBuilder()
            self.cluster_config_path = cfgBuilder.run(nodes, node_memory, version, ip_family)
            self.cluster_config_path_is_tmp = True

        args = [g_ts_cfg.env_binary_path, "create", "cluster", "--name", g_ts_cfg.k8s_cluster, "--config", self.cluster_config_path]
        if not self._cleanup:
            args.append(f"--retain")

        print(f"starting cluster: {args}")
        # we want to have a separate docker network per cluster like for k3d or minikube
        # but kind supports it only due to an experimental envar KIND_EXPERIMENTAL_DOCKER_NETWORK
        # refactor it when kind supports customization of network out of the box
        # https://github.com/kubernetes-sigs/kind/issues/273
        kind_env = os.environ.copy()
        kind_env["KIND_EXPERIMENTAL_DOCKER_NETWORK"] = g_ts_cfg.k8s_context
        subprocess.check_call(args, env=kind_env)

        # connect network of the cluster to the local image registry
        if g_ts_cfg.image_registry:
            subprocess.call(["docker", "network", "connect", g_ts_cfg.k8s_context, g_ts_cfg.image_registry_host])

        cfgmap = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: local-registry-hosting
  namespace: kube-public
data:
  localRegistryHosting.v1: |
    host: "{g_ts_cfg.image_registry_host}:{g_ts_cfg.image_registry_port}"
    help: "https://kind.sigs.k8s.io/docs/user/local-registry/"
"""
        kutil.apply("kube-public", cfgmap)

    def stop_cluster(self):
        # kind doesn't support stop command yet
        if g_ts_cfg.image_registry:
            args = ["docker", "network", "disconnect", g_ts_cfg.k8s_context, g_ts_cfg.image_registry_host]
            subprocess.call(args)

    def delete_cluster(self):
        args = [g_ts_cfg.env_binary_path, "delete", "cluster", "--name", g_ts_cfg.k8s_cluster]
        subprocess.check_call(args)


class KindConfigBuilder:
    def __init__(self):
        self.contents = ""

    def write(self, text):
        self.contents += text

    def writeln(self, text):
        self.write(text + '\n')

    def run(self, nodes, node_memory, version, ip_family):
        self.generate_header()
        self.generate_local_registry()
        self.generate_networking(ip_family)
        self.generate_nodes(nodes, node_memory, version, ip_family)
        fd, path = mkstemp(prefix="kind-registry", suffix=".yaml")
        with os.fdopen(fd, 'w') as f:
            f.write(self.contents)
            print(f"kind config file: {path}")
            print(self.contents)
        return path

    def generate_header(self):
        header = """kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
"""
        self.write(header)

    def generate_local_registry(self):
        # https://kind.sigs.k8s.io/docs/user/local-registry/
        local_registry = f"""containerdConfigPatches:
- |-
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."{g_ts_cfg.image_registry_host}:{g_ts_cfg.image_registry_port}"]
    endpoint = ["http://{g_ts_cfg.image_registry_host}:{g_ts_cfg.image_registry_port}"]
    insecure_skip_verify = true
- |-
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."docker.io"]
    endpoint = ["http://{g_ts_cfg.image_registry_host}:{g_ts_cfg.image_registry_port}"]
    insecure_skip_verify = true
- |-
  [plugins."io.containerd.grpc.v1.cri".registry.mirrors."ghcr.io"]
    endpoint = ["http://{g_ts_cfg.image_registry_host}:{g_ts_cfg.image_registry_port}"]
    insecure_skip_verify = true
"""
        self.write(local_registry)

    def generate_networking(self, ip_family):
        if ip_family:
            networking = f"""networking:
    ipFamily: {ip_family}
"""
            self.write(networking)

    def generate_node(self, role, version, config_kind, node_memory):
        node = f"- role: {role}\n"

        if version:
            # https://github.com/kubernetes-sigs/kind/releases
            node += f"image: {version}\n"

        if node_memory:
            node += f"""  kubeadmConfigPatches:
  - |
    kind: {config_kind}
    nodeRegistration:
      kubeletExtraArgs:
        system-reserved: memory={node_memory}Mi
"""

        self.write(node)

    def generate_nodes(self, nodes, node_memory, version, ip_family):
        self.writeln("nodes:")
        self.generate_node("control-plane", version, "InitConfiguration", node_memory)
        for _ in range(nodes if nodes else 1):
            self.generate_node("worker", version, "JoinConfiguration", node_memory)
