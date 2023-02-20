#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# monitors development branches that are not watched at concourse
# then triggers a build in a proper pipeline depending on the branch name
# the git branches monitored at concourse are (in theory): dev, qa, itch, and trunk. In practice, it is trunk only,
# but it shall change in the future, hopefully
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || return

PIPELINE_NAME=weekly
OPERATOR_GIT_REVISION=trunk
OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$COMMUNITY_OPERATOR_IMAGE_NAME:$OPERATOR_BASE_VERSION_TAG
OPERATOR_ENTERPRISE_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/$ENTERPRISE_OPERATOR_IMAGE_NAME:$OPERATOR_BASE_VERSION_TAG
OPERATOR_INTERNAL_BUILD=false

JOB_PARAMS="OPERATOR_GIT_REVISION=${OPERATOR_GIT_REVISION}&OPERATOR_IMAGE=${OPERATOR_IMAGE}"
JOB_PARAMS="${JOB_PARAMS}&OPERATOR_ENTERPRISE_IMAGE=${OPERATOR_ENTERPRISE_IMAGE}&OPERATOR_INTERNAL_BUILD=${OPERATOR_INTERNAL_BUILD}"

JOB_LINK=${JOB_PREFIX}/${PIPELINE_NAME}
curl -X POST -u ${JENKINS_USER_CRED} ${JOB_LINK}/buildWithParameters?${JOB_PARAMS}
