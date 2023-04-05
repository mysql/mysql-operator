#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry with images matching given pattern
# usage: <image-pattern> <registry-url>
# image-pattern - pattern matching images to charge e.g. ranches/k3s or 'ghcr.io/k3d-io/*'
# registry-url - url of a registry to be charged
# e.g.
# rancher/k3s registry.localhost:5000
# 'ghcr.io/k3d-io/*' registry.localhost:5000

set -vx

if [ "$#" -ne 2 ]; then
	echo "usage: <image-pattern> <registry-url>"
	exit 1
fi

IMAGE_PATTERN=$1
REGISTRY_URL=$2

IMAGES=$(docker images --filter=reference=$IMAGE_PATTERN --format "{{.Repository}}:{{.Tag}}")

for IMAGE in $IMAGES; do
	IMAGE_IN_REGISTRY=$REGISTRY_URL/$IMAGE
	docker tag $IMAGE $IMAGE_IN_REGISTRY
	docker push $IMAGE_IN_REGISTRY
	docker image rm $IMAGE_IN_REGISTRY
done
