#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to pull images according to specified list and charge with them the specified registry
# usage: <list-of-images-path> <registry-url>
# input:
# list-of-images-path - text file path with list of images to pull
# registry-url - url of a registry to be charged
# e.g.
# ./pull-images-and-charge-registry.sh ./other/other-images.txt registry.localhost:5000

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

if [ "$#" -ne 2 ]; then
	echo "usage: <list-of-images-path> <registry-url>"
	exit 1
fi

IMAGES_LIST=$1
REGISTRY_URL=$2

while read -r IMAGE_TO_PULL
do
	if [[ -n $IMAGE_TO_PULL ]]; then
		$SCRIPT_DIR/pull-image-and-charge-registry.sh $IMAGE_TO_PULL $REGISTRY_URL
	fi
done < $IMAGES_LIST
