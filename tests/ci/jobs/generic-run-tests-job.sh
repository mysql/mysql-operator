#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for running tests for both k3d / minikube
set -vx

export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh

# set our temporary kubeconfig, because the default one may contain unrelated data that could fail the build
TMP_KUBE_CONFIG="$WORKSPACE/tmpkubeconfig.$K8S_DRIVER"
cat "$TMP_KUBE_CONFIG"
: > "$TMP_KUBE_CONFIG"
export KUBECONFIG=$TMP_KUBE_CONFIG

trap 'kill $(jobs -p)' EXIT
cd "$TESTS_DIR"

if test -z ${TEST_OPTIONS+x}; then
	TEST_OPTIONS='-t -vvv --doperator --dkube --doci'
fi

if [[ -n ${OPERATOR_K8S_VERSION} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --kube-version=$OPERATOR_K8S_VERSION"
fi

if test -z ${WORKERS+x}; then
	if [[ $K8S_DRIVER == "minikube" ]]; then
		WORKERS=3
	elif [[ $K8S_DRIVER == "k3d" ]]; then
		WORKERS=4
	else
		WORKERS=1
	fi
fi

OTE_BUILD_TAG=ote-$JOB_BASE_NAME-build-$BUILD_NUMBER

if test -z ${WORKERS_DEFER+x}; then
	if test "$K8S_DRIVER" == "minikube"; then
		WORKERS_DEFER=60
	else
		WORKERS_DEFER=45
	fi
fi

LOG_DIR=$WORKSPACE/build-$BUILD_NUMBER
if test -d ${LOG_DIR}; then
	rm -rfd $LOG_DIR
fi
mkdir -p $LOG_DIR

TESTS_LOG=$LOG_DIR/tests-$JOB_BASE_NAME-$BUILD_NUMBER.log

XML_DIR=$LOG_DIR/xml

TESTS_XML=$XML_DIR/$K8S_DRIVER-tests-$BUILD_NUMBER.xml
SINGLE_WORKER_OPTIONS="--xml=${TESTS_XML} --cluster=$OTE_BUILD_TAG"


DIST_RUN_OPTIONS="--workers=$WORKERS --workdir=$LOG_DIR --defer=$WORKERS_DEFER --tag=$OTE_BUILD_TAG --xml --expected-failures=$EXPECTED_FAILURES_PATH"

touch $TESTS_LOG
tail -f "$TESTS_LOG" &

# a patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$CI_DIR/jobs/auxiliary/show-progress.sh" 240 30 &

# by default TEST_SUITE is not defined, it means to run all tests
if test $WORKERS == 1; then
	mkdir -p $XML_DIR
	./run --env=$K8S_DRIVER $SINGLE_WORKER_OPTIONS $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
	TMP_SUMMARY_PATH=$(mktemp)
	# process the tests results
	python3 $CI_DIR/jobs/auxiliary/process_single_worker_log.py $EXPECTED_FAILURES_PATH "$TESTS_LOG" > $TMP_SUMMARY_PATH 2>&1
	TESTS_RESULT=$?
	cat $TMP_SUMMARY_PATH >> "$TESTS_LOG"
	rm $TMP_SUMMARY_PATH
else
	python3 ./dist_run_e2e_tests.py --env=$K8S_DRIVER $DIST_RUN_OPTIONS $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
	TESTS_RESULT=$?
	$CI_DIR/cleanup/remove_networks.sh $OTE_BUILD_TAG
fi

cd $LOG_DIR
# badge results to discern the environment in the overall result
BADGE=$K8S_DRIVER
if [[ -n ${OPERATOR_K8S_VERSION} ]]; then
	BADGE="${BADGE}_${OPERATOR_K8S_VERSION}"
	BADGE=$(sed -e 's/[.:\/]/_/g' <<< $BADGE)
fi
sed -i "s/=\"e2e.mysqloperator./=\"$BADGE.e2e.mysqloperator./g" ./xml/*.xml
sed -i "s/<testcase classname=\"\" name=\"\(\w*\) (e2e.mysqloperator./<testcase classname=\"\" name=\"$BADGE.\1 ($BADGE.e2e.mysqloperator./g" ./xml/*.xml

# store extraordinary issues
BROKEN_WORKERS=$(egrep '^broken\s+: [0-9]+$' ${TESTS_LOG} | awk '{print $3}')
if [[ -n $BROKEN_WORKERS && $BROKEN_WORKERS -gt 0 ]]; then
	ALL_WORKERS=$(egrep '^all\s+: [0-9]+$' ${TESTS_LOG} | awk '{print $3}')
	BROKEN_WORKERS_MSG="${K8S_DRIVER}: ${BROKEN_WORKERS} out of ${ALL_WORKERS} worker(s) have broken, some test results are missing!"
	ISSUES_LOG=$LOG_DIR/${K8S_DRIVER}-issues.log
	echo ${BROKEN_WORKERS_MSG} > ${ISSUES_LOG}
	cat ${ISSUES_LOG}
fi

# store runtime environment
RUNTIME_ENV_LOG=${K8S_DRIVER}-runtime-env-$BUILD_NUMBER.log
${K8S_DRIVER} version > $RUNTIME_ENV_LOG
KUBECTL_VERSION=$(kubectl version --client -o json | jq '.clientVersion.gitVersion')
echo "kubectl: ${KUBECTL_VERSION}" >> $RUNTIME_ENV_LOG
if [[ -n ${OPERATOR_K8S_VERSION} ]]; then
	echo "custom k8s version: ${OPERATOR_K8S_VERSION}" >> $RUNTIME_ENV_LOG
fi
cat ${RUNTIME_ENV_LOG}

# archive all logs and auxiliary files
tar cvjf ../result-$JOB_BASE_NAME-$BUILD_NUMBER.tar.bz2 *
df -lh | grep /sd

exit $TESTS_RESULT
