#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for building dev images
set -vx

source $WORKSPACE/tests/ci/job_aux/job-env.sh

OPERATOR_BASE_VERSION_TAG=$(grep -m 1 OPERATOR_TEST_VERSION_TAG $WORKSPACE/tests/setup/defaults.py \
	| sed 's/[[:blank:]]*"OPERATOR_TEST_VERSION_TAG", default="\([0-9.-]*\)")/\1/')

if [ "$OPERATOR_BASE_VERSION_TAG" != "$OPERATOR_TEST_VERSION_TAG" ]; then
	DEV_IMAGE_DOCKERFILE=$WORKSPACE/tests/ci/registry/dev/Dockerfile

	# community
	BASE_IMAGE_COMMUNITY=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/mysql-operator:$OPERATOR_BASE_VERSION_TAG
	docker build -f $DEV_IMAGE_DOCKERFILE \
		-t $LOCAL_REGISTRY_OPERATOR_IMAGE \
		--build-arg BASE_IMAGE=$BASE_IMAGE_COMMUNITY .
	docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}

	# enterprise
	BASE_IMAGE_ENTERPRISE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/enterprise-operator:$OPERATOR_BASE_VERSION_TAG
	docker build -f $DEV_IMAGE_DOCKERFILE \
		-t $LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE \
		--build-arg BASE_IMAGE=$BASE_IMAGE_ENTERPRISE .
	docker push ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}
fi

docker images --digests
