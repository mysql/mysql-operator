# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import auxutil
from .base import BaseEnvironment
from datetime import datetime
from setup.config import g_ts_cfg
import subprocess
import os
import sys


class MinikubeEnvironment(BaseEnvironment):
    name = "Minikube"

    def load_images(self, images):
        self.list_images()
        for img_info_path, is_latest in images:
            md = open(img_info_path + ".txt")
            image_id = md.readline().strip()
            image_name = md.readline().strip()
            if self.image_exists_by_id(image_name, image_id):
                print(f"{image_name} ({image_id}) already exists")
            else:
                self.load_image(image_name, image_id)
                if not self.image_exists_by_id(image_name, image_id):
                    print(f"Cannot load image {image_name} ({image_id})")
                    self.list_images()
                    sys.exit(1)
        self.list_images()

    def image_exists_by_id(self, image_name, image_id, node="minikube"):
        for line in self.get_images(image_name, node):
            name, version, image = line.split()[:3]
            if image == image_id:
                return True
        return False

    def image_exists_by_name(self, image_name, node="minikube"):
        for line in self.get_images(image_name, node):
            name, version, image = line.split()[:3]
            if name == image_name:
                return True
        return False

    def list_images(self):
        for line in self.get_images():
            name, version, image = line.split()[:3]
            print(f"{name}/{version}:{image}")

    def get_images(self, filter="", node="minikube"):
        cmd = [g_ts_cfg.env_binary_path, f"--profile={g_ts_cfg.k8s_context}", "ssh", f"-n{node}", "docker", "image", "ls", filter]
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
        return p.stdout.decode("utf8").strip().split("\n")

    def load_image(self, image_name, image_id, node="minikube"):
        print(f"Loading image {image_name} ({image_id})")

        if self.image_exists_by_name(image_name, node):
            # delete the old image from minikube
            self.run_command(f"{g_ts_cfg.env_binary_path} --profile={g_ts_cfg.k8s_context} ssh -n{node} docker image rm {image_name}")
            self.run_command(f"{g_ts_cfg.env_binary_path} --profile={g_ts_cfg.k8s_context} cache delete {image_name}")

        # load from local docker env into minikube
        # we've noticed that 'minikube image load' may work weirdly when another image with
        # the same name was already loaded in the past, i.e. it may not be updated at all
        # the workaround: in docker tag with a fancy name the image we want to load
        # load it to minikube, tag it with the proper name
        # remove the fancy image from docker and minikube
        timestamp = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")
        tmp_image_name = f"{image_name}-{timestamp}"
        self.run_command(f"docker tag {image_name} {tmp_image_name}")
        self.run_command(f"{g_ts_cfg.env_binary_path} --profile={g_ts_cfg.k8s_context} image load {tmp_image_name}")
        self.run_command(f"{g_ts_cfg.env_binary_path} --profile={g_ts_cfg.k8s_context} ssh docker image tag {tmp_image_name} {image_name}")
        self.run_command(f"{g_ts_cfg.env_binary_path} --profile={g_ts_cfg.k8s_context} ssh docker rmi {tmp_image_name}")
        self.run_command(f"docker rmi {tmp_image_name}")

    def run_command(self, command, verbose = True):
        if verbose:
            print(command)
        subprocess.run(command, shell=True)
        if verbose:
            print('done')

    def start_cluster(self, nodes, node_memory, version, cfg_path, ip_family):
        assert cfg_path is None
        args = [g_ts_cfg.env_binary_path, "start", f"--profile={g_ts_cfg.k8s_cluster}"]
        opts = os.getenv("TEST_MINIKUBE_OPTIONS")
        if opts:
            args += opts.split(" ")

        if nodes and nodes > 1:
            args.append(f"--nodes={nodes}")

        if node_memory:
            args.append(f"--memory={node_memory}m")

        if version:
            args.append(f"--kubernetes-version={version}")

        if self.operator_mount_path:
            args += ["--mount", f"--mount-string={self.operator_mount_path}:{self.operator_host_path}"]
        if self._mounts:
            for mount in self._mounts:
                args += ["--mount", f"--mount-string={mount}"]

        if g_ts_cfg.image_registry:
            args.append(f"--insecure-registry={self.resolve_registry()}")
        print(f"starting cluster: {args}")
        subprocess.check_call(args)

    def stop_cluster(self):
        args = [g_ts_cfg.env_binary_path, f"--profile={g_ts_cfg.k8s_context}", "stop"]
        subprocess.check_call(args)

    def delete_cluster(self):
        args = [g_ts_cfg.env_binary_path, f"--profile={g_ts_cfg.k8s_context}", "delete"]
        subprocess.check_call(args)

    def resolve_registry(self):
        # check if registry host is localhost at the bottom
        if not g_ts_cfg.image_registry_is_loopback:
            return g_ts_cfg.image_registry

        # localhost registry will not work inside minikube as it runs in its own vm
        # so resolve the host IP and override it in registry Url, it will work when
        # called from inside of minikube (but not from the host)

        # example, assume the following configuration
        # - in /etc/hosts there was added host registry.localhost
        #     127.0.0.1 registry.localhost
        # - create local registry registry.localhost:5000 (added registry.localhost in /etc/hosts)
        # - get host ip, it may be e.g. 10.0.2.15
        # - minikube start --insecure-registry=10.0.2.15:5000
        # - minikube ssh && docker pull 10.0.2.15:5000/mysql/community-operator:8.0.25, should work
        # - btw on the host the above pull cmd will not work if 10.0.2.15 is not added as insecure
        #     registry, but it doesn't matter, because we will need it only inside minikube

        # some more details also in the following article:
        # https://hasura.io/blog/sharing-a-local-registry-for-minikube-37c7240d0615/
        host_ip = auxutil.resolve_host_ip()
        g_ts_cfg.image_registry = g_ts_cfg.image_registry.replace(g_ts_cfg.image_registry_host, host_ip)
        return g_ts_cfg.image_registry
