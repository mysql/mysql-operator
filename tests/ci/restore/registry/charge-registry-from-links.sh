#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry according to specified list of versions of image archives (also compressed)
# usage: <list-of-versions-path> <src-link-pattern> <archives-dir> <registry-url> [add-suffix]
# list-of-versions-path - text file path with list of image versions
# src-link-pattern - pattern of link containing token VERSION to be substituted with the actual version, e.g.
#   https://github.com/k3s-io/k3s/releases/download/VERSION/k3s-airgap-images-amd64.tar.gz
# archives-dir - directory with images archives, archives will be downloaded only if newer
# registry-url - url of a registry to be charged
# add-suffix - optional flag, add the version as a suffix of an archive file name; useful in case the archive file
# 		name doesn't contain any distinguishing information like
# 		https://github.com/k3s-io/k3s/releases/download/v1.26.0%2Bk3s1/k3s-airgap-images-amd64.tar.gz
# 		where the version is mentioned in the link but not in its last element
# e.g.
# ./charge-registry-from-links.sh ./k3d/k3s-airgap-images.txt \
#       https://github.com/k3s-io/k3s/releases/download/VERSION/k3s-airgap-images-amd64.tar.gz ~/k8s-archives 1

set -vx

if [[ "$#" -ne 4 && "$#" -ne 5 ]]; then
	echo "usage: <list-of-versions-path> <src-link-pattern> <archives-dir> <registry-url> [add-suffix]"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

VERSIONS_LIST=$1
SRC_LINK_PATTERN=$2
ARCHIVES_DIR=$3
REGISTRY_URL=$4
ADD_SUFFIX=$5

while read -r VERSION_TO_DOWNLOAD
do
	ARCHIVE_LINK=${SRC_LINK_PATTERN/VERSION/$VERSION_TO_DOWNLOAD}
	if [[ -n $ADD_SUFFIX ]]; then
		ARCHIVE_SUFFIX=$(sed -e 's/[.:\@%/]/_/g' <<< $VERSION_TO_DOWNLOAD)
	fi
	$SCRIPT_DIR/charge-registry-from-link.sh $ARCHIVE_LINK $REGISTRY_URL $ARCHIVES_DIR $ARCHIVE_SUFFIX
done < $VERSIONS_LIST
