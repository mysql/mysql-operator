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

from .base import BaseEnvironment
import subprocess
import os


class MinikubeEnvironment(BaseEnvironment):
    name = "Minikube"

    def load_images(self, images):
        loaded = []
        for img, is_latest in images:
            md = open(img+".txt")
            image_id = md.readline().strip()
            name = md.readline().strip()
            if self.image_exists(image_id):
                print(f"{img} ({image_id}) already exists")
            else:
                loaded.append((img, name, image_id))
                self.load_image(img, name)
            # TODO tag :latest
        print("Reloading cache...")
        subprocess.run("minikube cache reload", shell=True)

        # Sometimes the 1st load doesn't work for whatever reason
        for img, name, image_id in loaded:
            while not self.image_exists(image_id):
                print(f"Reloading {img} ({image_id})...")
                self.load_image(img, name)

        subprocess.run("minikube cache reload", shell=True)

    def image_exists(self, image_id, node="minikube"):
        cmd = f"minikube ssh -n{node} docker image ls"
        p = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE)
        for line in p.stdout.decode("utf8").strip().split("\n"):
            name, version, image = line.split()[:3]
            if image == image_id:
                return True
        return False

    def load_image(self, image, name):
        print(f"Caching image {name} ({image})")

        # make sure image is in local docker env
        subprocess.run(f"docker image import {image}", shell=True)

        # delete the old image from minikube
        subprocess.run(
            f"minikube ssh 'docker image rm {name} || true'", shell=True)

        # load from local docker env into minikube
        subprocess.run(
            f"echo 'deleting {name}...'; minikube cache delete {name} ; echo 'adding {name}...'; minikube cache add {name}; echo done", shell=True)
        print()

    def start_cluster(self, nodes, version):
        args = ["minikube", "start", f"--nodes={nodes}"]
        if version:
            args.append(f"--kubernetes-version={version}")
        subprocess.check_call(args)

    def stop_cluster(self):
        args = ["minikube", "stop"]
        subprocess.check_call(args)

    def delete_cluster(self):
        args = ["minikube", "delete"]
        subprocess.check_call(args)
