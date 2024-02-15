# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


# Test utilities

import threading
from typing import List
import unittest
import subprocess
import logging
import os
from setup.config import g_ts_cfg
from utils import auxutil, ociutil

from utils.auxutil import isotime
from . import fmt
from . import kutil
from . import mutil
import datetime
import time
import sys
import re
from .aggrlog import LogAggregator
from kubernetes.stream import stream
from kubernetes import client
from kubernetes.stream.ws_client import ERROR_CHANNEL

g_test_data_dir = "."

debug_adminapi_sql = 0
testpod = os.getenv("TESTPOD_NAME") or "testpod"

g_full_log = LogAggregator()

logger = logging.getLogger("tutil")


def split_logs(logs):
    return logs.split("\n")


def get_pod_container(pod, container_name):
    for cont in pod["status"]["containerStatuses"]:
        if cont["name"] == container_name:
            return cont
    return None


class Rule:
    def __init__(self):
        pass

    def forget(self):
        pass


class LogAnalyzer:
    def __init__(self, logs):
        self.logs = split_logs(logs)
        self.pos = None

    def allow(self, patterns):
        assert type(patterns) is list

    def forbid(self, patterns):
        assert type(patterns) is list

    def at_least(self, count, pattern):
        pass

    def at_most(self, count, pattern):
        pass

    def seek_first(self, pattern):
        pass

    def seek_last(self, pattern):
        pass

    def expect_after(self, message):
        pass

    def expect_before(self):
        pass

    def expect_between(self):
        pass


#

def strip_finalizers(ns, rsrc, name):
    r = kutil.get(ns, rsrc, name)

    if r and "finalizers" in r["metadata"]:
        logger.info(
            f"Stripping finalizers from {ns}/{name} ({r['metadata']['finalizers']})")
        kutil.patch(ns, rsrc, name, [
                    {"op": "remove", "path": "/metadata/finalizers"}], type="json")


def delete_ic(ns, name):
    logger.info(f"Delete ic {ns}/{name}")

    strip_finalizers(ns, "ic", name)
    kutil.delete_ic(ns, name, timeout=90)
    logger.info(f"ic {ns}/{name} deleted")


def wipe_ns(ns, extra_rsrc=[]):
    ics = kutil.ls_ic(ns)
    for ic in ics:
        delete_ic(ns, ic["NAME"])

    logger.info(f"Deleting remaining pods from {ns}")
    for pod in kutil.ls_po(ns):
        strip_finalizers(ns, "po", pod["NAME"])
    kutil.delete_po(ns, None, timeout=90)

    for rsrc in kutil.ALL_RSRC_TYPES + extra_rsrc:
        if rsrc != "po" and rsrc != "ic":
            logger.info(f"- Deleting {rsrc} from {ns}...")
            kutil.delete(ns, rsrc, None, timeout=90)

    try:
        kutil.delete_ns(ns)
    except subprocess.CalledProcessError as e:
        if "Upon completion, this namespace will automatically be purged by the system." in e.stderr.decode("utf8"):
            for i in range(60):
                if ns not in [n["NAME"] for n in kutil.ls_ns()]:
                    break
                time.sleep(1)


def wipe_pv():
    kutil.delete_pv(None)


def run_from_operator_pod(uri, script):
    podname = kutil.ls_po("mysql-operator")[0]["NAME"]

    kutil.cat_in("mysql-operator", [podname, "mysql-operator"], "/tmp/script.py", "print('##########')\n" + script + "\n")

    api_core = client.CoreV1Api()

    if not uri.startswith("mysql:") and not uri.startswith("mysqlx:"):
        uri = "mysql://" + uri

    r = stream(api_core.connect_get_namespaced_pod_exec,
                        podname,
                        "mysql-operator",
                        container="mysql-operator",
                        command=["mysqlsh", uri, "-f", "/tmp/script.py"],
                        stderr=True, stdin=False,
                        stdout=True, tty=False,
                        _preload_content=True)

    return auxutil.purge_warnings(r.split("##########", 1)[-1].strip())

#


class EventWatcher(threading.Thread):
    def __init__(self):
        pass

    def check(self):
        pass

    def add_pod_watch(self, ns, name, badlist):
        pass

    def add_ic_watch(self, ns, name, badlist):
        pass


class TestTracer:
    def __init__(self):
        self.source_cache = {}
        self.basedir = "/"
        self.trace_all = False
        self.enabled = False

    def getline(self, filename, lineno):
        if filename == "<string>":
            return None
        f = self.source_cache.get(filename)
        if not f:
            f = [l.rstrip() for l in open(filename).readlines()]
            self.source_cache[filename] = f
        return f[lineno-1]

    def localtrace(self, frame, why, arg, parent=None):
        code = frame.f_code
        filename = code.co_filename
        lineno = frame.f_lineno

        filename = filename.replace(self.basedir+"/", "")
        fn = os.path.basename(filename)

        if why == "line":
            line = self.getline(filename, lineno)
            if parent:
                g_full_log.annotate(f"{fn}:{lineno}: {parent}: {line}")
            print(fmt.cyan(f"{fn}:{lineno}: {line}"))
            return lambda f, w, a: self.localtrace(f, w, arg, parent)
        elif why == "call":
            if parent:
                print(fmt.cyan(f"{fn}:{lineno}: {code.co_name} >>"))
            return lambda f, w, a: self.localtrace(f, w, arg, None)
        elif why == "return":
            if parent:
                if "self" in frame.f_locals:
                    if isinstance(frame.f_locals["self"], OperatorTest):
                        print(fmt.purple(
                            f"{fn}:{lineno}: <<<< {frame.f_locals['self'].__class__.__name__}.{code.co_name}"))
                    else:
                        print(fmt.cyan(
                            f"{fn}:{lineno}: <<<< {frame.f_locals['self'].__class__.__name__}.{code.co_name}"))
                elif "cls" in frame.f_locals:
                    print(fmt.cyan(
                        f"{fn}:{lineno}: <<<< {frame.f_locals['cls'].__name__}.{code.co_name}"))
                else:
                    print(fmt.cyan(f"{fn}:{lineno}: <<<< {code.co_name}"))
            else:
                print(fmt.cyan(f"{fn}:{lineno}: << {code.co_name}"))
            return lambda f, w, a: self.localtrace(f, w, arg, None)
        elif why == "exception":
            if parent:
                g_full_log.annotate(f"exception at {fn}:{lineno}")
            print(fmt.red(f"{fn}:{lineno}: EXCEPTION"))
            return lambda f, w, a: self.localtrace(f, w, arg, parent)
        else:
            assert False, why

    def globaltrace(self, frame, why, arg):
        if why == "call":
            code = frame.f_code
            filename = frame.f_globals.get('__file__', None)
            lineno = frame.f_lineno

            if filename and (filename.startswith(self.basedir) or not filename.startswith("/")):
                filename = filename.replace(self.basedir+"/", "")
                fn = os.path.basename(filename)

                if "_t.py" in filename or self.trace_all:
                    print()
                    if "self" in frame.f_locals:
                        if isinstance(frame.f_locals["self"], OperatorTest):
                            g_full_log.annotate(
                                f"Begin Test {frame.f_locals['self'].__class__.__name__}.{code.co_name}")
                            print(fmt.purple(
                                f"{fn}:{lineno}: {frame.f_locals['self'].__class__.__name__}.{code.co_name} >>>>"))
                            return lambda f, w, a: self.localtrace(f, w, arg, code.co_name)
                        else:
                            print(fmt.cyan(
                                f"{fn}:{lineno}: {frame.f_locals['self'].__class__.__name__}.{code.co_name} >>>>"))
                    elif "cls" in frame.f_locals:
                        print(fmt.cyan(
                            f"{fn}:{lineno}: {frame.f_locals['cls'].__name__}.{code.co_name} >>>>"))
                    else:
                        print(fmt.cyan(f"{fn}:{lineno}: {code.co_name} >>>>"))
                    return lambda f, w, a: self.localtrace(f, w, arg)

            return None
        else:
            assert False, why

    def install(self):
        if self.enabled:
            print("Enabled tracer")
            sys.settrace(self.globaltrace)


tracer = TestTracer()


class PodHelper:
    def __init__(self, owner, ns, name):
        self.owner = owner
        self.ns = ns
        self.name = name
        self.refresh()

    def get_po(self):
        return kutil.get_po(self.ns, self.name)

    def refresh(self):
        self._state = self.get_po()

    def get_container_status(self, cont):
        return get_pod_container(self._state, cont)

    def wait_ready(self):
        self.owner.assertNotEqual(
            kutil.wait_pod(self.ns, self.name, "Running",
                           checkabort=self.owner.check_operator_exceptions,
                           checkready=True),
            None, f"timeout waiting for pod {self.name}")

    def wait_restart(self):
        restarts0 = self.get_container_status("mysql")["restartCount"]

        def ready():
            pod = self.get_po()
            restarts = get_pod_container(pod, "mysql")["restartCount"]
            return restarts >= restarts0+1 and pod["status"]["phase"] == "Running"

        self.owner.wait(ready)

    def wait_gone(self):
        kutil.wait_pod_gone(self.ns, self.name,
                        checkabort=self.owner.check_operator_exceptions)


class StoreOperatorLog:
    operator_ns = "mysql-operator"
    operator_container = "mysql-operator"
    timestamp = None
    work_dir = None

    def get_timestamp_now(self):
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        return timestamp.isoformat()

    def mark_timestamp(self):
        prev_timestamp = self.timestamp
        self.timestamp = self.get_timestamp_now()
        return prev_timestamp

    def get_work_dir(self):
        if not self.work_dir:
            self.work_dir = os.path.join(g_ts_cfg.work_dir, 'operator-log', g_ts_cfg.k8s_context)

        if not os.path.exists(self.work_dir):
            os.makedirs(self.work_dir)

        return self.work_dir

    def get_snapshot_log_path(self, snapshot_dir, cls_name):
        index = 0
        log_fname = f'{datetime.datetime.utcnow().strftime("%Y.%m.%d-%H.%M.%S")}-{cls_name}'
        while True:
            suffix = f"-{str(index)}" if index > 0 else ""
            snapshot_log_path = os.path.join(snapshot_dir, log_fname) + f"{suffix}.log"
            if not os.path.exists(snapshot_log_path):
                return snapshot_log_path
            index += 1

    def store_log(self, operator_pod, timestamp, snapshot_log_path):
        try:
            logger.info(f"store snapshot of operator {self.operator_ns}/{operator_pod} log into {snapshot_log_path}...")
            contents = kutil.logs(self.operator_ns, [operator_pod, self.operator_container], since_time=timestamp, cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE)
            with open(snapshot_log_path, 'w') as f:
                f.write(contents)
            logger.info(f"store snapshot of operator {self.operator_ns}/{operator_pod} log into {snapshot_log_path} completed")
        except BaseException as err:
            logger.error(f"error while storing snapshot of operator {self.operator_ns}/{operator_pod} log into {snapshot_log_path}: {err}")

    def take_snapshot(self, cls_name):
        logger.info(f"taking snapshot of operator log for {cls_name}...")
        snapshot_dir = self.get_work_dir()
        prev_timestamp = self.mark_timestamp()

        operator_pods = kutil.ls_pod(self.operator_ns, "mysql-operator-.*")
        for operator_pod in operator_pods:
            snapshot_log_path = self.get_snapshot_log_path(snapshot_dir, cls_name)
            self.store_log(operator_pod["NAME"], prev_timestamp, snapshot_log_path)
        logger.info(f"taking snapshot of operator log for {cls_name} completed")

g_store_log_operator = None


def mangle_name(base_name):
    ns = []
    prev_chr_was_upper = False
    for chr in base_name:
        if chr.isupper():
            if len(ns) > 0 and not prev_chr_was_upper:
                ns.append('-')
            ns.append(chr.lower())
            prev_chr_was_upper = True
        else:
            ns.append(chr)
            prev_chr_was_upper = False
    return ''.join(ns)

class OperatorTest(unittest.TestCase):
    logger = logging
    stream_handler = None
    ns = None
    op_stdout = []
    op_check_stdout = None
    default_allowed_op_errors: List[str]

    @classmethod
    def setUpClass(cls, ns=None):
        # set up loggers
        cls.stream_handler = logging.StreamHandler(sys.stdout)
        cls.logger.addHandler(cls.stream_handler)
        logger.addHandler(cls.stream_handler) # tutil.logger
        kutil.logger.addHandler(cls.stream_handler)
        mutil.logger.addHandler(cls.stream_handler)
        ociutil.logger.addHandler(cls.stream_handler)
        g_ts_cfg.current_test_name = mangle_name(cls.__name__)

        cls.logger.info(f"Starting {cls.__name__}")
        if ns:
            __class__.ns = ns
        else:
            __class__.ns = g_ts_cfg.current_test_name

        leftovers = kutil.ls_all_raw(cls.ns)
        if leftovers:
            cls.logger.error("Namespace %s not empty before test: %s", cls.ns, leftovers)
            raise Exception(f"Namespace {cls.ns} not empty")

        kutil.create_ns(cls.ns, g_ts_cfg.get_custom_test_ns_labels())

        # stdout from operator
        cls.op_stdout = []
        # errors collected from operator output that indicate an unexpected
        # error happened, which would mean the operator broke
        cls.op_fatal_errors = []
        cls.op_logged_errors = []
        cls.op_log_errors = []
        cls.op_exception = []

        def check_operator_output(line):
            if "[CRITICAL]" in line or "[ERROR   ]" in line:
                cls.op_logged_errors.append(line)
            elif line.startswith("Traceback (most recent call last):"):
                cls.op_exception.append(line)
            elif cls.op_exception:
                if line.startswith("["):
                    stack = "".join(cls.op_exception)
                    # Ignore error caused by bug in kopf
                    if "ClientResponseError: 422" in stack:
                        pass
                    else:
                        cls.op_fatal_errors.append(stack)
                    cls.op_exception = []
                else:
                    cls.op_exception.append(line)

        # TODO monitor for operator pod restarts (from crashes)
        g_full_log.on_operator = check_operator_output

        if g_ts_cfg.custom_secret_name:
            secrets = kutil.ls_secret(cls.ns, g_ts_cfg.custom_secret_name)
            if len(secrets) == 0:
                kutil.copy_secret(g_ts_cfg.custom_secret_ns, g_ts_cfg.custom_secret_name, cls.ns)

    @classmethod
    def tearDownClass(cls):
        try:
            kutil.delete_pvc(cls.ns, None)

            leftovers = kutil.ls_all_raw(cls.ns)
            if leftovers:
                cls.logger.error(
                    "Namespace %s not empty at the end of the test case!", cls.ns)
                cls.logger.info("%s", leftovers)
                wipe_ns(cls.ns)
        finally:
            if cls.stream_handler:
                logger.removeHandler(cls.stream_handler) # tutil.logger
                kutil.logger.removeHandler(cls.stream_handler)
                mutil.logger.removeHandler(cls.stream_handler)
                ociutil.logger.removeHandler(cls.stream_handler)
                cls.logger.removeHandler(cls.stream_handler)
            g_ts_cfg.current_test_name = None
            cls.take_log_operator_snapshot()

    @classmethod
    def take_log_operator_snapshot(cls):
        if g_store_log_operator:
            g_store_log_operator.take_snapshot(mangle_name(cls.__name__))

    def setUp(self):
        self.allowed_op_logged_errors = self.default_allowed_op_errors[:]
        self.start_time = isotime()

    def tearDown(self):
        self.check_operator_exceptions()

        # reset operator error counter
        self.op_fatal_errors = []
        self.op_logged_errors = []

    def has_got_cluster_event(self, cluster, after=None, *, type, reason, msg):
        if after is None:
            after = self.start_time

        if isinstance(msg, str):
            msgpat = re.compile(f"^{msg}$")
        else:
            msgpat = re.compile(msg)

        events = kutil.get_ic_ev(
            self.ns, cluster, after=after, fields=["message", "reason", "type"])

        events = [(ev['type'], ev['reason'], ev['message']) for ev in events]

        for t, r, m in events:
            if t == type and r == reason and msgpat.match(m):
                return True
        else:
            logger.info(f"Events for {cluster}" + "\n".join([str(x) for x in events]))
            return False

    def assertGotClusterEvent(self, cluster, after=None, *, type, reason, msg):
        if not self.has_got_cluster_event(cluster, after, type=type, reason=reason, msg=msg):
            self.fail(
                f"Event ({type}, {reason}, {msg}) not found for {cluster}")

    def wait_got_cluster_event(self, cluster, after=None, timeout=90, delay=2, *, type, reason, msg):
        def timeout_diagnostics():
            kutil.store_ic_diagnostics(self.ns, cluster)

        def check_has_got_cluster_event():
            return self.has_got_cluster_event(cluster, after, type=type, reason=reason, msg=msg)

        self.wait(check_has_got_cluster_event, timeout=timeout, delay=delay, timeout_diagnostics=timeout_diagnostics)

    def check_operator_exceptions(self):
        # Raise an exception if there's no hope that the operator will make progress
        # (e.g. because of an unhandled exception)
        if len(self.op_fatal_errors) > 0:
            self.logger.critical(
                fmt.red("Operator exception: ") + "\n    ".join(self.op_fatal_errors))
        self.assertEqual(len(self.op_fatal_errors), 0,
                         "Unexpected operator exceptions detected")

        # Check for logged errors from the operator
        op_errors = []
        for err in self.op_logged_errors:
            for allowed in self.allowed_op_logged_errors:
                if re.search(allowed, err):
                    break
            else:
                op_errors.append(err.rstrip())
        if op_errors:
            self.logger.critical(
                fmt.red("Unexpected operator errors: ") + "\n    " + "\n    ".join(op_errors))
        self.assertEqual(len(op_errors), 0,
                         "Unexpected operator exceptions detected")

    def check_pod_errors(self):
        # Raise an exception if pods enter an error state they're not expected to
        pass  # TODO

    def wait(self, fn, args=tuple(), check=None, timeout=60, delay=2, timeout_diagnostics=None):
        if not timeout_diagnostics:
            timeout_diagnostics = lambda : kutil.store_ns_diagnostics(self.ns)

        # TODO abort watchers when nothing new gets printed by operator for a while too
        self.check_operator_exceptions()

        timeout //= delay

        r = None
        for i in range(timeout):
            r = fn(*args)
            self.logger.debug(f"fn() returned {r}")
            if check:
                ret = check(r)
                if ret:
                    return ret
            else:
                if r:
                    return r
            time.sleep(delay)
            self.check_operator_exceptions()
            self.check_pod_errors()

        if check:
            self.logger.error(
                f"Waited condition never became true. Last value was {r}")
        else:
            self.logger.error("Waited condition never became true")

        if timeout_diagnostics:
            timeout_diagnostics()
        raise Exception("Timeout waiting for condition")

    def get_pod(self, name, ns=None):
        return PodHelper(self, ns if ns else self.ns, name)

    def wait_ic(self, name, status_list, num_online=None, ns=None, timeout=600, probe_time=None):
        """
        Wait for given ic object to reach one of the states in the list.
        Aborts on timeout or when an unexpected error is detected in the operator.
        """
        self.assertNotEqual(kutil.wait_ic(ns or self.ns, name, status_list, num_online=num_online, probe_time=probe_time,
                                          checkabort=self.check_operator_exceptions, timeout=timeout), None, "timeout waiting for cluster")

    def wait_member_state(self, pod, states, timeout=120):
        with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s0:
            def check():
                s = s0.query_sql("select member_state from performance_schema.replication_group_members where member_id=@@server_uuid").fetch_one()[0]
                return s in states
            self.wait(check, timeout=timeout)

    def wait_pod(self, name, status_list, ns=None, ready=False):
        """
        Wait for given pod object to reach one of the states in the list.
        Aborts on timeout or when an unexpected error is detected in the operator.
        """
        self.assertNotEqual(kutil.wait_pod(ns or self.ns, name, status_list,
                                           checkabort=self.check_operator_exceptions,
                                           checkready=ready),
                            None, "timeout waiting for pod")

    def wait_routers(self, name_pattern, num_online, awaited_status=["Running"], awaited_ready = None,
                    total_router_pod_containers_getter = g_ts_cfg.get_router_total_containers_per_pod, ns=None, timeout=60):
        """
        Wait for routers matching the name-pattern to reach one of the states in the awaited status list.
        Aborts on timeout or when an unexpected error is detected in the operator.
        """
        if type(awaited_status) not in (tuple, list):
            awaited_status = [awaited_status]

        logger.info(
            f"Waiting for routers {ns}/{name_pattern} to become {awaited_status}, num_online={num_online}")

        router_names = []

        if not awaited_ready:
            total_router_pod_containers = total_router_pod_containers_getter()
            awaited_ready = f"{total_router_pod_containers}/{total_router_pod_containers}"

        def routers_ready():
            pods = kutil.ls_po(ns or self.ns, pattern=name_pattern)

            # there may be a case where other routers are still running (termination may take some time)
            # we expect the exact number of routers with a given status
            if len(pods) != num_online:
                return False

            router_names[:] = [pod["NAME"] for pod in pods if pod["STATUS"] in awaited_status and (awaited_ready == None or pod["READY"] == awaited_ready)]

            return num_online == len(router_names)

        def timeout_diagnostics():
            kutil.store_routers_diagnostics(self.ns, name_pattern)

        self.wait(routers_ready, timeout=timeout, timeout_diagnostics=timeout_diagnostics)

        return router_names

    def wait_ic_gone(self, name, ns=None):
        kutil.wait_ic_gone(ns or self.ns, name,
                           checkabort=self.check_operator_exceptions)

    def wait_pod_gone(self, name, ns=None):
        kutil.wait_pod_gone(ns or self.ns, name,
                            checkabort=self.check_operator_exceptions)

    def wait_pods_gone(self, name_pattern, timeout=180, ns=None):
        pods = kutil.ls_pod(ns or self.ns, name_pattern)
        for pod in pods:
            kutil.wait_pod_gone(ns or self.ns, pod["NAME"], timeout=timeout,
                                checkabort=self.check_operator_exceptions)

    def wait_routers_gone(self, name_pattern, timeout=180, ns=None):
        self.wait_pods_gone(name_pattern, timeout, ns)

    def get_instances_by_role(self, instance="mycluster-0", user="root", password="sakila"):
        primaries = []
        secondaries = []
        with mutil.MySQLPodSession(self.ns, instance, user, password) as session:
            members = session.query_sql(
                "SELECT member_host, member_role FROM performance_schema.replication_group_members ORDER BY member_host").fetch_all()

            print(members)

            for mhost, mrole in members:
                instance_name = auxutil.extract_instance_name(mhost)
                if mrole == "PRIMARY":
                    primaries.append(instance_name)
                elif mrole == "SECONDARY":
                    secondaries.append(instance_name)
                else:
                    raise Exception(f"unexpected role {mrole}")

        return (primaries, secondaries)

    def get_primary_instance(self, instance="mycluster-0", user="root", password="sakila"):
        primaries, _ = self.get_instances_by_role(instance, user, password)
        if len(primaries) != 1:
            raise Exception(f"expected one primary, but got {primaries}")
        return primaries[0]

    def get_secondary_instances(self, instance="mycluster-0", user="root", password="sakila"):
        _, secondaries = self.get_instances_by_role(instance, user, password)
        return secondaries


def get_sts_rollover_update_waiter(test_obj: OperatorTest, cluster_name:str, timeout: int, delay: int):
    def get_pods_uids(pattern) -> set:
        return set([kutil.get_po(test_obj.ns, pod['NAME'])['metadata']['uid'] for pod in kutil.ls_po(test_obj.ns, pattern=pattern)])

    pattern = f"{cluster_name}-\d"
    old_uids = get_pods_uids(pattern)

    def waiter() -> bool:
        test_obj.wait(lambda : len(old_uids.intersection(get_pods_uids(pattern))) == 0, timeout=timeout, delay=delay)

    return waiter