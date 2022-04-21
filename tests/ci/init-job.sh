#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for running tests for both k3d / minikube
set -vx

source $WORKSPACE/tests/ci/job-env.sh

PULL_REPOSITORY_NAME=qa
PUSH_REGISTRY_URL=$OPERATOR_TEST_REGISTRY
PUSH_REPOSITORY_NAME=mysql
IMAGES_LIST=$CI_DIR/images-list.txt

# purge dangling items
$CI_DIR/cleanup/purge.sh

# ensure the local registry is running
$CI_DIR/run-local-registry.sh $LOCAL_REGISTRY_CONTAINER_NAME $LOCAL_REGISTRY_HOST_PORT $LOCAL_REGISTRY_CONTAINER_PORT

# charge the local registry
$CI_DIR/charge-local-registry.sh $PULL_REGISTRY_URL $PULL_REPOSITORY_NAME \
	$PUSH_REGISTRY_URL $PUSH_REPOSITORY_NAME $IMAGES_LIST

# push the newest operator image to the local registry
LOCAL_REGISTRY_OPERATOR_IMAGE=$PUSH_REGISTRY_URL/$PUSH_REPOSITORY_NAME/mysql-operator:$OPERATOR_TEST_VERSION_TAG
docker pull ${OPERATOR_IMAGE}
if [ $? -ne 0 ]; then
	echo "cannot pull operator image ${OPERATOR_IMAGE}"
	exit 2
fi
docker tag ${OPERATOR_IMAGE} ${LOCAL_REGISTRY_OPERATOR_IMAGE}
docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}

# prepare enterprise image (temporary patch untile we will have it in our hub)
LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE=$PUSH_REGISTRY_URL/$PUSH_REPOSITORY_NAME/enterprise-operator:$OPERATOR_TEST_VERSION_TAG
$CI_DIR/build-enterprise-image.sh $OPERATOR_IMAGE $LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE
docker push ${LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE}

docker images --digests
