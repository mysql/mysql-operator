#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry from an image archive (also compressed) under a given link
# usage: <image-archive-link> <registry-url> [archives-dir]
# image-archive-link - link to an image archive
# registry-url - url of a registry to be charged
# archives-dir - optional directory where archive is to be stored, if it contains the archive to be downloaded, then
# 		the timestamp will be checked, and file will be downloaded only if newer
# 		by default it is a temporary directory
# e.g.
# ./charge-registry-from-link.sh https://github.com/k3s-io/k3s/releases/download/v1.23.15%2Bk3s1/k3s-airgap-images-amd64.tar.gz \
# 		registry.localhost:5000 ~/k8s-archives
# ./charge-registry-from-link.sh https://github.com/k3s-io/k3s/releases/download/v1.22.7%2Bk3s1/k3s-airgap-images-arm64.tar \
# 		registry.localhost:5000 ./archives

set -vx

if [[ "$#" -ne 2 && "$#" -ne 3 ]]; then
	echo "usage: <image-archive-link> <registry-url> [archives-dir]"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

ARCHIVE_LINK=$1
REGISTRY_URL=$2
if [ "$#" -eq 3 ]; then
	ARCHIVES_DIR=$3
else
	ARCHIVES_DIR=$(mktemp -d)
fi

ARCHIVE_FILENAME=$(basename $ARCHIVE_LINK)
ARCHIVE_PATH=$ARCHIVES_DIR/$ARCHIVE_FILENAME
curl -L ${ARCHIVE_LINK} -o ${ARCHIVE_PATH} --time-cond ${ARCHIVE_PATH}

$SCRIPT_DIR/charge-registry-from-archive.sh $ARCHIVE_PATH $REGISTRY_URL
