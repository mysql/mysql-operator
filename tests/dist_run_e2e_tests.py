#!/usr/bin/python3
# Copyright (c) 2022, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from datetime import timedelta
import multiprocessing
import os
import queue
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from ci.jobs.auxiliary import process_workers_logs
from run_e2e_tests import parse_filter
from utils import fmt, testsuite

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
		self.cleanup_workers = True
		self.queue_suite_path = None
		self.result_events = []
		self.result_log_paths = []
		self.result_log_labels = []
		self.task_names = {}
		self.worker_exitcodes = {}
		self.scheduled_task_ids = set()
		self.result_task_ids = []
		self.total_test_count = 0
		self.status_line_visible = False
		self.multi_node_worker_index = 0
		self.multi_node_cluster_node_count = 3

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
			elif arg.startswith("--nodes="):
				self.multi_node_cluster_node_count = int(arg.split("=")[-1])
				self.worker_argv.append(arg)
			elif arg in ("--noclean", "--no-clean", "--nosetup", "--no-setup"):
				self.cleanup_workers = False
				self.worker_argv.append(arg)
			elif arg.startswith("-"):
				self.worker_argv.append(arg)
			else:
				inc, exc = parse_filter(arg)
				self.pattern_include += inc
				self.pattern_exclude += exc

	def prepare_test_queue(self):
		if self.custom_suite_path:
			with open(self.custom_suite_path, 'r') as f:
				self.pattern_include += f.read().splitlines()

		test_suite = testsuite.prepare_test_suite(self.base_dir, self.pattern_include, self.pattern_exclude, self.sort_cases)
		if not test_suite or len(test_suite) == 0:
			return None

		return test_suite

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

	def get_queue_suite_path(self):
		return self.get_worker_path(self.workers_subdir, "suite-queue.txt")

	def get_task_suite_path(self, task_index):
		return self.get_worker_path(self.workers_subdir, f"suite-task-{task_index:04}.txt")

	def get_worker_log_path(self, worker_index):
		return self.get_worker_path(self.workers_subdir, f"worker-{worker_index}.log")

	def get_task_log_path(self, worker_index, task_index):
		return self.get_worker_path(self.workers_subdir, f"worker-{worker_index}-task-{task_index:04}.log")

	def get_task_xml_path(self, worker_index, task_index):
		return self.get_worker_path(self.xml_subdir, f"worker-{worker_index}-task-{task_index:04}.xml")

	def color_test_result_line(self, line):
		if not line.startswith("ending test "):
			return line

		_, separator, rc_text = line.rpartition(": rc=")
		if not separator:
			return line

		try:
			rc = int(rc_text)
		except ValueError:
			return line

		color = fmt.green if rc == 0 else fmt.red
		return color(line)

	def get_result_color(self, rc):
		return fmt.green if rc == 0 else fmt.red

	def clear_status_line(self):
		if not self.status_line_visible:
			return
		print("\r\033[K", end="", flush=True)
		self.status_line_visible = False

	def format_running_status(self, running_tests):
		if not running_tests:
			return ""

		running = [
			test_name for _, test_name in sorted(running_tests.items())
		]
		line = " | ".join(running)
		terminal_width = shutil.get_terminal_size((120, 20)).columns
		if len(line) > terminal_width:
			return line[:max(0, terminal_width - 3)] + "..."
		return line

	def draw_status_line(self, running_tests):
		status = self.format_running_status(running_tests)
		if not status:
			self.clear_status_line()
			return

		print(f"\r\033[K{status}", end="", flush=True)
		self.status_line_visible = True

	def live_print(self, message, running_tests=None, color=None):
		self.clear_status_line()
		print(color(message) if color else message, flush=True)
		if running_tests is not None:
			self.draw_status_line(running_tests)

	def print_file(self, path, colorize_test_results=False):
		try:
			print(f"source: {path}")
			with open(path, "r") as f:
				if not colorize_test_results:
					shutil.copyfileobj(f, sys.stdout)
					return

				for line in f:
					print(self.color_test_result_line(line.rstrip("\n")))
		except BaseException as err:
			print(err)

	def store_suite(self, path, portion):
		with open(path, 'w') as f:
			for test_case in portion:
				f.write('%s\n' % test_case)

	def get_worker_env(self, worker_index=None):
		repo_dir = os.path.dirname(self.base_dir)
		env = os.environ.copy()
		env["TESTPOD_NAME"] = "testpod"
		env["NAMESPACE"] = "mysql-operator"
		env.setdefault("OPERATOR_TEST_HELM_PATH", os.path.join(repo_dir, "helm"))
		if worker_index == self.multi_node_worker_index:
			env["OPERATOR_TEST_CLUSTER_NODE_COUNT"] = str(self.multi_node_cluster_node_count)

		pythonpath_entries = [repo_dir, self.base_dir]
		if env.get("PYTHONPATH"):
			pythonpath_entries.extend(env["PYTHONPATH"].split(os.pathsep))
		env["PYTHONPATH"] = os.pathsep.join(pythonpath_entries)
		return env

	def worker_has_arg(self, *arg_names):
		return any(arg in arg_names for arg in self.worker_argv)

	def get_runner_argv(self, command, cluster, suite_path, xml_path=None, force_noclean=False):
		argv = [
			sys.executable,
			os.path.join(self.base_dir, "run_e2e_tests.py"),
			command,
			f"--cluster={cluster}",
			f"--suite={suite_path}"
		]
		if self.work_dir:
			argv.append(f"--work-dir={self.work_dir}")
		argv.extend(self.worker_argv)
		if force_noclean and not self.worker_has_arg("--noclean", "--no-clean"):
			argv.append("--noclean")
		if xml_path:
			argv.append(f"--xml={xml_path}")
		return argv

	def run_command(self, argv, log_path, mode="w", worker_index=None):
		with open(log_path, mode) as log:
			print(f"$ {shlex.join(argv)}", file=log)
			log.flush()
			try:
				rc = subprocess.call(
					argv,
					cwd=self.base_dir,
					env=self.get_worker_env(worker_index),
					stdout=log,
					stderr=subprocess.STDOUT,
				)
			except BaseException as err:
				print(f"ERROR: failed to run command: {err}", file=log)
				rc = 127
			print(f"Tests finished. rc={rc}", file=log)
			return rc

	def append_worker_log(self, worker_index, message):
		with open(self.get_worker_log_path(worker_index), "a") as log:
			print(message, file=log)

	def format_test_progress(self, task_index, total_count, test_case):
		return f"test {task_index + 1}/{total_count}: {test_case}"

	def format_status_test_name(self, test_case):
		test_parts = test_case.rsplit(".", 2)
		if len(test_parts) >= 2 and test_parts[-1].startswith("test"):
			return ".".join(test_parts[-2:])
		return test_case.rsplit(".", 1)[-1]

	def log_and_emit_worker_progress(self, worker_index, progress_queue, kind, task_index, progress, rc=None):
		if kind == "start":
			message = f"starting {progress} on worker {worker_index}"
		else:
			message = f"ending {progress} on worker {worker_index}: rc={rc}"

		self.append_worker_log(worker_index, message)
		progress_queue.put((kind, worker_index, task_index, progress, rc))

	def run_worker_setup(self, worker_index):
		cluster = self.get_worker_cluster(worker_index)
		log_path = self.get_worker_log_path(worker_index)
		with open(log_path, "w") as log:
			print(f"worker {worker_index}, cluster: {cluster}", file=log)
			print("setting up worker cluster", file=log)
		argv = self.get_runner_argv("setup", cluster, self.queue_suite_path, force_noclean=True)
		return self.run_command(argv, log_path, "a", worker_index=worker_index)

	def run_worker_cleanup(self, worker_index):
		if not self.cleanup_workers:
			return 0
		cluster = self.get_worker_cluster(worker_index)
		self.append_worker_log(worker_index, "cleaning up worker cluster")
		argv = self.get_runner_argv("clean", cluster, self.queue_suite_path)
		return self.run_command(argv, self.get_worker_log_path(worker_index), "a", worker_index=worker_index)

	def run_worker_task(self, worker_index, task_index, total_count, test_case, progress_queue):
		cluster = self.get_worker_cluster(worker_index)

		suite_path = self.get_task_suite_path(task_index)
		self.store_suite(suite_path, [test_case])

		log_path = self.get_task_log_path(worker_index, task_index)
		xml_path = self.get_task_xml_path(worker_index, task_index) if self.generate_xml else None

		progress = self.format_test_progress(task_index, total_count, test_case)
		self.log_and_emit_worker_progress(worker_index, progress_queue, "start", task_index, progress)
		argv = self.get_runner_argv("test", cluster, suite_path, xml_path)
		rc = self.run_command(argv, log_path, worker_index=worker_index)
		self.log_and_emit_worker_progress(worker_index, progress_queue, "end", task_index, progress, rc)
		return log_path, rc

	def run_worker_task_and_report(self, worker_index, task, result_queue, progress_queue):
		task_index, total_count, test_case = task
		try:
			log_path, rc = self.run_worker_task(worker_index, task_index, total_count, test_case, progress_queue)
		except BaseException as err:
			log_path = self.get_task_log_path(worker_index, task_index)
			with open(log_path, "a") as log:
				print(f"ERROR: worker {worker_index} failed while running task {task_index}: {err}", file=log)
			rc = 127
			progress = self.format_test_progress(task_index, total_count, test_case)
			self.log_and_emit_worker_progress(worker_index, progress_queue, "end", task_index, progress, rc)
		result_queue.put(("task", worker_index, task_index, log_path, rc))

	def run_worker(self, worker_index, reserved_tasks, work_queue, result_queue, progress_queue, setup_done_events):
		if worker_index > 0:
			setup_done_events[worker_index - 1].wait()

		try:
			setup_rc = self.run_worker_setup(worker_index)
		except BaseException as err:
			log_path = self.get_worker_log_path(worker_index)
			with open(log_path, "a") as log:
				print(f"ERROR: worker {worker_index} failed while setting up: {err}", file=log)
			setup_rc = 127
		finally:
			setup_done_events[worker_index].set()

		if setup_rc != 0:
			progress_queue.put(("worker_error", worker_index, -1, "setup", setup_rc))
			if self.cleanup_workers:
				self.run_worker_cleanup(worker_index)
			result_queue.put(("worker", worker_index, -1, self.get_worker_log_path(worker_index), setup_rc))
			return

		setup_done_events[-1].wait()

		for task in reserved_tasks:
			self.run_worker_task_and_report(worker_index, task, result_queue, progress_queue)

		while True:
			task = work_queue.get()
			if task is None:
				break

			self.run_worker_task_and_report(worker_index, task, result_queue, progress_queue)

		cleanup_rc = self.run_worker_cleanup(worker_index)
		if cleanup_rc != 0:
			progress_queue.put(("worker_error", worker_index, -1, "cleanup", cleanup_rc))
			result_queue.put(("worker", worker_index, -1, self.get_worker_log_path(worker_index), cleanup_rc))

	def collect_result_events(self, result_queue):
		events = []
		while True:
			try:
				events.append(result_queue.get_nowait())
			except queue.Empty:
				break
		events.sort(key=lambda event: (event[2], event[1], event[0]))
		return events

	def process_progress_events(self, progress_queue, running_tests):
		processed = False
		while True:
			try:
				kind, worker_index, task_index, progress, rc = progress_queue.get_nowait()
			except queue.Empty:
				break

			processed = True
			if kind == "start":
				test_case = progress.partition(": ")[2] or progress
				running_tests[worker_index] = self.format_status_test_name(test_case)
				self.live_print(f"starting {progress} on worker {worker_index}", running_tests)
			elif kind == "end":
				running_tests.pop(worker_index, None)
				self.live_print(
					f"ending {progress} on worker {worker_index}: rc={rc}",
					running_tests,
					self.get_result_color(rc))
			elif kind == "worker_error":
				self.live_print(
					f"worker {worker_index} {progress} failed: rc={rc}",
					running_tests,
					self.get_result_color(rc))

		return processed

	def wait_with_progress(self, seconds, progress_queue, running_tests):
		deadline = time.time() + seconds
		while True:
			self.process_progress_events(progress_queue, running_tests)
			remaining = deadline - time.time()
			if remaining <= 0:
				break
			time.sleep(min(0.1, remaining))

	def run_workers(self, test_queue):
		start = time.time()
		workers = []
		self.total_test_count = len(test_queue)
		multi_node_tasks = []
		general_tasks = []

		for task_index, test_case in enumerate(test_queue):
			task = (task_index, self.total_test_count, test_case)
			self.task_names[task_index] = test_case
			self.scheduled_task_ids.add(task_index)
			if testsuite.is_multi_node_cluster_test(test_case):
				multi_node_tasks.append(task)
			else:
				general_tasks.append(task)

		if multi_node_tasks:
			worker_count = min(self.max_worker_count, len(general_tasks) + 1)
		else:
			worker_count = min(self.max_worker_count, len(general_tasks))
		work_queue = multiprocessing.Queue()
		result_queue = multiprocessing.Queue()
		progress_queue = multiprocessing.Queue()
		setup_done_events = [
			multiprocessing.Event()
			for _ in range(worker_count)
		]
		running_tests = {}

		for task in general_tasks:
			work_queue.put(task)
		for _ in range(worker_count):
			work_queue.put(None)

		self.live_print("---------------------------------")
		self.live_print(f"workers dir: {self.work_dir}")
		self.live_print(f"total tests: {self.total_test_count}")
		self.live_print(f"queue size: {len(general_tasks)}")
		self.live_print(f"worker 0 reserved multi-node tests: {len(multi_node_tasks)}")

		for worker_index in range(worker_count):
			if self.defer_worker_start > 0 and worker_index != 0:
				self.wait_with_progress(self.defer_worker_start, progress_queue, running_tests)
			self.process_progress_events(progress_queue, running_tests)
			self.live_print(f"------ starting worker {worker_index}...", running_tests)

			reserved_tasks = multi_node_tasks if worker_index == self.multi_node_worker_index else []
			worker = multiprocessing.Process(
				target=self.run_worker,
				args=(
					worker_index,
					reserved_tasks,
					work_queue,
					result_queue,
					progress_queue,
					setup_done_events))
			worker.start()
			workers.append(worker)

		self.live_print("waiting for workers...", running_tests)
		while any(worker.is_alive() for worker in workers):
			self.process_progress_events(progress_queue, running_tests)
			time.sleep(0.1)

		for worker in workers:
			worker.join()
		self.worker_exitcodes = {
			worker_index: worker.exitcode
			for worker_index, worker in enumerate(workers)
			if worker.exitcode != 0
		}

		while self.process_progress_events(progress_queue, running_tests):
			pass
		running_tests.clear()
		self.clear_status_line()
		self.live_print("all workers completed")

		self.result_events = self.collect_result_events(result_queue)
		self.result_task_ids = [
			task_index
			for kind, _, task_index, _, _ in self.result_events
			if kind == "task"
		]
		self.result_log_paths = []
		self.result_log_labels = []
		for kind, worker_index, task_index, log_path, rc in self.result_events:
			if kind != "task" and rc == 0:
				continue

			self.result_log_paths.append(log_path)
			if kind == "task":
				test_name = self.task_names.get(task_index, f"task {task_index}")
				self.result_log_labels.append(f"worker {worker_index} task {task_index}: {test_name}")
			else:
				self.result_log_labels.append(f"worker {worker_index} lifecycle")

		end = time.time()
		return worker_count, timedelta(seconds = end - start)

	def print_logs(self, worker_count):
		print(f"==========================================")
		self.print_file(self.queue_suite_path)

		for i in range(worker_count):
			cluster = self.get_worker_cluster(i)
			print(f"==========================================")
			print(f"########## worker {i}, cluster: {cluster}, lifecycle")

			log_path = self.get_worker_log_path(i)
			self.print_file(log_path, colorize_test_results=True)

		for event in self.result_events:
			kind, worker_index, task_index, log_path, _ = event
			if kind != "task":
				continue
			print(f"==========================================")
			print(f"########## worker {worker_index}, task {task_index}")
			self.print_file(log_path)

	def process_result(self, execution_time):
		if self.worker_exitcodes:
			workers = ", ".join(
				f"worker {worker_index}: rc={rc}"
				for worker_index, rc in sorted(self.worker_exitcodes.items()))
			print(f"Workers exited unexpectedly: {workers}")
			return False

		if (set(self.result_task_ids) != self.scheduled_task_ids
				or len(self.result_task_ids) != len(self.scheduled_task_ids)):
			print(
				"Worker task results do not match scheduled tasks: "
				f"scheduled={sorted(self.scheduled_task_ids)}, "
				f"reported={sorted(self.result_task_ids)}")
			return False

		if not self.result_log_paths:
			print("No worker result logs found")
			return False
		return process_workers_logs.run(
			self.expected_failures_path,
			self.result_log_paths,
			execution_time,
			self.result_log_labels)

	def run(self, argv):
		self.parse_cmdline(argv)

		if self.max_worker_count <= 0:
			print("--clusters must be greater than 0")
			return False

		test_queue = self.prepare_test_queue()
		if not test_queue:
			print("No tests matched")
			return

		self.prepare_workspace()
		self.queue_suite_path = self.get_queue_suite_path()
		self.store_suite(self.queue_suite_path, test_queue)

		worker_count, execution_time = self.run_workers(test_queue)

		self.print_logs(worker_count)

		result = self.process_result(execution_time)
		print(f"result dir: {self.work_dir}")

		return result


def main():
	test_suite_runner = DistTestSuiteRunner()
	if not test_suite_runner.run(sys.argv[1:]):
		sys.exit(6)


if __name__ == "__main__":
	main()
