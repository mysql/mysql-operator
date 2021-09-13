# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from .base import BaseEnvironment
from datetime import datetime
import subprocess
import os
import sys


class MinikubeEnvironment(BaseEnvironment):
    name = "Minikube"
    _mount_operator_path = None

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
        cmd = f"minikube ssh -n{node} docker image ls {filter}"
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
        return p.stdout.decode("utf8").strip().split("\n")

    def load_image(self, image_name, image_id, node="minikube"):
        print(f"Loading image {image_name} ({image_id})")

        if self.image_exists_by_name(image_name, node):
            # delete the old image from minikube
            self.run_command(f"minikube ssh -n{node} docker image rm {image_name}")
            self.run_command(f"minikube cache delete {image_name}")

        # load from local docker env into minikube
        # we've noticed that 'minikube image load' may work weirdly when another image with
        # the same name was already loaded in the past, i.e. it may not be updated at all
        # the workaround: in docker tag with a fancy name the image we want to load
        # load it to minikube, tag it with the proper name
        # remove the fancy image from docker and minikube
        timestamp = datetime.now().strftime("%Y.%m.%d-%H.%M.%S")
        tmp_image_name = f"{image_name}-{timestamp}"
        self.run_command(f"docker tag {image_name} {tmp_image_name}")
        self.run_command(f"minikube image load {tmp_image_name}")
        self.run_command(f"minikube ssh docker image tag {tmp_image_name} {image_name}")
        self.run_command(f"minikube ssh docker rmi {tmp_image_name}")
        self.run_command(f"docker rmi {tmp_image_name}")

    def run_command(self, command, verbose = True):
        if verbose:
            print(command)
        subprocess.run(command, shell=True)
        if verbose:
            print('done')

    def mount_operator_path(self, path):
        self.operator_host_path = os.path.join("/tmp", os.path.basename(path))
        self._mount_operator_path = path

    def start_cluster(self, nodes, version):
        args = ["minikube", "start", f"--nodes={nodes}"]
        if version:
            args.append(f"--kubernetes-version={version}")
        if self._mount_operator_path:
            args += ["--mount", f"--mount-string={self._mount_operator_path}:{self.operator_host_path}"]
        subprocess.check_call(args)

    def stop_cluster(self):
        args = ["minikube", "stop"]
        subprocess.check_call(args)

    def delete_cluster(self):
        args = ["minikube", "delete"]
        subprocess.check_call(args)
