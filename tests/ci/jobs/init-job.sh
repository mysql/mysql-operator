#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# init script executed before running tests, it purges old items and charges the local registry with necessary images
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh

# purge dangling items
$CI_DIR/cleanup/purge.sh

# ensure the local registry is running
$CI_DIR/registry/run-local-registry.sh $LOCAL_REGISTRY_CONTAINER_NAME $LOCAL_REGISTRY_HOST_PORT $LOCAL_REGISTRY_CONTAINER_PORT

IMAGES_LIST=$CI_DIR/registry/images-list.txt

# charge the local registry
$CI_DIR/registry/charge-local-registry.sh $REMOTE_REGISTRY_ADDRESS $REMOTE_REPOSITORY_NAME \
	$LOCAL_REGISTRY_ADDRESS $LOCAL_REPOSITORY_NAME $IMAGES_LIST

# push images only for a build triggered from concourse (for dev branches we build images on our own)
if [[ $OPERATOR_INTERNAL_BUILD == 'false' ]]; then
	# push the operator image to the local registry
	docker pull ${OPERATOR_IMAGE}
	if [ $? -ne 0 ]; then
		echo "cannot pull operator image ${OPERATOR_IMAGE}"
		exit 2
	fi
	docker tag ${OPERATOR_IMAGE} ${LOCAL_REGISTRY_OPERATOR_IMAGE}
	docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}

	# prepare enterprise image (temporary patch until we will have it in our hub)
	if [[ "$OPERATOR_IMAGE" =~ .*"$ENTERPRISE_OPERATOR_PATTERN".* ]]; then
		# if the image name contains pattern, then just tag to push it...
		docker tag ${OPERATOR_IMAGE} ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}
	else
		# ...else build a "stub" enterprise image
		$CI_DIR/registry/build-enterprise-image.sh $OPERATOR_IMAGE $LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE
	fi
	docker push ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}
fi

docker images --digests
