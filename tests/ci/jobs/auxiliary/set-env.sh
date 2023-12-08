#!/bin/bash
# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for running tests for both k3d / minikube
set -vx

TESTS_DIR=$WORKSPACE/tests
export PYTHONPATH=$PYTHONPATH:$TESTS_DIR
CI_DIR=$TESTS_DIR/ci
EXPECTED_FAILURES_PATH="$CI_DIR/expected-failures.txt"
export BUILD_DIR=$WORKSPACE/build-$BUILD_NUMBER

LOCAL_REGISTRY_CONTAINER_NAME=registry.localhost
LOCAL_REGISTRY_HOST_PORT=5000
LOCAL_REGISTRY_CONTAINER_PORT=5000

LOCAL_REGISTRY_ADDRESS=$LOCAL_REGISTRY_CONTAINER_NAME:$LOCAL_REGISTRY_HOST_PORT
LOCAL_REPOSITORY_NAME=mysql

if [[ -z $REMOTE_REPOSITORY_NAME ]]; then
	export REMOTE_REPOSITORY_NAME=qa
fi

WEEKLY_REPOSITORY_NAME=weekly

export OPERATOR_TEST_REGISTRY=$LOCAL_REGISTRY_ADDRESS

OPERATOR_IMAGE_TO_PARSE_TAG=${OPERATOR_IMAGE:-$OPERATOR_ENTERPRISE_IMAGE}
export OPERATOR_TEST_VERSION_TAG=$(echo $OPERATOR_IMAGE_TO_PARSE_TAG | awk -F":" '{print $NF}')

COMMUNITY_OPERATOR_IMAGE_NAME=community-operator
ENTERPRISE_OPERATOR_IMAGE_NAME=enterprise-operator

LOCAL_REGISTRY_OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$COMMUNITY_OPERATOR_IMAGE_NAME:$OPERATOR_TEST_VERSION_TAG
LOCAL_REGISTRY_ENTERPRISE_OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$ENTERPRISE_OPERATOR_IMAGE_NAME:$OPERATOR_TEST_VERSION_TAG

# OCI config
export OPERATOR_TEST_LOCAL_CREDENTIALS_DIR=$BUILD_DIR/credentials
if [[ -z $OPERATOR_TEST_CREDENTIALS_DIR ]]; then
	OPERATOR_TEST_CREDENTIALS_DIR=$OPERATOR_TEST_LOCAL_CREDENTIALS_DIR
fi
export OPERATOR_TEST_OCI_CONFIG_PATH=${OPERATOR_TEST_CREDENTIALS_DIR}/config
export OPERATOR_TEST_OCI_BUCKET=dumps
export OPERATOR_TEST_VAULT_CONFIG_PATH=${OPERATOR_TEST_CREDENTIALS_DIR}/vault.cfg

# extract operator version from the defaults
export OPERATOR_BASE_VERSION_TAG=$(grep -m 1 OPERATOR_TEST_VERSION_TAG $WORKSPACE/tests/setup/defaults.py \
	| sed 's/[[:blank:]]*"OPERATOR_TEST_VERSION_TAG", default="\([0-9.-]*\)")/\1/')

# to enable OCI builds
# - set it here explicitly to apply it globally
# - define it in a given trigger job (monitor | weekly | gerrit)
# - modify a given pipeline (regular | weekly) in src or through Jenkins UI
# U may also want to modify the function canRunOnOci in ./tests/ci/pipeline/utils.groovy
# to indicate which k8s workers can run on an OCI instance
# OTE_RUN_ON_OCI=1

# execution environment
if [[ -v OTE_RUN_ON_OCI && $OTE_RUN_ON_OCI -eq 1 ]]; then
	OTE_DEFAULT_EXECUTION_ENVIRONMENT='oci'
else
	OTE_DEFAULT_EXECUTION_ENVIRONMENT='local'
fi

# log some infos
pwd
python3 --version
df -lh | grep /sd
free -h
nproc --all

echo "NODE_NAME: $NODE_NAME"
