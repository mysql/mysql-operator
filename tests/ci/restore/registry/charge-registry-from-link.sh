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
# archive-suffix - optional suffix to add to the filename of the archive, e.g.
#		if link is https://github.com/k3s-io/k3s/releases/download/v1.23.15%2Bk3s1/k3s-airgap-images-amd64.tar.gz and
# 		archive-suffix is v1.23.15_2Bk3s1, then the archive file will be k3s-airgap-images-amd64-v1.23.15_2Bk3s1.tar.gz,
# 		i.e. suffix is added in front of the extension
#
# e.g.
# ./charge-registry-from-link.sh https://github.com/k3s-io/k3s/releases/download/v1.23.15%2Bk3s1/k3s-airgap-images-amd64.tar.gz \
# 		registry.localhost:5000 ~/k8s-archives v1.23.15_2Bk3s1
# ./charge-registry-from-link.sh https://github.com/k3s-io/k3s/releases/download/v1.22.7%2Bk3s1/k3s-airgap-images-arm64.tar \
# 		registry.localhost:5000 ./archives v1.22.7_2Bk3s1

set -vx

if [[ "$#" -ne 2 && "$#" -ne 3 && "$#" -ne 4 ]]; then
	echo "usage: <image-archive-link> <registry-url> [archives-dir] [archive-suffix]"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

ARCHIVE_LINK=$1
REGISTRY_URL=$2
if [[ "$#" -eq 3 || "$#" -eq 4 ]]; then
	ARCHIVES_DIR=$3
else
	ARCHIVES_DIR=$(mktemp -d)
	ARCHIVES_DIR_IS_TMP=1
fi
ARCHIVE_SUFFIX=$4

ARCHIVE_FILENAME=$(basename $ARCHIVE_LINK)
if [[ -z $ARCHIVE_SUFFIX ]]; then
	ARCHIVE_PATH=$ARCHIVES_DIR/$ARCHIVE_FILENAME
else
	ARCHIVE_BASEFILENAME="${ARCHIVE_FILENAME%%.*}"
	ARCHIVE_EXTENSION="${ARCHIVE_FILENAME#*.}"
	ARCHIVE_PATH=$ARCHIVES_DIR/${ARCHIVE_BASEFILENAME}-${ARCHIVE_SUFFIX}.${ARCHIVE_EXTENSION}
fi

curl -L ${ARCHIVE_LINK} -o ${ARCHIVE_PATH} --time-cond ${ARCHIVE_PATH}

$SCRIPT_DIR/charge-registry-from-archive.sh $ARCHIVE_PATH $REGISTRY_URL

if [[ -v ARCHIVES_DIR_IS_TMP ]]; then
	rm -rfd $ARCHIVES_DIR
fi
