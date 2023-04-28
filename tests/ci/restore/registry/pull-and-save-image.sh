#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to pull an image and save it to tar.gz archive
# usage: <image-name> [dest-dir]
# image-name - image to pull and save e.g. rancher/k3s:v1.25.6-k3s1
# dest-dir - optional directory to store the archive
# e.g.
# ./pull-and-save-image.sh rancher/k3s:v1.26.1-k3s1 ~/images

set -vx

if [[ "$#" -ne 1 && "$#" -ne 2 ]]; then
	echo "usage: <image-name> [dest-dir]"
	exit 1
fi

IMAGE_NAME=$1

if [ "$#" -eq 2 ]; then
	DEST_DIR=$2
else
	DEST_DIR=.
fi

docker pull $IMAGE_NAME
IMAGE_ARCHIVE_NAME=$(sed -e 's/[.:\@/]/_/g' <<< $IMAGE_NAME).tar.gz
IMAGE_ARCHIVE_PATH=$DEST_DIR/$IMAGE_ARCHIVE_NAME
docker save $IMAGE_NAME | gzip > $IMAGE_ARCHIVE_PATH
echo "image $IMAGE_NAME saved to $IMAGE_ARCHIVE_PATH"
