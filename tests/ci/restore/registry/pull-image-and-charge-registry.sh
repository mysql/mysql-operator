#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to pull an image and charge the local registry
# usage: <image-name> <registry-url>
# image-name - image to pull e.g. mcr.microsoft.com/azure-storage/azurite
# registry-url - url of a registry to be charged
# e.g.
# ./pull-image-and-charge-registry.sh mcr.microsoft.com/azure-cli registry.localhost:5000

set -vx

if [[ "$#" -ne 2 ]]; then
	echo "usage: <image-name> <registry-url>"
	exit 1
fi

IMAGE_NAME=$1
REGISTRY_URL=$2

docker pull $IMAGE_NAME
IMAGE_IN_REGISTRY=$REGISTRY_URL/$IMAGE_NAME
docker tag $IMAGE_NAME $IMAGE_IN_REGISTRY
docker push $IMAGE_IN_REGISTRY
docker image rm $IMAGE_IN_REGISTRY
