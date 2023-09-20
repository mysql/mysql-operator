#!/bin/bash
# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# generic script intended for running tests for both k3d / minikube
set -vx

export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

# set our temporary kubeconfig, because the default one may contain unrelated data that could fail the build
export KUBECONFIG=$(mktemp /tmp/kubeconfig.$K8S_DRIVER-XXXXXX)

trap 'kill $(jobs -p)' EXIT
cd "$TESTS_DIR"

if test -z ${TEST_OPTIONS+x}; then
	TEST_OPTIONS='-t -vvv --doperator --dkube --doci --skip-audit-log --store-operator-log'
fi

if [[ -z ${OPERATOR_TEST_SKIP_AZURE} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --start-azure"
fi

ENV_BINARY_PATH=${OPERATOR_ENV_BINARY_PATH}
if [[ -n ${ENV_BINARY_PATH} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --env-binary-path=$ENV_BINARY_PATH"
else
	ENV_BINARY_PATH=$K8S_DRIVER
fi

if [[ -n ${OPERATOR_K8S_VERSION} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --kube-version=$OPERATOR_K8S_VERSION"
fi

KUBECTL_PATH=${OPERATOR_KUBECTL_PATH}
if [[ -n ${KUBECTL_PATH} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --kubectl-path=$KUBECTL_PATH"
else
	KUBECTL_PATH="kubectl"
fi

if ! [[ -n ${OPERATOR_WORKERS_COUNT} && ${OPERATOR_WORKERS_COUNT} -gt 0 ]]; then
	if [[ $K8S_DRIVER == "minikube" ]]; then
		OPERATOR_WORKERS_COUNT=3
	elif [[ $K8S_DRIVER == "k3d" ]]; then
		OPERATOR_WORKERS_COUNT=4
	else
		OPERATOR_WORKERS_COUNT=1
	fi
fi

if [[ -n ${OPERATOR_NODES_COUNT} && ${OPERATOR_NODES_COUNT} -gt 1 ]]; then
	OTE_MULTIPLE_NODES=true
fi

if [[ -n ${OTE_MULTIPLE_NODES} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --nodes=$OPERATOR_NODES_COUNT"
fi

if [[ -n ${OPERATOR_NODE_MEMORY} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --node-memory=$OPERATOR_NODE_MEMORY"
fi

if [[ -n ${OPERATOR_IP_FAMILY} ]]; then
	TEST_OPTIONS="$TEST_OPTIONS --ip-family=$OPERATOR_IP_FAMILY"
fi

OTE_LOG_PREFIX=$K8S_DRIVER-build-$BUILD_NUMBER
OTE_BUILD_TAG=ote-$OTE_LOG_PREFIX

if test -z ${WORKERS_DEFER+x}; then
	if test "$K8S_DRIVER" == "k3d"; then
		WORKERS_DEFER=45
	else
		WORKERS_DEFER=60
	fi
fi

LOG_DIR=$WORKSPACE/build-$BUILD_NUMBER
if test -d "${LOG_DIR}"; then
	rm -rfd $LOG_DIR
fi
mkdir -p $LOG_DIR
TEST_OPTIONS="$TEST_OPTIONS --workdir=$LOG_DIR"

TESTS_LOG=$LOG_DIR/$OTE_LOG_PREFIX-all.log

XML_DIR=$LOG_DIR/xml

TESTS_XML=$XML_DIR/$OTE_LOG_PREFIX-tests.xml
SINGLE_WORKER_OPTIONS="--xml=${TESTS_XML} --cluster=$OTE_BUILD_TAG ${TEST_OPTIONS}"


DIST_RUN_OPTIONS="--workers=$OPERATOR_WORKERS_COUNT --defer=$WORKERS_DEFER --tag=$OTE_BUILD_TAG --xml"
DIST_RUN_OPTIONS="--expected-failures=$EXPECTED_FAILURES_PATH ${DIST_RUN_OPTIONS} ${TEST_OPTIONS}"

touch $TESTS_LOG
tail -f "$TESTS_LOG" &

# a patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$CI_DIR/jobs/auxiliary/show-progress.sh" 480 30 &

# by default TEST_SUITE is not defined, it means to run all tests
if test $OPERATOR_WORKERS_COUNT == 1; then
	mkdir -p $XML_DIR
	./run --env=$K8S_DRIVER $SINGLE_WORKER_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
	TMP_SUMMARY_PATH=$(mktemp)
	# process the tests results
	python3 $CI_DIR/jobs/auxiliary/process_single_worker_log.py $EXPECTED_FAILURES_PATH "$TESTS_LOG" > $TMP_SUMMARY_PATH 2>&1
	TESTS_RESULT=$?
	cat $TMP_SUMMARY_PATH >> "$TESTS_LOG"
	rm $TMP_SUMMARY_PATH
else
	python3 ./dist_run_e2e_tests.py --env=$K8S_DRIVER $DIST_RUN_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
	TESTS_RESULT=$?
	$CI_DIR/cleanup/remove_networks.sh $OTE_BUILD_TAG
fi

cd $LOG_DIR
# badge results to discern the environment in the overall result
JOB_BADGE=$K8S_DRIVER
if [[ -n ${OPERATOR_K8S_VERSION} ]]; then
	JOB_BADGE="${JOB_BADGE}_${OPERATOR_K8S_VERSION}"
fi
if [[ -n ${OTE_MULTIPLE_NODES} ]]; then
	JOB_BADGE="${JOB_BADGE}-${OPERATOR_NODES_COUNT}_nodes"
fi
if [[ -n ${OPERATOR_IP_FAMILY} && ${OPERATOR_IP_FAMILY} != "ipv4" ]]; then
	JOB_BADGE="${JOB_BADGE}-${OPERATOR_IP_FAMILY}"
fi
JOB_BADGE=$(sed -e 's/[.:\/]/_/g' <<< $JOB_BADGE)
sed -i "s/=\"e2e.mysqloperator./=\"$JOB_BADGE.e2e.mysqloperator./g" ./xml/*.xml
sed -i "s/<testcase classname=\"\" name=\"\(\w*\) (e2e.mysqloperator./<testcase classname=\"\" name=\"$JOB_BADGE.\1 ($JOB_BADGE.e2e.mysqloperator./g" ./xml/*.xml

# store test suite stats, search the log for the following numbers:
# tests   : 214
# failures: 3
# errors  : 0
# skipped : 0

function extract_test_suite_stat() {
	STAT_LABEL=$1
	echo $(tac ${TESTS_LOG} | egrep -m 1 "^${STAT_LABEL}\s*: [0-9]+$" | awk -F':' '{print $2}')
}

function extract_execution_time() {
	echo $(tac ${TESTS_LOG} | egrep -m 1 '^execution time\s*: .+ \([0-9.]+s\)$' | sed 's/.*:\s*//')
}

TESTS_COUNT=$(extract_test_suite_stat 'tests')
FAILURES_COUNT=$(extract_test_suite_stat 'failures')
ERRORS_COUNT=$(extract_test_suite_stat 'errors')
SKIPPED_COUNT=$(extract_test_suite_stat 'skipped')
EXECUTION_TIME=$(extract_execution_time)

if [[ -n $TESTS_COUNT && $TESTS_COUNT -gt 0 && -n $FAILURES_COUNT && -n $ERRORS_COUNT && -n $SKIPPED_COUNT && -n $EXECUTION_TIME ]]; then
	FAILED_TESTS=$(expr $FAILURES_COUNT + $ERRORS_COUNT)
	if [[ $FAILED_TESTS -gt $TESTS_COUNT ]]; then
		FAILED_TESTS=$TESTS_COUNT
	fi
	PASSED_TESTS=$(expr $TESTS_COUNT - $FAILED_TESTS - $SKIPPED_COUNT)
	if [[ $PASSED_TESTS -lt 0 ]]; then
		PASSED_TESTS=0
	fi
	STATS_MSG="${JOB_BADGE}: $TESTS_COUNT tests, $PASSED_TESTS passed, $FAILED_TESTS failed, $SKIPPED_COUNT skipped [$EXECUTION_TIME]"
	STATS_LOG=$LOG_DIR/${OTE_LOG_PREFIX}-stats.log
	echo ${STATS_MSG} > ${STATS_LOG}
	cat ${STATS_LOG}
fi

# store extraordinary issues
BROKEN_WORKERS=$(tac ${TESTS_LOG} | egrep -m 1 '^broken\s+: [0-9]+$' | awk '{print $3}')
if [[ -n $BROKEN_WORKERS && $BROKEN_WORKERS -gt 0 ]]; then
	ALL_WORKERS=$(tac ${TESTS_LOG} | egrep -m 1 '^all\s+: [0-9]+$' | awk '{print $3}')
	BROKEN_WORKERS_MSG="${JOB_BADGE}: ${BROKEN_WORKERS} out of ${ALL_WORKERS} worker(s) have broken, some test results are missing!"
	ISSUES_LOG=$LOG_DIR/${OTE_LOG_PREFIX}-issues.log
	echo ${BROKEN_WORKERS_MSG} > ${ISSUES_LOG}
	cat ${ISSUES_LOG}
fi

# store runtime environment
RUNTIME_ENV_LOG=${OTE_LOG_PREFIX}-runtime-env.log
# env
${ENV_BINARY_PATH} version > $RUNTIME_ENV_LOG
ENV_BINARY_PATH=$(which ${ENV_BINARY_PATH})
echo "path: ${ENV_BINARY_PATH}" >> $RUNTIME_ENV_LOG
if [[ -n ${OPERATOR_K8S_VERSION} ]]; then
	echo "custom k8s version: ${OPERATOR_K8S_VERSION}" >> $RUNTIME_ENV_LOG
fi
# workers / nodes
if [[ -n ${OPERATOR_WORKERS_COUNT} ]]; then
	echo "workers: ${OPERATOR_WORKERS_COUNT}" >> $RUNTIME_ENV_LOG
fi
if [[ -n ${OTE_MULTIPLE_NODES} ]]; then
	echo "nodes per worker: ${OPERATOR_NODES_COUNT}" >> $RUNTIME_ENV_LOG
fi
if [[ -n ${OPERATOR_NODE_MEMORY} ]]; then
	echo "memory per node: ${OPERATOR_NODE_MEMORY}MB" >> $RUNTIME_ENV_LOG
fi
if [[ -n ${OPERATOR_IP_FAMILY} ]]; then
	echo "IP family: ${OPERATOR_IP_FAMILY}" >> $RUNTIME_ENV_LOG
fi
# kubectl (client, server, path)
KUBECTL_VERSION=$(${KUBECTL_PATH} version --client -o json | jq '.clientVersion.gitVersion')
echo "kubectl client: ${KUBECTL_VERSION}" >> $RUNTIME_ENV_LOG
KUBECTL_VERSION=$(${KUBECTL_PATH} version -o json | jq '.serverVersion.gitVersion')
echo "kubectl server: ${KUBECTL_VERSION}" >> $RUNTIME_ENV_LOG
KUBECTL_FULL_PATH=$(which ${KUBECTL_PATH})
echo "path: ${KUBECTL_FULL_PATH}" >> $RUNTIME_ENV_LOG
cat ${RUNTIME_ENV_LOG}

# archive all logs and auxiliary files
tar cvjf ../$OTE_LOG_PREFIX-result.tar.bz2 *
df -lh | grep /sd

exit $TESTS_RESULT
