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

source $WORKSPACE/tests/ci/job-env.sh

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

if test -z ${WORKERS+x}; then
	WORKERS=1
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

TESTS_XML=$XML_DIR/tests-$JOB_BASE_NAME-$BUILD_NUMBER.xml
SINGLE_WORKER_OPTIONS="--xml=${TESTS_XML} --cluster=$OTE_BUILD_TAG"


DIST_RUN_OPTIONS="--workers=$WORKERS --workdir=$LOG_DIR --defer=$WORKERS_DEFER --tag=$OTE_BUILD_TAG --xml --expected-failures=$EXPECTED_FAILURES_PATH"

touch $TESTS_LOG
tail -f "$TESTS_LOG" &

# a patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$JOB_AUX_DIR/show-progress.sh" 240 30 &

# by default TEST_SUITE is not defined, it means to run all tests
if test $WORKERS == 1; then
	mkdir -p $XML_DIR
	./run --env=$K8S_DRIVER $SINGLE_WORKER_OPTIONS $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
	TMP_SUMMARY_PATH=$(mktemp)
	# process the tests results
	python3 $JOB_AUX_DIR/process_single_worker_log.py $EXPECTED_FAILURES_PATH "$TESTS_LOG" > $TMP_SUMMARY_PATH 2>&1
	TESTS_RESULT=$?
	cat $TMP_SUMMARY_PATH >> "$TESTS_LOG"
	rm $TMP_SUMMARY_PATH
else
	python3 ./dist_run_e2e_tests.py --env=$K8S_DRIVER $DIST_RUN_OPTIONS $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
	TESTS_RESULT=$?
	$CI_DIR/cleanup/remove_networks.sh $OTE_BUILD_TAG
fi

cd $LOG_DIR
sed -i "s/=\"e2e.mysqloperator./=\"$K8S_DRIVER.e2e.mysqloperator./g" ./xml/*.xml
tar cvjf ../result-$JOB_BASE_NAME-$BUILD_NUMBER.tar.bz2 *
df -lh | grep /sd

exit $TESTS_RESULT
