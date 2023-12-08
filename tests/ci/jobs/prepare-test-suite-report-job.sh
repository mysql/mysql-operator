#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# pulls test results for a specified job, then prepares a summary that will be sent in a slack notification
set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

LOG_DIR=$BUILD_DIR
if test -d "${LOG_DIR}"; then
	rm -rfd $LOG_DIR
fi
mkdir -p $LOG_DIR

JOB_RESULT_PATH=$LOG_DIR/job_test_result.json
curl -X GET -u $JENKINS_USER_CRED ${TEST_RESULT_BUILD_URL}/testReport/api/json?pretty > $JOB_RESULT_PATH

TEST_SUITE_REPORT_FNAME=test_suite_report.txt
TEST_SUITE_REPORT_PATH=$LOG_DIR/$TEST_SUITE_REPORT_FNAME
"$CI_DIR/jobs/auxiliary/parse_job_test_result.py" $JOB_RESULT_PATH > $TEST_SUITE_REPORT_PATH
cat $TEST_SUITE_REPORT_PATH | head -15

cd $LOG_DIR
tar cjf ../test_suite_report_$BUILD_NUMBER.tar.bz2 $TEST_SUITE_REPORT_FNAME

# prune old builds
find $WORKSPACE/ -maxdepth 1 -type d -name 'build-*' -mtime +30 -exec rm -rf {} \;
find $WORKSPACE/ -maxdepth 1 -type f -name 'test_suite_report_*.tar.bz2' -mtime +30 -delete
