#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to pull all needed images available on dockerhub only and save them to tar.gz archives,
# so they can be copied anywhere
# over time, these images may be available in other repos, then use charge-registry-from-links.sh to
# operate on them directly
# usage: <dest-archives-dir>
# input:
# dest-archives-dir - destination dir to store archives
# e.g.
# ./pull-and-save-dockerhub-images.sh ~/k8s-archives

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

if [ "$#" -ne 1 ]; then
	echo "usage: <dest-archives-dir>"
	exit 1
fi

DEST_ARCHIVES_DIR=$1

$SCRIPT_DIR/pull-and-save-images.sh $SCRIPT_DIR/k3d/node-images.txt $DEST_ARCHIVES_DIR
$SCRIPT_DIR/pull-and-save-images.sh $SCRIPT_DIR/kind/node-images.txt $DEST_ARCHIVES_DIR
$SCRIPT_DIR/pull-and-save-images.sh $SCRIPT_DIR/other/dockerhub-images.txt $DEST_ARCHIVES_DIR
