#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for building dev images
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh

OPERATOR_BASE_VERSION_TAG=$(grep -m 1 OPERATOR_TEST_VERSION_TAG $WORKSPACE/tests/setup/defaults.py \
	| sed 's/[[:blank:]]*"OPERATOR_TEST_VERSION_TAG", default="\([0-9.-]*\)")/\1/')

DEV_IMAGE_DOCKERFILE=$CI_DIR/registry/dev/Dockerfile

# overwrite the default version tag with the dev one
sed -i "s/${OPERATOR_BASE_VERSION_TAG}/${OPERATOR_TEST_VERSION_TAG}/" mysqloperator/controller/config.py

# community
BASE_IMAGE_COMMUNITY=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/mysql-operator:$OPERATOR_BASE_VERSION_TAG
docker build -f $DEV_IMAGE_DOCKERFILE \
	-t $LOCAL_REGISTRY_OPERATOR_IMAGE \
	--build-arg BASE_IMAGE=$BASE_IMAGE_COMMUNITY .
docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}

# enterprise
$CI_DIR/registry/build-enterprise-image.sh $LOCAL_REGISTRY_OPERATOR_IMAGE $LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE
docker push ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}

# BASE_IMAGE_ENTERPRISE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/enterprise-operator:$OPERATOR_BASE_VERSION_TAG
# docker build -f $DEV_IMAGE_DOCKERFILE \
# 	-t $LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE \
# 	--build-arg BASE_IMAGE=$BASE_IMAGE_ENTERPRISE .
# docker push ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}

docker images --digests
