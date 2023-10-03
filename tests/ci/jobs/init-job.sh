#!/bin/bash
# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# init script executed before running tests, it purges old items and charges the local registry with necessary images
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

# purge dangling items
$CI_DIR/cleanup/purge.sh

# ensure the local registry is running, and charged with common images
$CI_DIR/registry/ensure-local-registry-running.sh

# at the moment, we push images only for a build triggered from concourse, while for dev branches
# we build images on our own
if [[ $OPERATOR_BUILD_IMAGES == 'false' ]]; then

	# prepare community image (needed only for SysQA flow for enterprise edition)
	if [[ -n $OPERATOR_IMAGE ]]; then
		# if the community image was provided as param, then just pull it...
		docker pull ${OPERATOR_IMAGE}
		if [ $? -ne 0 ]; then
			echo "cannot pull community operator image ${OPERATOR_IMAGE}"
			exit 2
		fi
		# ... and tag before push...
		docker tag ${OPERATOR_IMAGE} ${LOCAL_REGISTRY_OPERATOR_IMAGE}
	else
		# ...else build a "stub" community image
		$CI_DIR/registry/build-community-image.sh $OPERATOR_ENTERPRISE_IMAGE $LOCAL_REGISTRY_OPERATOR_IMAGE
	fi
	# push the community operator image to the local registry
	docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}


	# prepare enterprise image (temporary patch until we will have it in our hub)
	if [[ -n $OPERATOR_ENTERPRISE_IMAGE ]]; then
		# if the enterprise image was provided as param, then just pull it...
		docker pull ${OPERATOR_ENTERPRISE_IMAGE}
		if [ $? -ne 0 ]; then
			echo "cannot pull enterprise operator image ${OPERATOR_ENTERPRISE_IMAGE}"
			exit 2
		fi
		# ... and tag before push...
		docker tag ${OPERATOR_ENTERPRISE_IMAGE} ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}
	else
		# ...else build a "stub" enterprise image
		$CI_DIR/registry/build-enterprise-image.sh $OPERATOR_IMAGE $LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE
	fi
	# push the enterprise operator image to the local registry
	docker push ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}
fi

docker images --digests
docker images | wc -l
