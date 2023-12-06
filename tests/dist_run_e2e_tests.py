#!/usr/bin/python3
# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from datetime import timedelta
import shutil
import multiprocessing
import tempfile
import time
from run_e2e_tests import parse_filter
import os
import sys
from ci.jobs.auxiliary import process_workers_logs
from utils import testsuite

class DistTestSuiteRunner:
	def __init__(self):
		self.base_dir = os.path.dirname(os.path.abspath(__file__))
		self.work_dir = None
		self.work_dir_is_tmp = False
		self.workers_subdir = "workers"
		self.xml_subdir = "xml"

		self.env_name = "minikube"
		self.tag = "ote-mysql"
		self.max_worker_count = 2
		self.defer_worker_start = 60
		self.sort_cases = False
		self.expected_failures_path = None
		self.generate_xml = False
		self.perform_purge = False
		self.custom_suite_path = None
		self.pattern_include = []
		self.pattern_exclude = []

		self.worker_argv = []

	def __del__(self):
		if self.perform_purge or self.work_dir_is_tmp:
			shutil.rmtree(self.work_dir)

	def parse_cmdline(self, argv):
		os.chdir(self.base_dir)

		for arg in argv:
			if arg.startswith("--env="):
				self.env_name = arg.partition("=")[-1]
				self.worker_argv.append(arg)
			elif arg.startswith("--tag="):
				self.tag = arg.partition("=")[-1]
			elif arg.startswith("--clusters="):
				self.max_worker_count = int(arg.split("=")[-1])
			elif arg.startswith("--defer="):
				self.defer_worker_start = int(arg.split("=")[-1])
			elif arg == "--sort":
				self.sort_cases = True
			elif arg.startswith("--expected-failures="):
				self.expected_failures_path = arg.split("=")[-1]
			elif arg == "--xml":
				self.generate_xml = True
			elif arg.startswith("--work-dir=") or arg.startswith("--workdir="):
				self.work_dir = arg.split("=")[-1]
			elif arg == "--purge":
				self.perform_purge = True
			elif arg.startswith("--suite="):
				self.custom_suite_path = arg.split("=")[-1]
			elif arg.startswith("-"):
				self.worker_argv.append(arg)
			else:
				inc, exc = parse_filter(arg)
				self.pattern_include += inc
				self.pattern_exclude += exc

	def prepare_test_portions_for_workers(self):
		if self.custom_suite_path:
			with open(self.custom_suite_path, 'r') as f:
				self.pattern_include += f.read().splitlines()

		test_suite = testsuite.prepare_test_suite(self.base_dir, self.pattern_include, self.pattern_exclude, self.sort_cases)
		if not test_suite or len(test_suite) == 0:
			return None

		return testsuite.divide_test_suite(test_suite, self.max_worker_count)

	def ensure_dir_exists(self, dir):
		if not os.path.exists(dir):
			os.makedirs(dir)

		if not os.path.isdir(dir):
			print(f"path {dir} is not dir")
			sys.exit(5)

		if any(os.scandir(dir)):
			print(f"warning: dir {dir} is not empty")

	def prepare_work_dir(self):
		# if work dir is not explicitly pointed out, then prepare a tmp dir
		if not self.work_dir:
			work_dir_prefix = f"{self.tag}-env-{self.env_name}-"
			self.work_dir = tempfile.mkdtemp(prefix=work_dir_prefix)
			self.work_dir_is_tmp = True
		self.ensure_dir_exists(self.work_dir)

	def prepare_work_subdir(self, subdir):
		work_subdir = os.path.join(self.work_dir, subdir)
		self.ensure_dir_exists(work_subdir)

	def prepare_workspace(self):
		self.prepare_work_dir()
		self.prepare_work_subdir(self.workers_subdir)
		if self.generate_xml:
			self.prepare_work_subdir(self.xml_subdir)

	def get_worker_cluster(self, worker_index):
		if self.env_name in ["k3d", "kind"]:
			prefix = ""
		else:
			prefix = f"{self.env_name}-"
		return f"{prefix}{self.tag}-{worker_index}"

	def get_worker_path(self, subdir, filename):
		return os.path.join(self.work_dir, subdir, f"{self.tag}-{filename}")

	def get_worker_portion_path(self, worker_index):
		return self.get_worker_path(self.workers_subdir, f"suite-{worker_index}.txt")

	def get_worker_log_path(self, worker_index):
		return self.get_worker_path(self.workers_subdir, f"worker-{worker_index}.log")

	def get_worker_xml_path(self, worker_index):
		return self.get_worker_path(self.xml_subdir, f"worker-{worker_index}.xml")

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

		argv = ["./run", f"--cluster={cluster}", f"--suite={portion_path}"]
		if self.work_dir:
			argv.append(f"--work-dir={self.work_dir}")
		argv.extend(self.worker_argv)
		if self.generate_xml:
			xml_path = self.get_worker_xml_path(worker_index)
			argv.append(f"--xml={xml_path}")

		cmd_line = f"{' '.join(argv)} > {log_path} 2>&1"
		return cmd_line

	def run_worker(self, worker_index, cmd_line):
		os.system(cmd_line)
		print(f"###### worker {worker_index} completed")

	def run_workers(self, portions):
		start = time.time()
		workers = []

		print("---------------------------------")
		print(f"workers dir: {self.work_dir}")

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

		end = time.time()
		return timedelta(seconds = end - start)

	def print_logs(self, worker_count):
		for i in range(worker_count):
			cluster = self.get_worker_cluster(i)
			print(f"==========================================")
			print(f"########## worker {i}, cluster: {cluster}")

			portion_path = self.get_worker_portion_path(i)
			self.print_file(portion_path)

			log_path = self.get_worker_log_path(i)
			self.print_file(log_path)

	def process_result(self, worker_count, execution_time):
		log_paths = []
		for i in range(worker_count):
			log_path = self.get_worker_log_path(i)
			log_paths.append(log_path)

		return process_workers_logs.run(self.expected_failures_path, log_paths, execution_time)

	def run(self, argv):
		self.parse_cmdline(argv)

		portions = self.prepare_test_portions_for_workers()
		if not portions:
			print("No tests matched")
			return

		self.prepare_workspace()

		execution_time = self.run_workers(portions)

		worker_count = len(portions)

		self.print_logs(worker_count)

		result = self.process_result(worker_count, execution_time)

		return result


test_suite_runner = DistTestSuiteRunner()
if not test_suite_runner.run(sys.argv[1:]):
	sys.exit(6)
