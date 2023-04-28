#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to restore CI environment
# usage: <binaries-dir> <archives-dir>
# input:
# binaries-dir - destination dir to store binaries like kubectl, minikube, k3d, kind, etc.
# archives-dir - destination dir to store image archives before charging the local registry
# e.g.
# ./restore-env.sh ~/k8s-binaries ~/k8s-archives

set -vx

if [ "$#" -ne 2 ]; then
	echo "usage: <binaries-dir> <archives-dir>"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

BINARIES_DIR=$1
ARCHIVES_DIR=$2

${SCRIPT_DIR}/binaries/restore-binaries.sh $BINARIES_DIR
${SCRIPT_DIR}/registry/restore-local-registry.sh $ARCHIVES_DIR
