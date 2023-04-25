#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to download binaries according to given list of versions and save it to specified directory
# usage: <list-of-versions-path> <src-link-pattern> <dest-binaries-dir> <binary-prefix>
# input:
# list-of-versions-path - text file path with list of versions to download
# src-link-pattern - pattern of link containing token VERSION to be substituted with the actual version, e.g.
#   https://dl.k8s.io/release/VERSION/bin/linux/amd64/kubectl
# dest-binaries-dir - destination dir to store binaries
# binary-prefix - prefix of binary, e.g. kubectl, k3d, minikube, kind, ...
# e.g.
# ./download-binaries-from-list.sh ./kubectl-versions.txt \
#       https://dl.k8s.io/release/VERSION/bin/linux/amd64/kubectl /usr/local/bin kubectl
# stores in /usr/local/bin binaries like kubectl-v1.25.6, kubectl-v1.26.1, etc.

set -vx

if [ "$#" -ne 4 ]; then
	echo "usage: <list-of-versions-path> <src-link-pattern> <dest-binaries-dir> <binary-prefix>"
	exit 1
fi

VERSIONS_LIST=$1
SRC_LINK_PATTERN=$2
DEST_DIR=$3
BINARY_PREFIX=$4

while read -r VERSION_TO_DOWNLOAD
do
    DOWNLOAD_LINK=${SRC_LINK_PATTERN/VERSION/$VERSION_TO_DOWNLOAD}
    BINARY_PATH=$DEST_DIR/${BINARY_PREFIX}-${VERSION_TO_DOWNLOAD}
    # --cond-time download only if newer
    curl -L ${DOWNLOAD_LINK} -o ${BINARY_PATH} --time-cond ${BINARY_PATH}
done < $VERSIONS_LIST
