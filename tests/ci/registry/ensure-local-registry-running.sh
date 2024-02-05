#!/bin/bash
# Copyright (c) 2023, 2024 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# ensure the local registry is running, and charged with common images
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

$CI_DIR/registry/run-local-registry.sh $LOCAL_REGISTRY_CONTAINER_NAME $LOCAL_REGISTRY_HOST_PORT $LOCAL_REGISTRY_CONTAINER_PORT

IMAGES_LIST=$CI_DIR/registry/images-list.txt

# charge the local registry
if [[ $OPERATOR_ALLOW_WEEKLY_IMAGES == 'true' ]]; then
	# temporarily allow pulling not-so-stable router and server images from the weekly repository, but we need
	# another policy for incremental images update (when they are good enough to not fail our test suite)
	$CI_DIR/registry/charge-local-registry.sh $REMOTE_REGISTRY_ADDRESS $WEEKLY_REPOSITORY_NAME \
		$LOCAL_REGISTRY_ADDRESS $LOCAL_REPOSITORY_NAME $IMAGES_LIST
fi

$CI_DIR/registry/charge-local-registry.sh $REMOTE_REGISTRY_ADDRESS $REMOTE_REPOSITORY_NAME \
	$LOCAL_REGISTRY_ADDRESS $LOCAL_REPOSITORY_NAME $IMAGES_LIST

