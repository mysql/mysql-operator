#!/bin/bash
# Copyright (c) 2022, 2023 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# a helper job to trigger the weekly build
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

PIPELINE_NAME=weekly

case "${JOB_PREFIX}" in
*/sandbox/*)
  OPERATOR_GIT_REVISION=${GIT_BRANCH}
  ;;
*)
  OPERATOR_GIT_REVISION=trunk
  ;;
esac

OPERATOR_GIT_REPO_URL=$MYREPO_GIT_REPO_URL
OPERATOR_GIT_REPO_NAME=origin
OPERATOR_GIT_REVISION=$GIT_COMMIT
OPERATOR_GIT_REFSPEC=$GIT_COMMIT
OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$COMMUNITY_OPERATOR_IMAGE_NAME:$OPERATOR_BASE_VERSION_TAG
OPERATOR_ENTERPRISE_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$ENTERPRISE_OPERATOR_IMAGE_NAME:$OPERATOR_BASE_VERSION_TAG
OPERATOR_TRIGGERED_BY=internal
OPERATOR_BUILD_IMAGES='false'
OPERATOR_ALLOW_WEEKLY_IMAGES='true'

JOB_PARAMS="OPERATOR_GIT_REPO_URL=${OPERATOR_GIT_REPO_URL}&OPERATOR_GIT_REPO_NAME=${OPERATOR_GIT_REPO_NAME}"
JOB_PARAMS="${JOB_PARAMS}&OPERATOR_GIT_REVISION=${OPERATOR_GIT_REVISION}&OPERATOR_GIT_REFSPEC=${OPERATOR_GIT_REFSPEC}"
JOB_PARAMS="${JOB_PARAMS}&OPERATOR_IMAGE=${OPERATOR_IMAGE}&OPERATOR_ENTERPRISE_IMAGE=${OPERATOR_ENTERPRISE_IMAGE}"
JOB_PARAMS="${JOB_PARAMS}&OPERATOR_TRIGGERED_BY=${OPERATOR_TRIGGERED_BY}&OPERATOR_BUILD_IMAGES=${OPERATOR_BUILD_IMAGES}"
JOB_PARAMS="${JOB_PARAMS}&OPERATOR_ALLOW_WEEKLY_IMAGES=${OPERATOR_ALLOW_WEEKLY_IMAGES}"

JOB_LINK=${JOB_PREFIX}/${PIPELINE_NAME}
curl -X POST -u ${JENKINS_USER_CRED} ${JOB_LINK}/buildWithParameters?${JOB_PARAMS}
