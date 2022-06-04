#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
#!/bin/bash
set -vx

source $WORKSPACE/tests/ci/job_aux/job-env.sh

LOG_DIR=$WORKSPACE/build-$BUILD_NUMBER
if test -d ${LOG_DIR}; then
	rm -rfd $LOG_DIR
fi
mkdir -p $LOG_DIR

JOB_RESULT_PATH=$LOG_DIR/job_test_result.json
curl -X GET -u $JENKINS_USER_CRED ${TEST_RESULT_BUILD_URL}/testReport/api/json?pretty > $JOB_RESULT_PATH

TEST_SUITE_REPORT_FNAME=test_suite_report.txt
TEST_SUITE_REPORT_PATH=$LOG_DIR/$TEST_SUITE_REPORT_FNAME
"$WORKSPACE/tests/ci/job_aux/parse_job_test_result.py" $JOB_RESULT_PATH > $TEST_SUITE_REPORT_PATH
cat $TEST_SUITE_REPORT_PATH

cd $LOG_DIR
tar cvjf ../test_suite_report_$BUILD_NUMBER.tar.bz2 $TEST_SUITE_REPORT_FNAME
