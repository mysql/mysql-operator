#!/bin/bash
# Copyright (c) 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# after upgrade of k3d version
# 0) create a local registry config, e.g.
# <registries.yaml>
# mirrors:
#   "docker.io":
#     endpoint:
#       - "http://registry.localhost:5000"
#   "registry.localhost:5000":
#     endpoint:
#       - "http://registry.localhost:5000"
# </registries.yaml>
# 1) k3d cluster delete
# 2) k3d cluster create --registry-config=./registries.yaml
# 3) docker network connect k3d-k3s-default registry.localhost
# 4) observe:
# a) kubectl get pods -Aw
# b) kubectl get events -Aw
# to recognize which images are missing
# 5) then update below the IMAGES list accordingly and charge the local registry

REGISTRY=registry.localhost:5000
IMAGES="rancher/pause:3.1 rancher/coredns-coredns:1.8.3 rancher/metrics-server:v0.3.6 rancher/klipper-helm:v0.6.1-build20210616 rancher/local-path-provisioner:v0.0.19 rancher/library-traefik:2.4.8 rancher/klipper-lb:v0.2.0 rancher/library-busybox:1.32.1"
for IMAGE in $IMAGES; do
	docker pull $IMAGE
	docker tag $IMAGE $REGISTRY/$IMAGE
	docker push $REGISTRY/$IMAGE
	docker image rm $REGISTRY/$IMAGE
done
