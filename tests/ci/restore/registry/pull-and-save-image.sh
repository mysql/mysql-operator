#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to pull an image and save it to tar.gz archive
# usage: <image-name>
# image-name - image to pull and save e.g. rancher/k3s:v1.25.6-k3s1

set -vx

if [ "$#" -ne 1 ]; then
	echo "usage: <image-name>"
	exit 1
fi

IMAGE_NAME=$1

docker pull $IMAGE_NAME
IMAGE_ARCHIVE_NAME=$(sed -e 's/[.:\/]/_/g' <<< $IMAGE_NAME).tar.gz
docker save $IMAGE_NAME | gzip > $IMAGE_ARCHIVE_NAME
echo "image saved to $IMAGE_ARCHIVE_NAME"
