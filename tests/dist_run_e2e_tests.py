#!/usr/bin/python3
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import shutil
import multiprocessing
import tempfile
import time
from unittest.util import strclass
from run_e2e_tests import load_test_suite, parse_filter
import os
import sys

class DistTestSuiteRunner:
    base_dir = os.path.dirname(os.path.abspath(__file__))
    work_dir = None

    env_name = "minikube"
    tag = "ote-mysql"
    default_worker_count = 2
    defer_worker_start = 0
    sort_cases = False
    perform_purge = False
    pattern_include = []
    pattern_exclude = []

    worker_argv = []

    def __init__(self, argv):
        os.chdir(self.base_dir)

        for arg in argv:
            if arg.startswith("--env="):
                self.env_name = arg.partition("=")[-1]
                self.worker_argv.append(arg)
            elif arg.startswith("--tag="):
                self.tag = arg.partition("=")[-1]
            elif arg.startswith("--workers="):
                self.default_worker_count = int(arg.split("=")[-1])
            elif arg.startswith("--defer"):
                self.defer_worker_start = int(arg.split("=")[-1])
            elif arg.startswith("--sort"):
                self.sort_cases = True
            elif arg.startswith("--purge"):
                self.perform_purge = True
            elif arg.startswith("-"):
                self.worker_argv.append(arg)
            else:
                inc, exc = parse_filter(arg)
                self.pattern_include += inc
                self.pattern_exclude += exc

    def prepare_test_suite(self):
        suites = load_test_suite(self.base_dir, self.pattern_include, self.pattern_exclude)
        if not suites or suites.countTestCases() == 0:
            return None

        testset = set()
        for suite in suites:
            for subtest in suite:
                testset.add(strclass(subtest.__class__))

        test_suite = list(testset)
        if self.sort_cases:
            test_suite.sort()
        return test_suite

    def divide_test_suite(self, test_suite):
        tests_count = len(test_suite)
        tests_per_worker, tests_remainder = divmod(tests_count, self.default_worker_count)

        worker_index = 0
        test_index = 0
        portions = []
        while worker_index < self.default_worker_count and test_index < tests_count:
            begin = test_index
            end = begin + tests_per_worker
            if worker_index < tests_remainder:
                end += 1
            portion = test_suite[begin:end]
            portions.append(portion)
            worker_index += 1
            test_index = end

        return portions

    def get_worker_cluster(self, worker_index):
        if self.env_name == "k3d":
            prefix = ""
        else:
            prefix = f"{self.env_name}-"
        return f"{prefix}{self.tag}-{worker_index}"

    def get_worker_portion_path(self, worker_index):
        return os.path.join(self.work_dir, f"suite-{worker_index}.txt")

    def get_worker_log_path(self, worker_index):
        return os.path.join(self.work_dir, f"worker-{worker_index}.log")

    def print_file(self, path):
        try:
            print(f"source: {path}")
            with open(path, "r") as f:
                shutil.copyfileobj(f, sys.stdout)
        except BaseException as err:
            print(err)

    def store_worker_portion(self, path, portion):
        with open(path, 'w') as f:
            for test_case in portion:
                f.write('%s\n' % test_case)

    def prepare_worker_data(self, worker_index, portion):
        cluster = self.get_worker_cluster(worker_index)

        portion_path = self.get_worker_portion_path(worker_index)
        self.store_worker_portion(portion_path, portion)
        print(f"worker {worker_index} portion stored in {portion_path}")

        log_path = self.get_worker_log_path(worker_index)

        cmd_line = f"./run {' '.join(self.worker_argv)} --cluster={cluster} --suite={portion_path} > {log_path} 2>&1"
        return cmd_line

    def run_worker(self, worker_index, cmd_line):
        os.system(cmd_line)
        print(f"###### worker {worker_index} completed")

    def run_workers(self, portions):
        workers = []

        worker_index = 0
        for portion in portions:
            if self.defer_worker_start > 0 and worker_index != 0:
                time.sleep(self.defer_worker_start)
            print(f"------ starting worker {worker_index}...")
            cmd_line = self.prepare_worker_data(worker_index, portion)
            print(cmd_line)

            worker = multiprocessing.Process(target=self.run_worker, args=(worker_index, cmd_line))
            worker.start()
            workers.append(worker)
            worker_index += 1

        print("waiting for workers...\n")
        for worker in workers:
            worker.join()


    def print_logs(self, worker_count):
        for i in range(worker_count):
            cluster = self.get_worker_cluster(i)
            print(f"worker {i}, cluster: {cluster}")

            portion_path = self.get_worker_portion_path(i)
            self.print_file(portion_path)

            log_path = self.get_worker_log_path(i)
            self.print_file(log_path)

    def purge(self):
        shutil.rmtree(self.work_dir)

    def run(self):
        test_suite = self.prepare_test_suite()
        if not test_suite or len(test_suite) == 0:
            print("No tests matched")
            return

        portions = self.divide_test_suite(test_suite)

        work_dir_prefix = f"{self.tag}-{self.env_name}-"
        self.work_dir = tempfile.mkdtemp(prefix=work_dir_prefix)
        self.run_workers(portions)

        self.print_logs(len(portions))

        if self.perform_purge:
            self.purge()


test_suite_runner = DistTestSuiteRunner(sys.argv[1:])
test_suite_runner.run()
