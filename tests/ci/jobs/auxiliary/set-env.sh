#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for running tests for both k3d / minikube
set -vx

TESTS_DIR=$WORKSPACE/tests
CI_DIR=$TESTS_DIR/ci
EXPECTED_FAILURES_PATH="$CI_DIR/expected-failures.txt"

LOCAL_REGISTRY_CONTAINER_NAME=registry.localhost
LOCAL_REGISTRY_HOST_PORT=5000
LOCAL_REGISTRY_CONTAINER_PORT=5000

LOCAL_REGISTRY_ADDRESS=$LOCAL_REGISTRY_CONTAINER_NAME:$LOCAL_REGISTRY_HOST_PORT
LOCAL_REPOSITORY_NAME=mysql

REMOTE_REPOSITORY_NAME=qa

export OPERATOR_TEST_REGISTRY=$LOCAL_REGISTRY_ADDRESS
export OPERATOR_TEST_VERSION_TAG=$(echo $OPERATOR_IMAGE | awk -F":" '{print $NF}')

ENTERPRISE_OPERATOR_PATTERN="enterprise-operator"

LOCAL_REGISTRY_OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/mysql-operator:$OPERATOR_TEST_VERSION_TAG
LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$ENTERPRISE_OPERATOR_PATTERN:$OPERATOR_TEST_VERSION_TAG

# OCI config
CREDENTIALS_DIR=${WORKSPACE}/../../cred
if ! test -d ${CREDENTIALS_DIR}; then
	echo "credentials directory ${CREDENTIALS_DIR} doesn't exist"
	exit 1
fi
export OPERATOR_TEST_OCI_CONFIG_PATH=${CREDENTIALS_DIR}/config
export OPERATOR_TEST_OCI_BUCKET=dumps

# log some infos
pwd
python3 --version
df -lh | grep /sd

echo "NODE_NAME: $NODE_NAME"
