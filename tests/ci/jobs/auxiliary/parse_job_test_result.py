#!/usr/bin/python3
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# process and print the test suite report based on the results collected by junit plugin
# curl -X GET -u $JENKINS_USER_CRED '${TEST_JOB}/191/testReport/api/json?pretty=true'
# to get cases only e.g.:
# curl -X GET -u $JENKINS_USER_CRED '${TEST_JOB}/191/testReport/api/json?pretty=true&tree=suites\[cases\[className,name,age,status,duration\]\]'

from datetime import timedelta
import json
import sys
from utils import auxutil

class TestSuiteResult:
	def __init__(self):
		self.empty = True
		self.duration = None
		self.passed_count = 0
		self.failed_count = 0
		self.skipped_count = 0
		self.failures = []
		self.skipped = []

	def tests_count(self):
		return self.passed_count + self.failed_count + self.skipped_count

# -----------------------------------------------------------------------------

class ProcessJobTestResult:
	def __init__(self):
		self.ts_report = TestSuiteResult()

	def process_stats(self, job_test_result):
		# "duration" : 7.881,
		# "empty" : false,
		# "failCount" : 2,
		# "passCount" : 8,
		# "skipCount" : 3,
		self.ts_report.duration = job_test_result['duration']
		self.ts_report.passed_count = job_test_result['passCount']
		self.ts_report.failed_count = job_test_result['failCount']
		self.ts_report.skipped_count = job_test_result['skipCount']

	def get_test_case_info(self, test_case):
		return f"{test_case['className']}.{test_case['name']} [{test_case['duration']}s]"

	def process_suite(self, suite):
		test_cases = suite['cases']
		for test_case in test_cases:
			test_status = test_case['status']
			if test_status == 'FAILED':
				self.ts_report.failures.append(self.get_test_case_info(test_case))
			elif test_status == 'SKIPPED':
				self.ts_report.skipped.append(self.get_test_case_info(test_case))

	def process_suites(self, job_test_result):
		test_suites = job_test_result['suites']
		for test_suite in test_suites:
			self.process_suite(test_suite)

	def run(self, job_test_result):
		self.ts_report.empty = job_test_result.get('empty', True)
		if not self.ts_report.empty:
			self.process_stats(job_test_result)
			self.process_suites(job_test_result)
		return self.ts_report

# -----------------------------------------------------------------------------

class PrintTestSuiteReport:
	def print_stats(self, ts_result):
		print((f"{ts_result.tests_count()} tests, " +
			f"{ts_result.passed_count} passed, " +
			f"{ts_result.failed_count} failed, " +
			f"{ts_result.skipped_count} skipped"))
		print(f"Total execution time (all tests): {self.prepare_duration(ts_result.duration)}")

	def prepare_duration(self, duration_sec):
		if not duration_sec:
			return "no time"

		td = timedelta(seconds = duration_sec)
		return auxutil.get_formatted_duration(td)

	def print_items(self, kind, items):
		if items:
			items.sort()
			print(f"\n{len(items)} {kind} test(s):")
			for item in items:
				print(f"{item}")

	def run(self, test_suite_result):
		if not test_suite_result.empty:
			self.print_stats(test_suite_result)

			self.print_items("failed", test_suite_result.failures)
			self.print_items("skipped", test_suite_result.skipped)

# -----------------------------------------------------------------------------

if len(sys.argv) != 2:
	print("usage: <path_to_job_result_in_json_format>")
	sys.exit(1)

junit_result_path = sys.argv[1]
with open(junit_result_path, 'r') as f:
	job_test_result = json.load(f)

process_test_suite_result = ProcessJobTestResult()
test_suite_result = process_test_suite_result.run(job_test_result)

print_test_suite_report = PrintTestSuiteReport()
print_test_suite_report.run(test_suite_result)
