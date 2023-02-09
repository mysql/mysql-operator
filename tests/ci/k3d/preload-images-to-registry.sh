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
# b) kubectl get events -A | grep pulled
#	 it will list needed images, to recognize which images are missing
# 5) then update below the IMAGES list accordingly and charge the local registry
# 5a) the first images like rancher/k3d-proxy rancher/k3d-tools should be updated
#     according to the proper version (e.g. 5.3.0)
set -vx

REGISTRY=registry.localhost:5000

read -r -d '\t' IMAGES << EOM
	ghcr.io/k3d-io/k3d-proxy:5.4.4
	ghcr.io/k3d-io/k3d-proxy:5.4.6
	ghcr.io/k3d-io/k3d-proxy:5.4.7
	ghcr.io/k3d-io/k3d-tools:5.4.4
	ghcr.io/k3d-io/k3d-tools:5.4.6
	ghcr.io/k3d-io/k3d-tools:5.4.7
	rancher/coredns-coredns:1.8.3
	rancher/k3d-proxy:5.1.0
	rancher/k3d-proxy:5.3.0
	rancher/k3d-tools:5.1.0
	rancher/k3d-tools:5.3.0
	rancher/klipper-helm:v0.6.4-build20210813
	rancher/klipper-helm:v0.6.6-build20211022
	rancher/klipper-helm:v0.7.3-build20220613
	rancher/klipper-helm:v0.7.4-build20221121
	rancher/klipper-lb:v0.2.0
	rancher/klipper-lb:v0.3.4
	rancher/klipper-lb:v0.3.5
	rancher/klipper-lb:v0.4.0
	rancher/library-busybox:1.32.1
	rancher/library-traefik:2.4.8
	rancher/local-path-provisioner:v0.0.19
	rancher/local-path-provisioner:v0.0.21
	rancher/local-path-provisioner:v0.0.23
	rancher/metrics-server:v0.3.6
	rancher/mirrored-coredns-coredns:1.8.6
	rancher/mirrored-coredns-coredns:1.9.1
	rancher/mirrored-coredns-coredns:1.9.4
	rancher/mirrored-library-busybox:1.34.1
	rancher/mirrored-library-traefik:2.5.6
	rancher/mirrored-library-traefik:2.6.1
	rancher/mirrored-library-traefik:2.6.2
	rancher/mirrored-library-traefik:2.9.4
	rancher/mirrored-metrics-server:v0.5.2
	rancher/mirrored-metrics-server:v0.6.2
	rancher/mirrored-pause:3.6
	rancher/pause:3.1
EOM

HOST=ghcr.io/
for IMAGE_PULL in $IMAGES; do
	docker pull $IMAGE_PULL
	# cut a host name before pushing to a local repo
	IMAGE_PUSH=${IMAGE_PULL/$HOST/}
	docker tag $IMAGE_PULL $REGISTRY/$IMAGE_PUSH
	docker push $REGISTRY/$IMAGE_PUSH
	docker image rm $REGISTRY/$IMAGE_PUSH
done
