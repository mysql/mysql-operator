#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# splits the test suite report into parts (summary, also list of failures, and skipped cases if there are any)
# so they can be presented in a slack message as separated sections, and easily (un)folded by user in case there
# are plenty of items to show
set -vx

TEST_SUITE_REPORT_PATH=$1
TEST_SUITE_REPORT_DIR=$(dirname $(readlink -f "${TEST_SUITE_REPORT_PATH}"))

if [[ ! -f $TEST_SUITE_REPORT_PATH ]]; then
    echo "file $TEST_SUITE_REPORT_PATH not found"
    exit
fi

cd ${TEST_SUITE_REPORT_DIR}

csplit \
    --quiet \
    --prefix=${TEST_SUITE_REPORT_DIR}/test_suite_report_part_ \
    --suffix-format=%01d.txt \
    --suppress-matched \
    --elide-empty-files \
    ${TEST_SUITE_REPORT_PATH} /^$/ {*}

function recognize_test_suite_report_part() {
	FILE_PATH=$1
    if grep -q 'Total execution time (all tests): ' "$FILE_PATH"; then
        echo 'test_suite_report_summary.txt'
    elif grep -q 'failed test(s):' "$FILE_PATH"; then
        echo 'test_suite_report_failures.txt'
    elif grep -q 'skipped test(s):' "$FILE_PATH"; then
        echo 'test_suite_report_skipped.txt'
    fi
}

function trim_test_suite_report_part() {
    SRC_FILE_PATH=$1
    DEST_FILE_PATH=$2
    REPORTED_LINES_MAX_COUNT=40
	cat $SRC_FILE_PATH | sed -ne "1,${REPORTED_LINES_MAX_COUNT} p" -e "$((REPORTED_LINES_MAX_COUNT+1)) iand more..." > $DEST_FILE_PATH
}

for i in {0..2}; do
    SRC_FILE_PATH="$TEST_SUITE_REPORT_DIR/test_suite_report_part_${i}.txt"
    if [[ ! -f $SRC_FILE_PATH ]]; then
        break
    fi
    DEST_FILE_PATH="$TEST_SUITE_REPORT_DIR/$(recognize_test_suite_report_part $SRC_FILE_PATH)"
    trim_test_suite_report_part "$SRC_FILE_PATH" "$DEST_FILE_PATH"
    rm "$SRC_FILE_PATH"
done
