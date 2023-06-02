#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry with docker saved tar archives (also compressed), according to
# specified list of images
# usage: <list-of-images-path> <archives-dir> <registry-url>
# input:
# list-of-images-path - path of a text file with list of images
# archives-dir - directory with images archives
# registry-url - url of a registry to be charged
# e.g.
# ./charge-registry-from-archives.sh ./k3d/node-images.txt ./image-archives/ registry.localhost:5000

set -vx

if [ "$#" -ne 3 ]; then
	echo "usage: <list-of-images-path> <archives-dir> <registry-url>"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

IMAGES_LIST=$1
ARCHIVES_DIR=$2
REGISTRY_URL=$3

while read -r IMAGE_NAME
do
	if [[ -z $IMAGE_NAME ]]; then
		continue
	fi
	IMAGE_ARCHIVE_NAME=$(sed -e 's/[.:\@/]/_/g' <<< $IMAGE_NAME).tar.gz
	IMAGE_ARCHIVE_PATH=$ARCHIVES_DIR/$IMAGE_ARCHIVE_NAME
	$SCRIPT_DIR/charge-registry-from-archive.sh $IMAGE_ARCHIVE_PATH $REGISTRY_URL $IMAGE_NAME
done < $IMAGES_LIST
