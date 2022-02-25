#!/bin/bash
# generic script intended for running tests for both k3d / minikube
set -vx

export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY

python3 --version
df -lh | grep /sd

pwd
TESTS_DIR=$WORKSPACE/tests
CI_DIR=$TESTS_DIR/ci

LOCAL_REGISTRY_CONTAINER_NAME=registry.localhost
LOCAL_REGISTRY_HOST_PORT=5000
LOCAL_REGISTRY_CONTAINER_PORT=5000

IFS=':' read OPERATOR_IMAGE_PREFIX OPERATOR_IMAGE_TAG <<< ${OPERATOR_IMAGE}

export OPERATOR_TEST_REGISTRY=$LOCAL_REGISTRY_CONTAINER_NAME:$LOCAL_REGISTRY_HOST_PORT
export OPERATOR_TEST_VERSION_TAG=$OPERATOR_IMAGE_TAG

# OCI config
export OPERATOR_TEST_BACKUP_OCI_APIKEY_PATH=${WORKSPACE}/cred/backup
export OPERATOR_TEST_RESTORE_OCI_APIKEY_PATH=${WORKSPACE}/cred/restore
export OPERATOR_TEST_BACKUP_OCI_BUCKET=dumps

export OPERATOR_TEST_OCI_CONFIG_PATH=${WORKSPACE}/cred/config
export OPERATOR_TEST_OCI_BUCKET=dumps

PUSH_REGISTRY_URL=$OPERATOR_TEST_REGISTRY
PUSH_REPOSITORY_NAME=mysql
IMAGES_LIST=$CI_DIR/images-list.txt
IMAGES_LIST_EE=$CI_DIR/images-list-ee.txt

# ensure the local registry is running
$CI_DIR/run-local-registry.sh $LOCAL_REGISTRY_CONTAINER_NAME $LOCAL_REGISTRY_HOST_PORT $LOCAL_REGISTRY_CONTAINER_PORT

# charge the local registry
$CI_DIR/charge-local-registry.sh $PULL_REGISTRY_URL $PULL_REPOSITORY_NAME \
	$PUSH_REGISTRY_URL $PUSH_REPOSITORY_NAME $IMAGES_LIST

# temporarily push-only, till a proper setup of credentials for the EE registry
$CI_DIR/charge-local-registry.sh --push-only $PULL_REGISTRY_URL_EE $PULL_REPOSITORY_NAME_EE \
	$PUSH_REGISTRY_URL $PUSH_REPOSITORY_NAME $IMAGES_LIST_EE

# push the newest operator image to the local registry
LOCAL_REGISTRY_OPERATOR_IMAGE=$PUSH_REGISTRY_URL/$PUSH_REPOSITORY_NAME/mysql-operator:$OPERATOR_TEST_VERSION_TAG
docker pull ${OPERATOR_IMAGE}
docker tag ${OPERATOR_IMAGE} ${LOCAL_REGISTRY_OPERATOR_IMAGE}
docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}

docker images

# set our temporary kubeconfig, because the default one may contain unrelated data that could fail the build
TMP_KUBE_CONFIG="$WORKSPACE/tmpkubeconfig.$K8S_DRIVER"
cat "$TMP_KUBE_CONFIG"
: > "$TMP_KUBE_CONFIG"
export KUBECONFIG=$TMP_KUBE_CONFIG

trap 'kill $(jobs -p)' EXIT
cd "$TESTS_DIR"

TEST_OPTIONS='-t -vvv --doperator --dkube -doci'

if test -z ${WORKERS+x}; then
	WORKERS=1
fi
TAG=$JOB_BASE_NAME-build-$BUILD_NUMBER

if test -z ${WORKERS_DEFER+x}; then
	WORKERS_DEFER=0
fi

TESTS_LOG=$WORKSPACE/tests-$JOB_BASE_NAME-$BUILD_NUMBER.log
touch $TESTS_LOG
tail -f "$TESTS_LOG" &

# a patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$CI_DIR/show-progress.sh" 240 30 &

# by default TEST_SUITE is not defined, it means to run all tests
if test $WORKERS == 1; then
	./run --env=$K8S_DRIVER $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
else
	python3 ./dist_run_e2e_tests.py --env=$K8S_DRIVER --workers=$WORKERS --defer=$WORKERS_DEFER --tag=$TAG $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
fi

# process the tests results
python3 $CI_DIR/inspect-result.py $CI_DIR/expected-failures.txt "$TESTS_LOG"
TESTS_RESULT=$?

bzip2 "$TESTS_LOG"
df -lh | grep /sd

exit $TESTS_RESULT
