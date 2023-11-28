# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# Kubernetes (kubectl) utilities

# Use kubectl instead of the API so we go through the same code path as an end-user

import datetime
import os
import subprocess
import logging
import threading
import time
import re
import yaml
import base64
import pathlib
import json
from setup.config import g_ts_cfg
from enum import Enum

logger = logging.getLogger("kutil")

debug_kubectl = False

ALL_RSRC_TYPES = ["ic", "mbk", "po", "sts", "rs", "deploy",
                  "svc", "cm", "secret", "jobs", "deploy", "pvc", "sa"]


def b64decode(s):
    return base64.b64decode(s).decode("utf8")


def b64encode(s):
    return base64.b64encode(bytes(s, "utf8")).decode("ascii")


def strip_blanks(s):
    """
    Strip empty lines in the string.
    """
    return "\n".join([l for l in s.split("\n") if l.strip()])


class TableSplitter:
    def __init__(self, header):
        columns = []
        while header:
            end = header.find("   ")
            if end < 0:
                columns.append(header)
                header = ""
            else:
                while end < len(header) and header[end] == " ":
                    end += 1
                columns.append(header[:end])
                header = header[end:]

        self.widths = [len(c) for c in columns]
        self.columns = [c.strip() for c in columns]
        # TODO handle changing column widths

    def split(self, line):
        fields = []
        offs = 0
        for i, p in enumerate(self.widths[:-1]):
            op = p
            # adjust widths in case some colunm grew
            while p <= len(line) and line[p-1] != " ":
                p += 1
                offs += 1
            if p > op:
                while p < len(line) and line[p] == " ":
                    p += 1
                    offs += 1
            self.widths[i] = p
            fields.append(line[:p].strip())
            line = line[p:]
        fields.append(line.strip())
        return fields

    def split_dict(self, line):
        return dict(zip(self.columns, self.split(line)))


def split_table(s):
    lines = s.rstrip().split("\n")
    splitter = TableSplitter(lines[0])
    return [dict(zip(splitter.columns, splitter.split(l))) for l in lines[1:]]

def decode_stream(stream):
    if stream:
        return stream.decode("utf8")
    return ""

def get_current_context():
    argv = [g_ts_cfg.kubectl_path, "config", "current-context"]
    if debug_kubectl:
        logger.debug("run %s", " ".join(argv))
    ret = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = decode_stream(ret.stdout)
    if debug_kubectl:
        logger.debug("rc = %s, stdout = %s", ret.returncode, output)
    if ret.returncode == 0:
        return output.strip()

    raise Exception(f"Could not get current context {ret}")


def get_kubectl_diagnostics_log_path(cmd, rsrc, argv):
    ns=None
    NAMESPACE_OPTION='-n'
    if argv and NAMESPACE_OPTION in argv:
        ns = argv[argv.index(NAMESPACE_OPTION) + 1]

    diag_dir = g_ts_cfg.get_diagnostics_dir(ns)
    if not os.path.exists(diag_dir):
        os.makedirs(diag_dir)

    log_fname = f'{datetime.datetime.utcnow().strftime("%Y.%m.%d-%H.%M.%S")}-{cmd}'
    if rsrc:
        log_fname += f"-{rsrc}"

    base_log_path = os.path.join(diag_dir, log_fname)

    index = 0
    while True:
        suffix = f"-{str(index)}" if index > 0 else ""
        log_path = base_log_path + f"{suffix}.log"
        if not os.path.exists(log_path):
            return log_path
        index += 1

def store_kubectl_diagnostics(cmd, rsrc, argv, contents):
    log_path = get_kubectl_diagnostics_log_path(cmd, rsrc, argv)
    cmd_info = f"kubectl {cmd}"
    if rsrc:
        cmd_info += f" {rsrc}"
    subject = f"storing '{cmd_info}' diagnostics in {log_path}"
    try:
        logger.info(f"{subject}...")
        with open(log_path, 'w') as f:
            f.write(f"{' '.join(argv)}\n")
            f.write(contents)
        logger.info(f"{subject} completed...")
    except BaseException as err:
        logger.error(f"error while {subject}: {err}")

class KubectlCmdOutputLogging(Enum):
    # if debug_kubectl is True, log kubectl cmd output to logger
    STD = 1
    # if debug_kubectl is True, log kubectl output to a file in diagnostics dir
    # it is used to avoid storing long cmd outputs in standard log
    DIAGNOSTICS = 2
    # don't write kubectl cmd output to logger regardless debug_kubectl is True
    MUTE = 3

def kubectl(cmd, rsrc=None, args=None, timeout=None, check=True, ignore=[], timeout_diagnostics=None, cmd_output_log=KubectlCmdOutputLogging.STD):
    argv = [g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", cmd]
    if rsrc:
        argv.append(rsrc)
    if args:
        argv += args
    if debug_kubectl:
        logger.debug("run %s", " ".join(argv))
    try:
        r = subprocess.run(argv, timeout=timeout,
            check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.TimeoutExpired as e:
        logger.error("kubectl failed: %s:\n    stderr=%s\n    stdout=%s",
                        e, decode_stream(e.stderr), decode_stream(e.stdout))
        if timeout_diagnostics:
            timeout_diagnostics()
        raise
    except subprocess.CalledProcessError as e:
        for ig in ignore:
            if "(%s)" % ig in e.stderr.decode("utf8"):
                if debug_kubectl:
                    logger.debug("rc = %s, stderr=%s",
                                 e.returncode, decode_stream(e.stderr))
                return
        else:
            logger.error("kubectl %s failed (rc=%s):\n    stderr=%s\n    stdout=%s",
                         e.cmd, e.returncode,
                         decode_stream(e.stderr), decode_stream(e.stdout))
            raise
    if debug_kubectl and cmd_output_log != KubectlCmdOutputLogging.MUTE:
        cmd_output = f"rc = {r.returncode}, stdout = {decode_stream(r.stdout)}, stderr = {decode_stream(r.stderr)}"
        if cmd_output_log == KubectlCmdOutputLogging.STD:
            logger.debug(cmd_output)
        elif cmd_output_log == KubectlCmdOutputLogging.DIAGNOSTICS:
            store_kubectl_diagnostics(cmd, rsrc, argv, cmd_output)

    return r


def kubectl_popen(cmd, args=[]):
    argv = [g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", cmd] + args

    if debug_kubectl:
        logger.debug("popen %s", " ".join(argv))

    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def watch(ns, rsrc, name, fn, timeout, format=None):
    argv = [g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", "get", rsrc, "-n", ns, "--watch", "-o%s" % format]
    if name:
        argv.append(name)

    found = None

    def kill_on_timeout(p):
        start_time = time.time()
        while time.time() - start_time < timeout and p.poll() is None:
            time.sleep(1)

        if p.poll() is None and not found:
            logger.info(f"Timeout waiting for condition on {rsrc}")
            p.terminate()

    if debug_kubectl:
        logger.debug("run %s", argv)

    p = subprocess.Popen(argv, stdout=subprocess.PIPE)
    thd = threading.Thread(target=kill_on_timeout, args=(p,))
    thd.start()

    header = p.stdout.readline().decode("utf8")
    splitter = TableSplitter(header)

    output = [header]
    while p.poll() is None:
        line = p.stdout.readline().decode("utf8")
        output.append(line)
        if fn(splitter.split_dict(line)):
            if debug_kubectl:
                logger.debug(
                    f"watch condition on {rsrc} succeeded with {line}")
            p.terminate()
            found = line
            break

    thd.join()

    output = "".join(output)

    if not found:
        logger.error(
            f"Timeout waiting for condition in {rsrc} {ns}/{name}. output={output}")
        store_diagnostics(ns, rsrc, name)

    if debug_kubectl:
        logger.debug("rc = %s, stdout = %s", p.returncode, output)

    return found


def feed_kubectl(input, cmd, rsrc=None, args=None, check=True):
    argv = [g_ts_cfg.kubectl_path, f"--context={g_ts_cfg.k8s_context}", cmd]
    if rsrc:
        argv.append(rsrc)
    if args:
        argv += args
    if debug_kubectl:
        logger.debug("run %s", argv)
        MaxInputSize = 16384
        if input and len(input) < MaxInputSize:
            store_kubectl_diagnostics(cmd, rsrc, argv, input)
    r = subprocess.run(argv, input=input.encode("utf8"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       check=check)
    logger.info(r.stdout.decode("utf8"))
    if debug_kubectl:
        logger.debug("rc = %s", r)
    return r


def server_version() -> str:
    output = kubectl("version", args=["-o", "json"])
    sv = json.loads(output.stdout.decode("utf8"))['serverVersion']
    return f"{sv['major']}.{sv['minor']}"


def client_version() -> str:
    output = kubectl("version", args=["-o", "json"])
    cv = json.loads(output.stdout.decode("utf8"))['clientVersion']
    return f"{cv['major']}.{cv['minor']}"


def __ls(ns, rsrc, ignore=[], w_ns = True):
    return split_table(kubectl("get", rsrc, args=["-n", ns] if w_ns else [], ignore=ignore).stdout.decode("utf8"))


def ls_ic(ns, ignore=[]):
    return __ls(ns, "ic", ignore=ignore)


def ls_mbk(ns):
    return __ls(ns, "mbk")


def ls_sts(ns):
    return __ls(ns, "sts")

def ls_sc(pattern=".*"):
    scs = __ls(None, rsrc="sc", w_ns=False)
    r = re.compile(pattern)
    return [sc for sc in scs if r.match(sc["NAME"])]


def ls_rs(ns, *, pattern=".*"):
    rss = __ls(ns, "rs")
    r = re.compile(pattern)
    return [rs for rs in rss if r.match(rs["NAME"])]


def ls_deploy(ns):
    return __ls(ns, "deploy")


def ls_svc(ns):
    return __ls(ns, "svc")


def ls_po(ns, *, pattern=".*"):
    pods = __ls(ns, "po")
    r = re.compile(pattern)
    return [pod for pod in pods if r.match(pod["NAME"])]

def ls_pod(ns, name):
    return ls_po(ns, pattern=name)


def ls_pvc(ns):
    return __ls(ns, "pvc")


def ls_pv(ns):
    return __ls(ns, "pv")

def ls_sa(ns):
    return __ls(ns, "sa")

def ls_secret(ns, pattern):
    secrets = __ls(ns, "secret")
    r = re.compile(pattern)
    return [secret for secret in secrets if r.match(secret["NAME"])]


def ls_cj(ns):
    return __ls(ns, "cj")


def ls_all_raw(ns):
    def ignore(t, name):
        if t == "secret":
            if name.startswith("default-token-"):
                return True
        elif t == "cm":
            if name == "kube-root-ca.crt":
                return True
        elif t == "sa":
            if name == "default":
                return True
        return False
    output = []
    for t in ALL_RSRC_TYPES:
        r = kubectl("get", t, args=["-n", ns]).stdout.decode("utf8")
        if r:
            # strip automatically added default token
            lines = [l for l in r.strip().split("\n") if not ignore(t, l.split()[0])]
            if len(lines) <= 1:
                r = ""
            else:
                r = "\n".join(lines)
        if r:
            output.append("### " + t)
            output.append(r)
    return "\n".join(output)


def ls_ns():
    return split_table(kubectl("get", "namespace").stdout.decode("utf8"))

def set_new_default_storage_class(new_default_sc: str):
    default_scs = default_scs = ls_sc(".*\(default\)")
    if len(default_scs) == 0:
        return None
    if len(default_scs) > 1:
        raise Exception(f"Too many default storage classes")
    current_default_sc = default_scs[0]["NAME"].split(" ", 1)[0]

    disable_default_sc = {"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"false"}}}
    enable_default_sc = {"metadata": {"annotations":{"storageclass.kubernetes.io/is-default-class":"true"}}}
    patch_sc(current_default_sc, disable_default_sc, type="merge", data_as_type='json')
    patch_sc(new_default_sc, enable_default_sc, type="merge", data_as_type='json')
#

def get_raw(ns, rsrc, name, format="yaml", check=True, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS, **kwargs):
    r = kubectl("get", rsrc, args=[name, "-n", ns, f"-o={format}"], check=check, cmd_output_log=cmd_output_log, **kwargs)
    if r and r.stdout:
        return r.stdout.decode("utf8")
    return None


def get(ns, rsrc, name, check=True, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS, **kwargs):
    raw_yaml = get_raw(ns, rsrc, name, check=check, cmd_output_log=cmd_output_log, **kwargs)
    if raw_yaml:
        return yaml.safe_load(raw_yaml)
    return None


def get_ic(ns, name, jpath=None):
    return get(ns, "ic", name)


def get_mbk(ns, name, jpath=None):
    return get(ns, "mbk", name)


def get_sts(ns, name, jpath=None):
    return get(ns, "sts", name)


def get_rs(ns, name, jpath=None):
    return get(ns, "rs", name)


def get_deploy(ns, name, jpath=None, **kwargs):
    return get(ns, "deploy", name, **kwargs)


def get_svc(ns, name, jpath=None):
    return get(ns, "svc", name)


def get_po(ns, name, jpath=None, check=True):
    return get(ns, "po", name, check=check)

def get_cj(ns, name, jpath=None, check=True):
    return get(ns, "cj", name, check=check)

def get_sa(ns, name, jpath=None, check=True):
    return get(ns, "sa", name, check=check)


def get_ev(ns, selector, *, after=None, fields=None):
    def lookup(obj, field):
        r = {}
        f, dot, rest = field.partition(".")
        if rest:
            assert isinstance(obj[f], dict), field
            r[f] = lookup(obj[f], rest)
        else:
            assert f in obj, f"key={f} dict={obj}"
            r[f] = obj[f]
        return r

    r = kubectl("get", "ev", args=[
                "--field-selector="+selector,
                "--sort-by=.metadata.creationTimestamp",
                "-n", ns, "-o=yaml"], cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS)

    if r.stdout:
        evs = yaml.safe_load(r.stdout.decode("utf8"))["items"]
        if after or fields:
            res = []
            for ev in evs:
                if (not after or
                        ev["metadata"]["creationTimestamp"] >= after):
                    nev = {}
                    if fields:
                        for f in fields:
                            nev.update(lookup(ev, f))
                    else:
                        nev = ev
                    res.append(nev)
            evs = res
        return evs
    return None


def get_ic_ev(ns, icname, *, after=None, fields=None):
    return get_ev(ns, f"involvedObject.kind=InnoDBCluster,involvedObject.name={icname}", after=after, fields=fields)


def get_po_ev(ns, name, *, after=None, fields=None):
    return get_ev(ns, f"involvedObject.kind=Pod,involvedObject.name={name}", after=after, fields=fields)

#

def describe_rsrc(ns, rsrc, name, jpath=None, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS):
    r = kubectl("describe", rsrc, [name, "-n", ns], cmd_output_log=cmd_output_log)
    if r.stdout:
        return r.stdout.decode("utf8")
    raise Exception(f"Error for describe {ns}/{name}")


def describe_po(ns, name, jpath=None, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS):
    return describe_rsrc(ns, "po", name, jpath, cmd_output_log=cmd_output_log)


def describe_sts(ns, name, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS):
    return describe_rsrc(ns, "sts", name, cmd_output_log=cmd_output_log)


def describe_ic(ns, name, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS):
    return describe_rsrc(ns, "ic", name, cmd_output_log=cmd_output_log)


def describe_cj(ns, name, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS):
    return describe_rsrc(ns, "cj", name, cmd_output_log=cmd_output_log)


#

def delete(ns, rsrc, name, timeout, wait=True):
    if not name:
        name = "--all"
    args = []
    if ns:
        args += ["-n", ns]
    if not wait:
        args += ["--wait=false"]

    kubectl("delete", rsrc, [name] + args, timeout=timeout, ignore=["NotFound"], timeout_diagnostics=lambda: store_diagnostics(ns, rsrc, name))


def delete_ic(ns, name, timeout=300):
    delete(ns, "ic", name, timeout=timeout)


def delete_mbk(ns, name, timeout=200):
    delete(ns, "mbk", name, timeout=timeout)

def delete_mbks(ns, prefix, timeout=200):
    mbks = ls_mbk(ns)
    for mbk in mbks:
        if mbk["NAME"].startswith(prefix):
            delete_mbk(ns, mbk["NAME"], timeout)


def delete_po(ns, name, timeout=120):
    delete(ns, "po", name, timeout=timeout)


def delete_sts(ns, name, timeout=5):
    delete(ns, "sts", name, timeout=timeout)


def delete_rs(ns, name, timeout=5):
    delete(ns, "rs", name, timeout=timeout)


def delete_deploy(ns, name, timeout=5):
    delete(ns, "deploy", name, timeout=timeout)


def delete_svc(ns, name, timeout=5):
    delete(ns, "svc", name, timeout=timeout)


def delete_pvc(ns, name, timeout=500, wait=True):
    delete(ns, "pvc", name, timeout=timeout, wait=wait)


def delete_pv(name, timeout=500):
    delete(None, "pv", name, timeout=timeout)


def delete_ns(ns, timeout=500):
    delete(None, "ns", ns, timeout=timeout)


def delete_cm(ns, name, timeout=5):
    delete(ns, "cm", name, timeout=timeout)


def delete_secret(ns, name, timeout=5):
    delete(ns, "secret", name, timeout=timeout)

def delete_default_secret(ns, name="mypwds", timeout=5):
    delete_secret(ns, name, timeout=timeout)

#

def restart_sts(ns, name):
    kubectl("rollout", None, ["-n", ns, "restart", "statefulset", name])

#


def logs(ns, name, prev=False, since=None, since_time=None, cmd_output_log=KubectlCmdOutputLogging.DIAGNOSTICS):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]
    if prev:
        args.append("-p")
    if since:
        args.extend(["--since", since])
    if since_time:
        args.extend(["--since-time", since_time])
    return kubectl("logs", None, args + ["-n", ns], cmd_output_log=cmd_output_log).stdout.decode("utf8")


def cat(ns, name, path):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]

    args += ["-n", ns, "--", "cat", path]

    p = kubectl_popen("exec", args)
    s = p.stdout.read()
    p.terminate()
    return s


def cat_in(ns, name, path, data):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]

    args += ["-n", ns, "-i", "--", "/bin/bash", "-c", f"cat > {path}"]

    p = feed_kubectl(data, "exec", args=args, check=False)


def file_exists(ns, name, path, check="-f"):
    """Use test(1) to check if a file exist in a container"""
    result = exec(ns, name, ["test", check, path])
    return result.returncode == 0


def exec(ns, name, cmd):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]
    return kubectl("exec", None, args + ["-n", ns, "--"] + cmd)


def execp(ns, name, cmd):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]
    p = kubectl_popen("exec", args + ["-n", ns, "--"] + cmd)
    s = p.stdout.read()
    p.terminate()
    return s

def kill(ns, name, sig, pid):
    try:
        if type(name) is str:
            args = [name]
        else:
            args = [name[0], "-c", name[1]]
        kubectl("exec", None, args +
                ["-n", ns, "--", "/bin/sh", "-c", f"kill -{sig} {pid}"])
    except subprocess.CalledProcessError as e:
        if e.returncode == 137:
            pass
        else:
            raise

#

def _preprocess_yaml(y) -> str:
    to_apply = []
    #el = yaml.safe_load(y)
    arr = list(yaml.safe_load_all(y))
    for el in arr:
        if "kind" in el and el["kind"].lower() == "innodbcluster" and "spec" in el:
            custom_sts_labels = g_ts_cfg.get_custom_sts_labels()
            if len(custom_sts_labels):
                if not "podLabels" in el["spec"]:
                    el["spec"]["podLabels"] = {}
                el["spec"]["podLabels"].update(custom_sts_labels)
                recreate = True

            custom_sts_podspec = g_ts_cfg.get_custom_sts_podspec()
            if custom_sts_podspec:
                if not "podSpec" in el["spec"]:
                    el["spec"]["podSpec"] = {}
                el["spec"]["podSpec"].update(yaml.safe_load(custom_sts_podspec))
                recreate = True

            custom_server_version_override = g_ts_cfg.get_custom_ic_server_version()
            if custom_server_version_override:
                el["spec"]["version"] = custom_server_version_override
                if "readReplicas" in el["spec"]:
                    el["spec"]["readReplicas"]["version"] = custom_server_version_override
            else:
                custom_server_version = g_ts_cfg.get_custom_ic_server_version()
                if custom_server_version:
                    if not "version" in el["spec"]:
                        el["spec"]["version"] = custom_server_version
                    if "readReplicas" in el["spec"] and not "version" in el["spec"]["readReplicas"]:
                        el["spec"]["readReplicas"]["version"] = custom_server_version

            custom_router_version_override = g_ts_cfg.get_custom_ic_router_version_override()
            if custom_router_version_override:
                if not "router" in el["spec"]:
                    el["spec"]["router"] = {}
                el["spec"]["router"]["version"]  = custom_router_version_override
            else:
                custom_router_version = g_ts_cfg.get_custom_ic_router_version()
                if custom_router_version:
                    if not "router" in el["spec"]:
                        el["spec"]["router"] = {}
                    if not "version" in el["spec"]["router"]:
                        el["spec"]["router"]["version"] = custom_router_version

        to_apply.append(yaml.safe_dump(el))

    return "---\n".join(to_apply)


def apply(ns, y, *, check=True):
    stripped = strip_blanks(y)
    to_apply = _preprocess_yaml(stripped)
    if not to_apply:
        to_apply = stripped

    try:
        return feed_kubectl(to_apply, "apply", args=[
            "-n", ns, "-f", "-"], check=check)
    except subprocess.CalledProcessError as e:
        if debug_kubectl:
            logger.debug("rc = %s, stdout = %s, stderr = %s", e.returncode,
                            decode_stream(e.stdout), decode_stream(e.stderr))
        raise


def patch(ns, rsrc, name, changes, type=None, data_as_type='yaml', w_ns = True):
    data_processor = json.dumps if data_as_type and str(data_as_type).lower() =='json' else yaml.dump
    patch_data = data_processor(changes)
    type_param = ["--type=%s" % type] if type else []
    ns_arg = ["-n", ns] if w_ns else []
    kubectl("patch", rsrc, [name, "-p", patch_data, *ns_arg, *type_param])


def patch_pod(ns, name, changes, type=None, data_as_type='yaml'):
    patch(ns, "pod", name, changes, type, data_as_type='yaml')


def patch_ic(ns, name, changes, type=None, data_as_type='yaml'):
    patch(ns, "ic", name, changes, type, data_as_type)


def patch_dp(ns, name, change, type=None, data_as_type='yaml'):
    patch(ns, "deployment", name, change, type, data_as_type)

def patch_sc(name, change, type=None, data_as_type='yaml'):
    patch(None, "sc", name, change, type, data_as_type, w_ns=False)

#


def ls_nodes():
    return split_table(kubectl("get", "nodes").stdout.decode("utf8"))


def node_pods():
    return split_table(kubectl("get", "nodes").stdout.decode("utf8"))


def drain_node(node):
    pass

#

class StoreTimeoutDiagnostics:
    operator_ns = "mysql-operator"
    operator_container = "mysql-operator"

    def __init__(self, ns):
        self.ns = ns
        self.work_dir = None


    def get_work_dir(self):
        base_work_dir = os.path.join(g_ts_cfg.work_dir, 'timeout', g_ts_cfg.k8s_context)
        if self.ns:
            base_work_dir = os.path.join(base_work_dir, self.ns)

        index = 0
        while True:
            suffix = f"-{str(index)}" if index > 0 else ""
            work_dir = base_work_dir + f"{suffix}"
            if not os.path.exists(work_dir):
                return work_dir
            index += 1


    def create_work_dir(self):
        self.work_dir = self.get_work_dir()
        os.makedirs(self.work_dir)


    def store_log(self, rsrc, item_name, kind_of_log, generate_contents):
        log_fname = f"{item_name}-{kind_of_log}-{rsrc}.log"
        log_path = os.path.join(self.work_dir, log_fname)
        try:
            contents = generate_contents()
            with open(log_path, 'w') as f:
                f.write(contents)
        except BaseException as err:
            logger.error(f"error while storing '{kind_of_log}' diagnostics for {rsrc} {self.ns}/{item_name}: {err}")

    def describe_rsrc(self, rsrc, name):
        self.store_log(rsrc, name, "describe", lambda: describe_rsrc(self.ns, rsrc, name, cmd_output_log=KubectlCmdOutputLogging.MUTE))

    def describe_sts(self, sts):
        self.store_log("sts", sts, "describe", lambda: describe_sts(self.ns, sts, cmd_output_log=KubectlCmdOutputLogging.MUTE))

    def describe_ic(self, ic):
        self.store_log("ic", ic, "describe", lambda: describe_ic(self.ns, ic, cmd_output_log=KubectlCmdOutputLogging.MUTE))

    def describe_pod(self, pod, ns=None):
        self.store_log("pod", pod, "describe", lambda: describe_po(ns if ns else self.ns, pod, cmd_output_log=KubectlCmdOutputLogging.MUTE))

    def logs_pod(self, pod, container, ns=None, since=None):
        self.store_log("pod", f"{pod}-{container}", "logs", lambda: logs(ns if ns else self.ns, [pod, container], since=since, cmd_output_log=KubectlCmdOutputLogging.MUTE))


    def process_operators(self):
        # store other operator pods if exist
        operator_pods = ls_pod(self.operator_ns, "mysql-operator-.*")
        for operator_pod in operator_pods:
            operator_pod_name = operator_pod["NAME"]
            self.process_operator(operator_pod_name)

    def process_operator(self, pod):
        self.describe_pod(pod, ns=self.operator_ns)
        # store only the last few minutes of a mysql operator pod logs
        # it may run for quite a long time, but in the case of timeouts we are not interested in the whole
        # run that may take plenty of MBs which are useless for diagnostics after all
        LogOperatorPodSince = "5m"
        self.logs_pod(pod, self.operator_container, ns=self.operator_ns, since=LogOperatorPodSince)


    def process_namespace(self):
        self.process_all_clusters()
        self.process_operators()


    def process_all_clusters(self):
        if not self.ns:
            return
        ics = ls_ic(self.ns)
        for ic in ics:
            self.process_cluster(ic["NAME"])

    def process_cluster(self, cluster_name):
        self.process_ic(cluster_name)
        self.process_pods(cluster_name)
        self.process_routers(cluster_name)
        self.describe_ic(cluster_name)

    def process_ic(self, cluster_name):
        self.describe_sts(cluster_name)
        self.describe_ic(cluster_name)

    def process_pod(self, pod):
        self.describe_pod(pod)
        self.logs_pod(pod, "initconf")
        self.logs_pod(pod, "initmysql")
        self.logs_pod(pod, "sidecar")
        self.logs_pod(pod, "mysql")

    def process_pods(self, cluster_name):
        pods = ls_pod(self.ns, f"{cluster_name}-.*")
        for pod in pods:
            pod_name = pod["NAME"]
            if pod_name.startswith(f"{cluster_name}-router-"):
                continue
            self.process_pod(pod_name)

    def process_router(self, router):
        self.describe_pod(router)
        self.logs_pod(router, "router")

    def process_routers(self, cluster_name):
        routers = ls_pod(self.ns, f"{cluster_name}-router-.*")
        for router in routers:
            self.process_router(router["NAME"])


    def process_generic_rsrc(self, rsrc, name):
        self.describe_rsrc(rsrc, name)


    def extract_cluster_name_from_pod(self, base_name):
        separator = base_name.rfind('-')
        if separator == -1:
            return base_name
        return base_name[:separator]

    def extract_cluster_name_from_routers_pattern(self, routers_name_pattern):
        separator = routers_name_pattern.rfind('-router-')
        if separator == -1:
            return routers_name_pattern
        return routers_name_pattern[:separator]

    def run(self, rsrc, name):
        self.create_work_dir()
        logger.info(f"storing timeout diagnostics for {rsrc} {self.ns}/{name} into {self.work_dir} ...")

        if rsrc == "operator" or self.ns == self.operator_ns:
            self.process_operators()
        elif rsrc == "ns":
            self.process_namespace()
        elif rsrc == "ic":
            self.process_cluster(name)
            self.process_operators()
        elif rsrc == "po" or rsrc == "pod":
            # this assumes each pod has a corresponding IC, this might not be
            # the case. For example readReplica pods only got an stateful set,
            # which will however be processed as well.
            cluster_name = self.extract_cluster_name_from_pod(name)
            self.process_cluster(cluster_name)
            self.process_operators()
        elif rsrc == "router":
            cluster_name = self.extract_cluster_name_from_routers_pattern(name)
            self.process_cluster(cluster_name)
            self.process_operators()
        else:
            self.process_generic_rsrc(rsrc, name)
            self.process_all_clusters()
            self.process_operators()

        logger.info(f"storing timeout diagnostics for {rsrc} {self.ns}/{name} completed")


def store_diagnostics(ns, rsrc, name):
    if name and name[0].isalpha():
        sd = StoreTimeoutDiagnostics(ns)
        sd.run(rsrc, name)

def store_operator_diagnostics(ns, name):
    store_diagnostics(ns, "operator", name)

def store_pod_diagnostics(ns, name):
    store_diagnostics(ns, "pod", name)

def store_routers_diagnostics(ns, name_pattern):
    store_diagnostics(ns, "router", name_pattern)

def store_ic_diagnostics(ns, name):
    store_diagnostics(ns, "ic", name)

def store_ns_diagnostics(ns):
    store_diagnostics(ns, "ns", ns)

#

def wait_pod_exists(ns, name, timeout=150, checkabort=lambda: None):
    logger.info(f"Waiting for pod {ns}/{name} to come up")
    for i in range(timeout):
        pods = ls_po(ns)
        for pod in pods:
            if pod["NAME"] == name:
                logger.info(f"{ns}/{name} is {pod['STATUS']}")
                return pod
        time.sleep(1)

    logger.info("%s", kubectl("get", "pod", args=[
                "-n", ns]).stdout.decode("utf8"))

    store_pod_diagnostics(ns, name)
    raise Exception(f"Timeout waiting for pod {ns}/{name}")


def wait_pod_gone(ns, name, timeout=120, checkabort=lambda: None):
    logger.info(f"Waiting for pod {ns}/{name} to disappear")
    i = 0
    last_state = None
    while i < timeout:
        pods = ls_po(ns)
        for pod in pods:
            if pod["NAME"] == name:
                if last_state != pod["STATUS"]:
                    if last_state and pod["STATUS"] == "Terminating":
                        # state just switched to Terminating, extend the timeout
                        i = 0
                    last_state = pod["STATUS"]
                break
        else:
            logger.info(f"{ns}/{name} is gone")
            return True
        time.sleep(1)
        i += 1

    logger.info("%s", kubectl("get", "pod", args=[
                "-n", ns]).stdout.decode("utf8"))

    store_pod_diagnostics(ns, name)
    raise Exception(f"Timeout waiting for pod {ns}/{name}")


def wait_pod(ns, name, status="Running", timeout=150, checkabort=lambda: None, checkready:bool=False):
    if type(status) not in (tuple, list):
        status = [status]

    def check_status(line):
        checkabort()
        logger.debug("%s", line)
        if line["STATUS"] in ("Error", "ImagePullBackOff", "ErrImageNeverPull", "CrashLoopBackOff") and line["STATUS"] not in status:
            store_pod_diagnostics(ns, name)
            raise Exception(f"Pod error: {line['STATUS']}")
        logger.debug(line)
        return (line["STATUS"] in status) and (checkready == False or line["READY"] == "True")

    wait_pod_exists(ns, name, timeout, checkabort)

    logger.info(f"Waiting for pod {ns}/{name} to become {status}")

    checkabort()
    r = watch(ns, "pod", name, check_status, timeout,
              format="custom-columns=NAME:.metadata.name,STATUS:.status.phase,READY:.status.conditions[?(@.type==\"mysql.oracle.com/ready\")].status")

    logger.info(f"{r}")

    return r


def wait_ic_exists(ns, name, timeout=60, checkabort=lambda: None):
    logger.info(f"Waiting for ic {ns}/{name} to come up")
    for i in range(timeout):
        checkabort()
        ics = ls_ic(ns)
        for ic in ics:
            if ic["NAME"] == name:
                logger.info(f"{ns}/{name} is {ic['STATUS']}")
                return ic
        time.sleep(1)

    logger.info("%s", kubectl("get", "ic", args=[
                "-n", ns]).stdout.decode("utf8"))

    store_ic_diagnostics(ns, name)
    raise Exception(f"Timeout waiting for ic {ns}/{name}")


def wait_ic_gone(ns, name, timeout=150, checkabort=lambda: None):
    logger.info(f"Waiting for ic {ns}/{name} to disappear")
    last_state = None
    i = 0
    while i < timeout:
        checkabort()
        ics = ls_ic(ns, ignore=['error: the server doesn\'t have a resource type "ic"'])
        for ic in ics:
            if ic["NAME"] == name:
                if last_state != ic["STATUS"]:
                    if last_state and ic["STATUS"] == "FINALIZING":
                        # state just switched to FINALIZING, extend the timeout
                        i = 0
                    last_state = ic["STATUS"]
                break
        else:
            logger.info(f"{ns}/{name} is gone")
            return True
        time.sleep(1)
        i += 1

    logger.info("%s", kubectl("get", "ic", args=[
                "-n", ns]).stdout.decode("utf8"))

    store_ic_diagnostics(ns, name)
    raise Exception(f"Timeout waiting for ic {ns}/{name}")


def wait_ic(ns, name, status=["ONLINE"], num_online=None, timeout=200, probe_time=None,
            checkabort=lambda: None):
    if type(status) not in (tuple, list):
        status = [status]

    def check_status(line):
        checkabort()
        logger.debug("checking status with %s", line)
        if probe_time is None or line["PROBETIME"] > probe_time:
            return line["STATUS"] in status and (num_online is None or int(line["ONLINE"]) >= num_online)
        return False

    wait_ic_exists(ns, name, timeout, checkabort)

    logger.info(
        f"Waiting for ic {ns}/{name} to become {status}, num_online={num_online}")

    checkabort()
    r = watch(ns, "ic", name, check_status, timeout,
              format="custom-columns=NAME:.metadata.name,STATUS:.status.cluster.status,ONLINE:.status.cluster.onlineInstances,PROBETIME:.status.cluster.lastProbeTime")

    logger.info(f"{r}")

    return r

#

def portfw(ns, name, in_port, target_type="pod"):
    for _ in range(5):
        p = kubectl_popen("port-forward", [f"{target_type}/{name}", ":%s" %
                                        in_port, "--address", "127.0.0.1", "-n", ns])
        line = p.stdout.readline().decode("utf8")
        logger.info(f"portfw: {line}")
        port = line.split("->")[0].split(":")[-1].strip()
        if port.isnumeric():
            return p, int(port)
        time.sleep(1)
        logger.debug(f"portfw incorrect port: {port}, retrying...")
    raise Exception("portfw failed")

#

class PortForward:
    def __init__(self, ns, podname, port, target_type="pod", **kwargs):
        self.proc = None
        self.proc, self.port = portfw(ns, podname, port, target_type=target_type)

    def __del__(self):
        self.close()

    def __enter__(self, *args):
        return self.port

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.proc:
            self.proc.terminate()
            self.proc = None

#


def create_ns(ns, labels = {}):
    kubectl("create", "namespace", [ns], ignore=["AlreadyExists"])
    if len(labels):
        labels_kv = [ f"{key}={value}" for key, value in labels.items()]
        kubectl("label", "namespace", [ns, *labels_kv])


def create_testpv(ns, name):
    yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {name}
  labels:
    type: local
spec:
  storageClassName: manual
  capacity:
    storage: 20Gi
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: "/tmp/data"
"""
    apply(ns, yaml)


def create_secrets(ns, name, data):
    nl = "\n"
    indent = "\n  "
    yaml = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {name}
data:
  {indent.join(data.strip().split(nl))}
"""
    apply(ns, yaml)


def adjust_key_file_path(cfg_path, cfg_key_file_path):
    cfg_key_file_path = os.path.expanduser(cfg_key_file_path)
    if os.path.isabs(cfg_key_file_path):
        return cfg_key_file_path

    # kubectl doesn't like relative paths
    cfg_dir = os.path.dirname(cfg_path)
    key_file_path = os.path.join(cfg_dir, cfg_key_file_path)

    return pathlib.Path(key_file_path).absolute()

def create_apikey_secret(ns, name, cfg_path, profile_name):
    import configparser
    ini_parser = configparser.ConfigParser()
    ini_parser.read(cfg_path)
    if not profile_name in ini_parser:
        raise Exception(f"{profile_name} profile not found")

    KEY_FILE_INI_OPTION_NAME = "key_file"
    options = [ "generic", name, "-n", ns]

    for ini_key, ini_value in ini_parser[profile_name].items():
        if ini_key != KEY_FILE_INI_OPTION_NAME:
            options.append(f"--from-literal={ini_key}={ini_value}")
        else:
            key_file_path = adjust_key_file_path(cfg_path, ini_value)
            if not os.path.isfile(key_file_path):
                raise Exception(f"{key_file_path} doesn't exist")
            options.append(f"--from-file=privatekey={key_file_path}")

    kubectl("create", "secret", options)


def create_secret_from_files(ns, name, data):
    options = [ "generic", name, "-n", ns]
    for key, path in data:
        options.append(f"--from-file={key}={path}")

    kubectl("create", "secret", options)


def create_ssl_ca_secret(ns, name, path, crlpath=None):
    data = [("ca.pem", path)]
    if crlpath:
        data.append(("crl.pem", crlpath))
    create_secret_from_files(ns, name, data)


def create_ssl_cert_secret(ns, name, cert_path, key_path):
    options = [ "tls", name, "-n", ns]
    options.append(f"--cert={cert_path}")
    options.append(f"--key={key_path}")

    kubectl("create", "secret", options)


def create_generic_secret(ns, name, key_path, local_key_path):
    options = [ "generic", name, "-n", ns]
    options.append(f"--from-file={key_path}={local_key_path}")

    kubectl("create", "secret", options)


def create_user_secrets(ns, name, root_user=None, root_host=None, root_pass=None, extra_keys=[]):
    data = []
    if root_user is not None:
        data.append(f"rootUser: {b64encode(root_user)}")
    if root_host is not None:
        data.append(f"rootHost: {b64encode(root_host)}")
    if root_pass is not None:
        data.append(f"rootPassword: {b64encode(root_pass)}")
    data += extra_keys
    create_secrets(ns, name, "\n".join(data))

def create_default_user_secrets(ns, name="mypwds", root_user="root", root_host="%", root_pass="sakila", extra_keys=[]):
    create_user_secrets(ns, name, root_user, root_host, root_pass, extra_keys)

def copy_secret(secret_ns, secret_name, dst_ns):
    src_secret_yaml = get_raw(secret_ns, "secret", secret_name)
    src_yaml = src_secret_yaml.splitlines()
    dst_yaml = []
    for line in src_yaml:
        if re.match("^\s*namespace:", line):
            continue
        dst_yaml.append(line)
    apply(dst_ns, '\n'.join(dst_yaml))


def create_cm(ns, name, data):
    nl = "\n"
    indent = "\n  "
    yaml = f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: {name}
data:
  {indent.join(data.strip().split(nl))}
"""
    apply(ns, yaml)


def create_pod():
    pass


if __name__ == "__main__":
    testdata = """NAMESPACE        NAME                                     READY   STATUS    RESTARTS   AGE
default          testpod                                  1/1     Running   0          38m
docker           compose-78f95d4f8c-pj4pl                 1/1     Running   0          7h32m
docker           compose-api-6ffb89dc58-2fpc2             1/1     Running   0          7h32m
kube-system      coredns-5644d7b6d9-qbjrv                 1/1     NotRunning   0          7h33m
kube-system      coredns-5644d7b6d9-vf6ft                 1/1     Running      0          7h33m
kube-system      etcd-docker-desktop                      1/1     Running      0          7h32m
kube-system      kube-apiserver-docker-desktop            1/1                  0          7h32m
kube-system      kube-controller-manager-docker-desktop   1/1     Running      0          7h32m
kube-system      kube-proxy-cxcgf                         1/1     Running      0          7h33m
kube-system      kube-scheduler-docker-desktop            1/1     Running      0          7h32m
kube-system      storage-provisioner                      1/1     Running      0          7h32m
kube-system      vpnkit-controller                        1/1     Running      0          7h32m
mysql-operator   mysql-operator-5bfb6dfdb7-mj5tx          1/1     Running      0          6h20m
"""

    lines = testdata.strip().split("\n")
    splitter = TableSplitter(lines[0])
    for l in lines[1:]:
        p = splitter.split(l)
        logger.debug(p)
        assert len(p) == len(splitter.columns)
