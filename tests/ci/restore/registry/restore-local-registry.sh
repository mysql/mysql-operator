#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# ensure the local registry is running and charge it with all needed images
# usage: <archives-dir>
# archives-dir - a directory where downloaded archives are stored
# ./restore-local-registry.sh ~/k8s-archives

set -vx

if [ "$#" -ne 1 ]; then
	echo "usage: <archives-dir>"
	exit 1
fi

ARCHIVES_DIR=$1

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

# ensure the local registry is running, and charged with common images
$CI_DIR/registry/ensure-local-registry-running.sh

RESTORE_REGISTRY_DIR=$CI_DIR/restore/registry

# charge with k3d images and dependencies
$RESTORE_REGISTRY_DIR/charge-registry-with-pattern.sh rancher/k3s $LOCAL_REGISTRY_ADDRESS
$RESTORE_REGISTRY_DIR/charge-registry-with-pattern.sh 'ghcr.io/k3d-io/*' $LOCAL_REGISTRY_ADDRESS
$RESTORE_REGISTRY_DIR/k3d/preload-images-to-registry.sh

K3S_IMAGES_LINK_PATTERN='https://github.com/k3s-io/k3s/releases/download/VERSION/k3s-airgap-images-amd64.tar.gz'
$RESTORE_REGISTRY_DIR/charge-registry-from-links.sh $RESTORE_REGISTRY_DIR/k3d/k3s-airgap-images.txt \
	$K3S_IMAGES_LINK_PATTERN $ARCHIVES_DIR $LOCAL_REGISTRY_ADDRESS 1

$RESTORE_REGISTRY_DIR/charge-registry-from-archives.sh $RESTORE_REGISTRY_DIR/k3d/node-images.txt \
	$ARCHIVES_DIR $LOCAL_REGISTRY_ADDRESS
$RESTORE_REGISTRY_DIR/charge-registry-from-archives.sh $RESTORE_REGISTRY_DIR/kind/node-images.txt \
	$ARCHIVES_DIR $LOCAL_REGISTRY_ADDRESS
$RESTORE_REGISTRY_DIR/charge-registry-from-archives.sh $RESTORE_REGISTRY_DIR/other/dockerhub-images.txt \
	$ARCHIVES_DIR $LOCAL_REGISTRY_ADDRESS

$RESTORE_REGISTRY_DIR/pull-images-and-charge-registry.sh $RESTORE_REGISTRY_DIR/other/other-images.txt \
	$LOCAL_REGISTRY_ADDRESS
