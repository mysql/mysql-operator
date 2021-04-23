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
import os
import subprocess


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
        cmd = f"k3d image import {repo_tag} -c k3s-default"
        print(cmd)
        subprocess.check_call(cmd, shell=True)

    def start_cluster(self, nodes, version):
        assert version is None

        #regpath = os.path.join(os.path.dirname(__file__), "k3d-registries.yaml")
        # , "--volume", regpath+":/etc/rancher/k3s/registries.yaml"]
        args = ["k3d", "cluster", "create", "k3s-default"]
        subprocess.check_call(args)

        # connect network of the cluster to the local image registry
        #subprocess.call(["docker", "network", "connect", "k3d-k3s-cluster", "registry.localhost"])

    def stop_cluster(self):
        args = ["k3d", "cluster", "stop", "k3s-default"]
        subprocess.check_call(args)

    def delete_cluster(self):
        args = ["k3d", "cluster", "delete", "k3s-default"]
        subprocess.check_call(args)
