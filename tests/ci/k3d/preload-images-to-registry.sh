#!/bin/bash
# Copyright (c) 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

REGISTRY=registry.localhost:5000
IMAGES="rancher/pause:3.1 rancher/coredns-coredns:1.8.0 rancher/metrics-server:v0.3.6 rancher/klipper-helm:v0.4.3 rancher/local-path-provisioner:v0.0.19 rancher/library-traefik:1.7.19 rancher/klipper-lb:v0.1.2"
for IMAGE in $IMAGES; do
	docker pull $IMAGE
	docker tag $IMAGE $REGISTRY/$IMAGE
	docker push $REGISTRY/$IMAGE
done
