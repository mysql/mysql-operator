# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# Kubernetes (kubectl) utilities

# Use kubectl instead of the API so we go through the same code path as an end-user

import subprocess
import logging
import threading
import time
import re
import yaml
import base64

logger = logging.getLogger("kutil")

debug_kubectl = False

ALL_RSRC_TYPES = ["ic", "mbk", "po", "sts", "rs",
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


def kubectl(cmd, rsrc=None, args=None, timeout=None, check=True, ignore=[]):
    argv = ["kubectl", cmd]
    if rsrc:
        argv.append(rsrc)
    if args:
        argv += args
    if debug_kubectl:
        logger.debug("run %s", " ".join(argv))
    try:
        r = subprocess.run(argv, timeout=timeout,
                           check=check, capture_output=True)
    except subprocess.CalledProcessError as e:
        for ig in ignore:
            if "(%s)" % ig in e.stderr.decode("utf8"):
                if debug_kubectl:
                    logger.debug("rc = %s, stderr=%s",
                                 e.returncode, e.stderr.decode("utf8"))
                return
        else:
            logger.error("kubectl %s failed:\n    stderr=%s\n    stdout=%s",
                         e.cmd, e.stderr.decode("utf8"), e.stdout.decode("utf8"))
            raise
    if debug_kubectl:
        logger.debug("rc = %s, stdout = %s", r.returncode,
                     r.stdout.decode("utf8"))
    return r


def kubectl_popen(cmd, args=[]):
    argv = ["kubectl", cmd] + args

    if debug_kubectl:
        logger.debug("popen %s", " ".join(argv))

    return subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


def watch(ns, rsrc, name, fn, timeout, format=None):
    argv = ["kubectl", "get", rsrc, "-n", ns, "--watch", "-o%s" % format]
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

    if debug_kubectl:
        logger.debug("rc = %s, stdout = %s", p.returncode, output)

    return found


def feed_kubectl(input, cmd, rsrc=None, args=None, check=True):
    argv = ["kubectl", cmd]
    if rsrc:
        argv.append(rsrc)
    if args:
        argv += args
    if debug_kubectl:
        logger.debug("run %s", argv)
    r = subprocess.run(argv, input=input.encode("utf8"),
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       check=check)
    print(r.stdout.decode("utf8"))
    if debug_kubectl:
        logger.debug("rc = %s", r)
    return r

#


def __ls(ns, rsrc):
    return split_table(kubectl("get", rsrc, args=["-n", ns]).stdout.decode("utf8"))


def ls_ic(ns):
    return __ls(ns, "ic")


def ls_mbk(ns):
    return __ls(ns, "mbk")


def ls_sts(ns):
    return __ls(ns, "sts")


def ls_rs(ns):
    return __ls(ns, "rs")


def ls_svc(ns):
    return __ls(ns, "svc")


def ls_po(ns, *, pattern=".*"):
    pods = __ls(ns, "po")
    r = re.compile(pattern)
    return [pod for pod in pods if r.match(pod["NAME"])]


def ls_pvc(ns):
    return __ls(ns, "pvc")


def ls_pv(ns):
    return __ls(ns, "pv")


def ls_all_raw(ns):
    output = []
    for t in ALL_RSRC_TYPES:
        r = kubectl("get", t, args=["-n", ns]).stdout.decode("utf8")
        if r and t == "secret":
            # strip automatically added default token
            lines = [l for l in r.strip().split(
                "\n") if not l.startswith("default-token-")]
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

#


def get(ns, rsrc, name):
    r = kubectl("get", rsrc, args=[name, "-n", ns, "-o=yaml"])
    if r.stdout:
        return yaml.safe_load(r.stdout.decode("utf8"))
    return None


def get_ic(ns, name, jpath=None):
    return get(ns, "ic", name)


def get_mbk(ns, name, jpath=None):
    return get(ns, "mbk", name)


def get_sts(ns, name, jpath=None):
    return get(ns, "sts", name)


def get_rs(ns, name, jpath=None):
    return get(ns, "rs", name)


def get_svc(ns, name, jpath=None):
    return get(ns, "svc", name)


def get_po(ns, name, jpath=None):
    return get(ns, "po", name)


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
                "-n", ns, "-o=yaml"])

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


def describe_po(ns, name, jpath=None):
    r = kubectl("describe", "po", [name, "-n", ns])
    if r.stdout:
        return r.stdout.decode("utf8")
    raise Exception(f"Error for describe {ns}/{name}")


def describe_ic(ns, name):
    r = kubectl("describe", "ic", [name, "-n", ns])
    if r.stdout:
        return r.stdout.decode("utf8")
    raise Exception(f"Error for describe {ns}/{name}")

#


def delete(ns, rsrc, name, timeout):
    if not name:
        name = "--all"
    kubectl("delete", rsrc, [name] + (["-n", ns]
                                      if ns else []), timeout=timeout, ignore=["NotFound"])


def delete_ic(ns, name, timeout=200):
    delete(ns, "ic", name, timeout=timeout)


def delete_po(ns, name, timeout=120):
    delete(ns, "po", name, timeout=timeout)


def delete_sts(ns, name, timeout=5):
    delete(ns, "sts", name, timeout=timeout)


def delete_rs(ns, name, timeout=5):
    delete(ns, "rs", name, timeout=timeout)


def delete_svc(ns, name, timeout=5):
    delete(ns, "svc", name, timeout=timeout)


def delete_pvc(ns, name, timeout=60):
    delete(ns, "pvc", name, timeout=timeout)


def delete_pv(name, timeout=60):
    delete(None, "pv", name, timeout=timeout)


def delete_ns(ns, timeout=90):
    delete(None, "ns", ns, timeout=timeout)


def delete_cm(ns, name, timeout=5):
    delete(ns, "cm", name, timeout=timeout)


def delete_secret(ns, name, timeout=5):
    delete(ns, "secret", name, timeout=timeout)

#


def logs(ns, name, prev=False):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]
    if prev:
        args.append("-p")
    return kubectl("logs", None, args + ["-n", ns]).stdout.decode("utf8")


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


def exec(ns, name, cmd):
    if type(name) is str:
        args = [name]
    else:
        args = [name[0], "-c", name[1]]
    kubectl("exec", None, args + ["-n", ns, "--"] + cmd)


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


def apply(ns, yaml, *, check=True):
    try:
        return feed_kubectl(strip_blanks(yaml), "apply", args=[
            "-n", ns, "-f", "-"], check=check)
    except subprocess.CalledProcessError as e:
        print(e.stdout.decode("utf8"))
        raise


def patch(ns, rsrc, name, changes, type=None):
    kubectl("patch", rsrc, [name, "-p", yaml.dump(changes),
                            "-n", ns] + (["--type=%s" % type] if type else []))


def patch_pod(ns, name, changes, type=None):
    patch(ns, "pod", name, changes, type)


def patch_ic(ns, name, changes, type=None):
    patch(ns, "ic", name, changes, type)

#


def ls_nodes():
    return split_table(kubectl("get", "nodes").stdout.decode("utf8"))


def node_pods():
    return split_table(kubectl("get", "nodes").stdout.decode("utf8"))


def drain_node(node):
    pass

#


def wait_pod_exists(ns, name, timeout=120, checkabort=lambda: None):
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

    raise Exception(f"Timeout waiting for pod {ns}/{name}")


def wait_pod(ns, name, status="Running", timeout=120, checkabort=lambda: None):
    if type(status) not in (tuple, list):
        status = [status]

    def check_status(line):
        checkabort()
        logger.debug("%s", line)
        if line["STATUS"] in ("Error", "ImagePullBackOff", "ErrImageNeverPull", "CrashLoopBackOff") and line["STATUS"] not in status:
            raise Exception(f"Pod error: {line['STATUS']}")
        print(line)
        return line["STATUS"] in status

    wait_pod_exists(ns, name, timeout, checkabort)

    logger.info(f"Waiting for pod {ns}/{name} to become {status}")

    checkabort()
    r = watch(ns, "pod", name, check_status, timeout,
              format="custom-columns=NAME:.metadata.name,STATUS:.status.phase")

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

    raise Exception(f"Timeout waiting for ic {ns}/{name}")


def wait_ic_gone(ns, name, timeout=120, checkabort=lambda: None):
    logger.info(f"Waiting for ic {ns}/{name} to disappear")
    last_state = None
    i = 0
    while i < timeout:
        checkabort()
        ics = ls_ic(ns)
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

    raise Exception(f"Timeout waiting for ic {ns}/{name}")


def wait_ic(ns, name, status=["ONLINE"], num_online=None, timeout=200, checkabort=lambda: None):
    if type(status) not in (tuple, list):
        status = [status]

    def check_status(line):
        checkabort()
        logger.debug("%s", line)
        return line["STATUS"] in status and (num_online is None or line["ONLINE"] >= str(num_online))

    wait_ic_exists(ns, name, timeout, checkabort)

    logger.info(
        f"Waiting for ic {ns}/{name} to become {status}, num_online={num_online}")

    checkabort()
    r = watch(ns, "ic", name, check_status, timeout,
              format="custom-columns=NAME:.metadata.name,STATUS:.status.cluster.status,ONLINE:.status.cluster.onlineInstances")

    logger.info(f"{r}")

    return r

#


def portfw(ns, name, in_port):
    p = kubectl_popen("port-forward", ["pod/%s" % name, ":%s" %
                                       in_port, "--address", "127.0.0.1", "-n", ns])
    line = p.stdout.readline().decode("utf8")
    return p, int(line.split("->")[0].split(":")[-1].strip())

#


def create_ns(ns):
    kubectl("create", "namespace", [ns], ignore=["AlreadyExists"])


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


def create_apikey_secret(ns, name, path, config_name = "config", profile_name = "DEFAULT", privatekey = "key.pem"):
    import configparser
    ini_parser = configparser.ConfigParser()
    ini_parser.read(f"{path}/{config_name}")
    if not profile_name in ini_parser:
        raise Exception(f"{profile_name} profile not found")

    # kubectl doesn't like relative paths
    path = pathlib.Path(path).expanduser().absolute()

    if not os.path.isfile(f"{path}/{privatekey}"):
        raise Exception(f"{path}/{privatekey} doesn't exist")


    KEY_FILE_INI_OPTION_NAME = "key_file"
    options = [ "generic", name, "-n", ns]
    options.append(f"--from-file=privatekey={path}/{privatekey}")

    for ini_key, ini_value in ini_parser[profile_name].items():
        if ini_key != KEY_FILE_INI_OPTION_NAME:
            options.append(f"--from-literal={ini_key}={ini_value}")

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
        print(p)
        assert len(p) == len(splitter.columns)
