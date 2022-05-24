# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from datetime import timedelta
import os
from pathlib import Path
import re
import time

class LogResult:
    def __init__(self):
        self.execution_time = None
        self.tests = 0
        self.failures = 0
        self.errors = 0
        self.skipped = 0
        self.issues = []
        self.summary = None
        self.runtime_error_msg = ''

# =============================================================================

class LogParser:
    # Ran 4 tests in 212.127s
    # Ran 4 tests in 205.962s
    # Ran 9 tests in 288.074s
    # Ran 1 test in 210.129s
    execution_summary_matcher = re.compile('^Ran (\d+) tests? in (\d+\.\d+)(\w)$')

    # FAILED (failures=3)
    # FAILED (errors=1)
    # FAILED (failures=2, errors=2)
    # FAILED (failures=8, errors=4, skipped=1)
    failed_summary_matcher = re.compile('^FAILED \(([a-z=0-9, ]*)\)$')

    # 'failures=3'
    # 'errors=1'
    # 'skipped=2'
    failure_stat_matcher = re.compile('^\s*(\w+)=(\d+)\s*$')

    # OK
    success_summary_matcher = re.compile('^OK$')

    # regex to match issues (errors and failures), e.g.:
    # FAIL: test_4_recover_restart_3_of_3 (e2e.mysqloperator.cluster.cluster_t.Cluster3Defaults)
    # ERROR: tearDownClass (e2e.mysqloperator.cluster.cluster_t.Cluster3Defaults)
    # FAIL [42.676s]: test_2_modify_ssl_certs (e2e.mysqloperator.cluster.cluster_ssl_t.ClusterSSL)
    # ERROR [54.306s]: test_1_create_cluster_missing_ssl_recover (e2e.mysqloperator.cluster.cluster_ssl_t.ClusterNoSSL)
    issue_matcher = re.compile('^(FAIL|ERROR)( \[[\w\d.]+\])?: \w+ \([^)]*\)$')

    def __init__(self):
        self.result = LogResult()

    def add_error(self, msg):
        if self.result.runtime_error_msg:
            self.result.runtime_error_msg += ';'
        self.result.runtime_error_msg += msg

    # 212.127s
    # 205.962s
    # 288.074s
    def process_execution_time(self, period, time_unit):
        if time_unit == 's':
            factor = 1000.0
        else:
            factor = 1000.0
            self.add_error(f"unknown time unit {time_unit}")
        ms = int(factor * float(period))
        self.result.execution_time = timedelta(milliseconds = ms)

    # 'failures=3'
    # 'errors=1'
    # 'failures=2, errors=2'
    # 'failures=8, errors=4, skipped=1'
    def process_failure_stats(self, raw_stats):
        stats = raw_stats.split(',')
        for stat in stats:
            m = self.failure_stat_matcher.match(stat)
            if not m:
                self.add_error(f"unknown kind of issue {stat}")

            kind = m.group(1)
            counter = m.group(2)

            if kind == 'failures':
                self.result.failures = int(counter)
            elif kind == 'errors':
                self.result.errors = int(counter)
            elif kind == 'skipped':
                self.result.skipped = int(counter)


    def run(self, log_path):
        if not Path(log_path).is_file():
            self.add_error(f"error: test log {log_path} not found")
            return self.result

        with open(log_path) as f:
            for line in f:
                line = line.rstrip()

                if self.issue_matcher.match(line):
                    self.result.issues.append(line)
                    continue

                m = self.execution_summary_matcher.match(line)
                if m:
                    self.result.tests = int(m.group(1))
                    self.process_execution_time(m.group(2), m.group(3))
                    self.result.summary = line
                    continue

                m = self.failed_summary_matcher.match(line)
                if m:
                    self.process_failure_stats(m.group(1))
                    self.result.summary += ' ' + line
                    continue

                m = self.success_summary_matcher.match(line)
                if m:
                    self.result.summary += ' ' + line

        if not self.result.summary:
            self.add_error("error: a summary not found in the log, the execution could be stopped before completion")

        return self.result

# =============================================================================

class TotalSummary:
    def __init__(self):
        self.execution_time = None
        self.total_workers_time = timedelta()
        self.workers_finished = 0
        self.workers_broken = 0
        self.tests = 0
        self.failures = 0
        self.errors = 0
        self.skipped = 0
        self.expected_failures = []
        self.unexpected_failures = []
        self.success = True

# =============================================================================

class ResultAggregator:
    def __init__(self):
        self.results = []
        self.total_summary = TotalSummary()

    def read_expected_failures(self, expected_failures_path):
        with open(expected_failures_path) as f:
            self.total_summary.expected_failures = set(f.read().splitlines())

    def add(self, log_path):
        log_parser = LogParser()
        log_result = log_parser.run(log_path)
        self.results.append(log_result)

    def process_worker_result(self, result):
        if result.execution_time:
            self.total_summary.total_workers_time += result.execution_time
        self.total_summary.tests += result.tests
        self.total_summary.failures += result.failures
        self.total_summary.errors += result.errors
        self.total_summary.skipped += result.skipped

        for issue in result.issues:
            if issue in self.total_summary.expected_failures:
                self.total_summary.expected_failures.remove(issue)
            else:
                self.total_summary.unexpected_failures.append(issue)

        if result.summary:
            self.total_summary.workers_finished += 1
        else:
            self.total_summary.workers_broken += 1
            self.total_summary.success = False

    def process_worker_results(self):
        for worker_result in self.results:
            self.process_worker_result(worker_result)

        if self.total_summary.unexpected_failures:
            self.total_summary.success = False

    def run(self, expected_failures_path, log_paths, execution_time):
        self.read_expected_failures(expected_failures_path)

        for log_path in log_paths:
            self.add(log_path)
        self.process_worker_results()

        if execution_time:
            self.total_summary.execution_time = execution_time
        elif len(self.results) > 0:
            self.total_summary.execution_time = self.results[0].execution_time

# =============================================================================

class ResultPrinter:
    def print_failures(self, summary):
        if summary.expected_failures:
            print("--------------- expected failures that didn't happen ---------------")
            print(f"quantity: {len(summary.expected_failures)}")
            for failure_not_met in summary.expected_failures:
                print(failure_not_met)

        if summary.unexpected_failures:
            print("------------------------ unexpected failures -----------------------")
            print(f"quantity: {len(summary.unexpected_failures)}")
            for unexpected_failure in summary.unexpected_failures:
                print(unexpected_failure)

    def prepare_worker_summary(self, worker_result):
        summary = worker_result.summary
        if summary:
            if worker_result.runtime_error_msg:
                summary += ';' + worker_result.runtime_error_msg
        else:
            summary = worker_result.runtime_error_msg
        return summary

    def print_worker_summary(self, index, worker_result):
        summary = self.prepare_worker_summary(worker_result)
        print(f"worker {index}: {summary}")

    def print_worker_results(self, worker_results, summary):
        print("------------------------------ workers -----------------------------")
        print(f"all      : {summary.workers_finished + summary.workers_broken}")
        print(f"completed: {summary.workers_finished}")
        print(f"broken   : {summary.workers_broken}")
        print("----------")

        index = 0
        for worker_result in worker_results:
            self.print_worker_summary(index, worker_result)
            index += 1

    def prepare_time_brief(self, tm):
        if tm:
            return f"{tm} ({tm.total_seconds()}s)"
        else:
            return "no time"

    def print_summary(self, summary):
        print("------------------------------ summary -----------------------------")
        print(f"execution time    : {self.prepare_time_brief(summary.execution_time)}")
        print(f"total workers time: {self.prepare_time_brief(summary.total_workers_time)}")
        print("-------------------")
        print(f"tests   : {summary.tests}")
        print(f"failures: {summary.failures}")
        print(f"errors  : {summary.errors}")
        print(f"skipped : {summary.skipped}")
        print(f"success : {summary.success}")


    def run(self, worker_results, total_summary):
        self.print_failures(total_summary)
        self.print_worker_results(worker_results, total_summary)
        self.print_summary(total_summary)

# =============================================================================

def run(expected_failures_path, log_paths, execution_time):
    aggregator = ResultAggregator()
    aggregator.run(expected_failures_path, log_paths, execution_time)

    printer = ResultPrinter()
    printer.run(aggregator.results, aggregator.total_summary)

    return aggregator.total_summary.success
