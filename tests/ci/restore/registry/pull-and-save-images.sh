#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to pull images according to specified list and save them to tar.gz archives
# usage: <list-of-images-path> <dest-archives-dir>
# input:
# list-of-images-path - text file path with list of images to pull and archive
# dest-archives-dir - destination dir to store archives
# e.g.
# ./pull-and-save-images.sh ./kind/node-images.txt ~/images

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

if [ "$#" -ne 2 ]; then
	echo "usage: <list-of-images-path> <dest-archives-dir>"
	exit 1
fi

IMAGES_LIST=$1
DEST_ARCHIVES_DIR=$2

while read -r IMAGE_TO_PULL
do
	if [[ -n $IMAGE_TO_PULL ]]; then
		$SCRIPT_DIR/pull-and-save-image.sh $IMAGE_TO_PULL $DEST_ARCHIVES_DIR
	fi
done < $IMAGES_LIST
