#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to download k8s related binaries in various versions, used in our CI
# usage: <dest-binaries-dir>
# input:
# dest-binaries-dir - destination dir to store binaries
# e.g.
# ./restore-binaries.sh /usr/local/bin
# stores in specified directory binaries of kubectl, minikube, k3d, kind, etc. in various versions

set -vx

if [ "$#" -ne 1 ]; then
	echo "usage: <dest-binaries-dir>"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

DEST_DIR=$1

KUBECTL_LINK_PATTERN='https://dl.k8s.io/release/VERSION/bin/linux/amd64/kubectl'
$SCRIPT_DIR/download-binaries-from-list.sh $SCRIPT_DIR/kubectl-versions.txt $KUBECTL_LINK_PATTERN $DEST_DIR kubectl

MINIKUBE_LINK_PATTERN='https://github.com/kubernetes/minikube/releases/download/VERSION/minikube-linux-amd64'
$SCRIPT_DIR/download-binaries-from-list.sh $SCRIPT_DIR/minikube-versions.txt $MINIKUBE_LINK_PATTERN $DEST_DIR minikube

K3D_LINK_PATTERN='https://github.com/k3d-io/k3d/releases/download/VERSION/k3d-linux-amd64'
$SCRIPT_DIR/download-binaries-from-list.sh $SCRIPT_DIR/k3d-versions.txt $K3D_LINK_PATTERN $DEST_DIR k3d

KIND_LINK_PATTERN='https://github.com/kubernetes-sigs/kind/releases/download/VERSION/kind-linux-amd64'
$SCRIPT_DIR/download-binaries-from-list.sh $SCRIPT_DIR/kind-versions.txt $KIND_LINK_PATTERN $DEST_DIR kind
