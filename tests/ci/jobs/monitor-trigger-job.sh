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

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh

echo "GIT_COMMIT: ${GIT_COMMIT}"
echo "GIT_COMMITTER_NAME: ${GIT_COMMITTER_NAME}"
echo "GIT_COMMITTER_EMAIL: ${GIT_COMMITTER_EMAIL}"
echo "GIT_URL: ${GIT_URL}"
echo "GIT_URL_N: ${GIT_URL_N}"
echo "GIT_BRANCH: ${GIT_BRANCH}"
echo "GIT_LOCAL_BRANCH: ${GIT_LOCAL_BRANCH}"
echo "GIT_PREVIOUS_COMMIT: ${GIT_PREVIOUS_COMMIT}"
echo "GIT_PREVIOUS_SUCCESSFUL_COMMIT: ${GIT_PREVIOUS_SUCCESSFUL_COMMIT}"

# jenkins regex filter for dev branch: ":^(?!origin/trunk$|origin/itch$|origin/dev$|origin/qa$|origin/s?qa-.*$).+$"

BRANCH_SHORT_NAME=${GIT_BRANCH#"origin/"}

case "${BRANCH_SHORT_NAME}" in
dev|trunk)
	# skip, should be triggered by concourse
	;;
itch)
	JOB_NAME="itch"
	;;
qa)
	JOB_NAME="qa"
	;;
qa*|sqa*|ci*)
	JOB_NAME="qa"
	;;
*)
	JOB_NAME="dev"
	;;
esac
# JOB_NAME=test-pipeline

if [[ -z $JOB_NAME ]]; then
	echo "skipped ${GIT_BRANCH} $GIT_COMMIT"
	exit 0
fi

OPERATOR_GIT_REVISION=$GIT_COMMIT
OPERATOR_DEV_IMAGE_TAG=$(git describe --tags)'-dev'
OPERATOR_IMAGE=$LOCAL_REGISTRY_ADDRESS/$LOCAL_REPOSITORY_NAME/mysql-operator:$OPERATOR_DEV_IMAGE_TAG

JOB_PARAMS="OPERATOR_GIT_REVISION=${OPERATOR_GIT_REVISION}&OPERATOR_IMAGE=${OPERATOR_IMAGE}&OPERATOR_INTERNAL_BUILD=true"

JOB_LINK=${JOB_PREFIX}/${JOB_NAME}
curl -X POST -u ${JENKINS_USER_CRED} ${JOB_LINK}/buildWithParameters?${JOB_PARAMS}
