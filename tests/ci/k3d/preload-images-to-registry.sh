#!/bin/bash
# Copyright (c) 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# after upgrade of k3d version, run on your local machine the following steps:
# 0) create an appropriate local registry config, e.g.
# <registries.yaml>
# mirrors:
#   registry.localhost:5000:
#     endpoint:
#       - http://registry.localhost:5000
# </registries.yaml>
# the above file is stored in src/tests/ci/k3d/registries.yaml
# 1) k3d cluster delete
# 2) k3d cluster create --registry-config=./registries.yaml
# 3) docker network connect k3d-k3s-default registry.localhost
# 4) observe until containers are running (or not):
# a) kubectl get pods -Aw
# b) kubectl get events -A | grep pulled (will list needed images)
# to recognize which images are missing
# 5) then update below the IMAGES list accordingly and charge the local registry

REGISTRY=registry.localhost:5000

IMAGES="rancher/pause:3.1 rancher/mirrored-coredns-coredns:1.8.6 rancher/mirrored-metrics-server:v0.5.2 rancher/klipper-helm:v0.6.6-build20211022 rancher/local-path-provisioner:v0.0.21 rancher/mirrored-library-traefik:2.5.6 rancher/klipper-lb:v0.3.4 rancher/library-busybox:1.32.1"
for IMAGE in $IMAGES; do
	docker pull $IMAGE
	docker tag $IMAGE $REGISTRY/$IMAGE
	docker push $REGISTRY/$IMAGE
	docker image rm $REGISTRY/$IMAGE
done
