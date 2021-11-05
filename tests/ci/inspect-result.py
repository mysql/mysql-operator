# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from pathlib import Path
import re
import sys

if len(sys.argv) != 3:
    print("usage: <expected-failues-path> <log-path>")
    sys.exit(1)

expected_failures_path = sys.argv[1]
log_path = sys.argv[2]

if not Path(log_path).is_file():
    print(f"error: test log {log_path} not found")
    sys.exit(2)


success = None
summary = None

# Ran 4 tests in 212.127s
# Ran 4 tests in 205.962s
# Ran 9 tests in 288.074s
execution_summary_matcher = re.compile('^Ran \d+ tests in \d+\.\d+\w$')

# FAILED (failures=3)
# FAILED (errors=1)
# FAILED (failures=2, errors=2)
# FAILED (failures=8, errors=4, skipped=1)
failed_summary_matcher = re.compile('^FAILED \([a-z=0-9, ]*\)$')

# OK
success_summary_matcher = re.compile('^OK$')

# regex to match issues (errors and failures), e.g.:
# FAIL: test_4_recover_restart_3_of_3 (e2e.mysqloperator.cluster.cluster_t.Cluster3Defaults)
# ERROR: tearDownClass (e2e.mysqloperator.cluster.cluster_t.Cluster3Defaults)
issue_matcher = re.compile('^(FAIL|ERROR): \w+ \([^)]*\)$')

with open(expected_failures_path) as f:
    expected_failures = set(f.read().splitlines())

unexpected_failures = []
with open(log_path) as f:
    for line in f:
        line = line.rstrip()
        if issue_matcher.match(line):
            if line in expected_failures:
                expected_failures.remove(line)
            else:
                unexpected_failures.append(line)
        elif execution_summary_matcher.match(line):
            summary = line
        elif failed_summary_matcher.match(line):
            success = False
            summary += ' ' + line
        elif success_summary_matcher.match(line):
            success = True
            summary += ' ' + line


if expected_failures:
    print("--------------- expected failures that didn't happen ---------------")
    for failure_not_met in expected_failures:
        print(failure_not_met)

if unexpected_failures:
    print("------------------------ unexpected failures -----------------------")
    for unexpected_failure in unexpected_failures:
        print(unexpected_failure)
    success = False

# treat a build with the expected failures only as successful
if not success and summary and not unexpected_failures:
    success = True

print("------------------------------ summary -----------------------------")
if summary:
    print(summary)
else:
    print("warning: a summary not found in the log, the execution could be stopped before completion")

if not success:
    sys.exit(3)
