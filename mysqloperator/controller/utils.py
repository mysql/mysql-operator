# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
import sys
import os
import threading
import platform
import socket
import datetime
import time
import string
import random
import base64
import json
import hashlib
from logging import Logger
import importlib.metadata

from . import config

def b64decode(s: str) -> str:
    return base64.b64decode(s).decode("utf8")

def b64encode(s: str) -> str:
    return base64.b64encode(bytes(s, "utf8")).decode("ascii")

def sha256(s: str) -> str:
    return hashlib.sha256(bytes(s, "utf8")).hexdigest()


class EphemeralState:
    # State that's not persisted between operator restarts
    # Use only if get() returning None is interpreted as "skip optimization"
    def __init__(self):
        self.data = {}
        self.context = {}
        self.time = {}
        self.lock = threading.Lock()

    def get(self, obj, key: str):
        key = obj.namespace+"/"+obj.name+"/"+key
        with self.lock:
            return self.data.get(key)

    def testset(self, obj, key: str, value, context: str):
        key = obj.namespace+"/"+obj.name+"/"+key
        with self.lock:
            old_data = self.data.get(key)
            old_context = self.context.get(key)
            old_time = self.time.get(key)
            if old_data is None:
                self.data[key] = value
                self.context[key] = context
                self.time[key] = datetime.datetime.now()
        return (old_data, old_context, old_time)

    def set(self, obj, key: str, value, context: str) -> None:
        key = obj.namespace+"/"+obj.name+"/"+key
        with self.lock:
            self.data[key] = value
            self.context[key] = context
            self.time[key] = datetime.datetime.now()


g_ephemeral_pod_state = EphemeralState()


def ephemeral_value_changed(obj, key: str, value, context: str) -> bool:
    """
    Prime the ephemeral cache on the first observation and report only real
    changes after that.
    """
    previous, _, _ = g_ephemeral_pod_state.testset(obj, key, value, context=context)
    return previous is not None and previous != value


def thread_tag() -> str:
    return f"tid={threading.get_ident()}"


def log_with_thread(message: str, **fields) -> str:
    parts = [thread_tag()]
    for key, value in fields.items():
        if value is not None:
            parts.append(f"{key}={value}")
    parts.append(message)
    return " ".join(parts)


def format_mysql_target(target: dict) -> str:
    user = target.get("user", "?")
    if target.get("host"):
        endpoint = target["host"]
        if target.get("port") is not None:
            endpoint = f"{endpoint}:{target['port']}"
    else:
        endpoint = target.get("socket", "?")

    return f"{user}@{endpoint}"


def isotime() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"


def timestamp(dash: bool = True, four_digit_year: bool = True) -> str:
    dash_str = "-" if dash else ""
    year_str = "%Y" if four_digit_year else "%y"
    return datetime.datetime.utcnow().replace(microsecond=0).strftime(f"{year_str}%m%d{dash_str}%H%M%S")


def merge_patch_object(base: dict, patch: dict, prefix: str = "", key: str = "", none_deletes: bool = False) -> None:
    assert not key, "not implemented"  # TODO support key
    if type(base) != type(patch):
        raise ValueError(f"Invalid type in patch at {prefix}")
    if type(base) != dict:
        raise ValueError(f"Invalid type in base at {prefix}")

    def get_named_object(l, name):
        for o in l:
            assert type(o) == dict, f"{prefix}: {name} = {o}"
            if o["name"] == name:
                return o
        return None

    for k, v in patch.items():
        ov = base.get(k)

        if ov is not None:
            if type(ov) == dict:
                if type(v) != dict:
                    # TODO
                    raise ValueError(f"Invalid type in {prefix}")
                else:
                    merge_patch_object(ov, v, prefix+"."+k, none_deletes=none_deletes)
            elif type(ov) == list:
                if type(v) != list:
                    # TODO
                    raise ValueError(f"Invalid type in {prefix}")
                else:
                    if not ov:
                        base[k] = v
                    else:
                        if type(v[0]) != dict:
                            base[k] = v
                        else:
                            # When merging lists of objects, we matching objects by name
                            # If there's no matching object, we append
                            # If there's a matching object, recursively patch
                            for i, elem in enumerate(v):
                                if type(elem) != dict:
                                    raise ValueError(
                                        f"Invalid type in {prefix}")
                                name = elem.get("name")
                                if not name:
                                    raise ValueError(
                                        "Object in list must have name")
                                o = get_named_object(ov, name)
                                if o:
                                    merge_patch_object(
                                        o, elem, prefix+"."+k+"["+str(i)+"]",
                                        none_deletes=none_deletes)
                                else:
                                    ov.append(elem)

            elif type(ov) not in (dict, list) and type(v) in (dict, list):
                raise ValueError(f"Invalid type in {prefix}")
            else:
                if none_deletes and v is None:
                    del base[k]
                else:
                    base[k] = v
        else:
            if none_deletes and v is None:
                pass
            else:
                base[k] = v


def generate_password() -> str:
    random.seed(int(str(time.time()).split(".")[-1]))
    return "-".join("".join(random.choice(string.ascii_letters+string.digits+"_.=+-~") for i in range(5)) for ii in range(5))


def version_to_int(version: str) -> int:
    # x.y.z[.w]
    parts = version.split(".")
    if len(parts) > 4 or len(parts) < 3:
        raise ValueError(
            f"Invalid version number {version}. Must be n.n.n or n.n.n.n")

    parts = [int(p) for p in parts]

    # allow the last digit to be as long as a date value
    if len(parts) > 3:
        return parts[0] * 1000000000000 + parts[1] * 10000000000 + parts[2] + 100000000 + parts[3]
    else:
        return parts[0] * 1000000000000 + parts[1] * 10000000000 + parts[2] + 100000000

def version_in_range(version: str, minimum = None, maximum = None, check_disabled = True) -> list[bool, str]:
    if not minimum:
        minimum = config.MIN_SUPPORTED_MYSQL_VERSION

    if not maximum:
        maximum = config.MAX_SUPPORTED_MYSQL_VERSION

    # Some versions have been disabled due to major issues
    if check_disabled and version in config.DISABLED_MYSQL_VERSION:
        return [False, config.DISABLED_MYSQL_VERSION[version]]


    version_int = version_to_int(version)
    min_version = version_to_int(minimum)
    max_version = version_to_int(maximum)

    if not max_version >= version_int >= min_version:
        return [False,
            f"version {version} must be between "
            f"{minimum} and {maximum}"]

    return [True, None]

def indent(s: str, spaces: int) -> str:
    if s:
        ind = "\n" + " "*spaces
        return " " * spaces + ind.join(s.split("\n"))
    return ""

def get_mem_limits_in_gb():
    raw = None

    for path in (
        "/sys/fs/cgroup/memory.max",
        "/sys/fs/cgroup/memory/memory.limit_in_bytes",
    ):
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
                break
        except OSError:
            continue

    if not raw or raw == "max":
        return "Unlimited"

    try:
        limit = int(raw)
    except ValueError:
        return "Unlimited"

    if limit > 10**15:
        return "Unlimited"
    return f"{limit / (1024**3):.2f}"

def get_cpu_limits():
    try:
        # First we try with cgroup v2
        with open('/sys/fs/cgroup/cpu.max', 'r') as f:
            quota, period = f.read().split()
            if quota == "max": return os.cpu_count()
            return float(quota) / float(period)
    except (OSError, ValueError):
        # Then if this is an old system we try ith cgroup v1
        try:
            with open('/sys/fs/cgroup/cpu/cpu.cfs_quota_us') as q, \
                 open('/sys/fs/cgroup/cpu/cpu.cfs_period_us') as p:
                quota = int(q.read())
                period = int(p.read())
                return quota / period if quota > 0 else os.cpu_count()
        except (OSError, ValueError):
            return os.cpu_count()

def log_banner(path: str, logger: Logger) -> None:
    from . import config

    kopf_version = importlib.metadata.version("kopf")
    ts = datetime.datetime.fromtimestamp(os.stat(path).st_mtime).isoformat()

    path = os.path.basename(path)
    logger.info(f"MySQL Operator/{path}={config.OPERATOR_VERSION}  Python={sys.version.split()[0]}  timestamp={ts}  kopf={kopf_version}  uid={os.getuid()}")

    platform_info = platform.uname()._asdict()
    hostname = socket.gethostname()
    try:
        node_ip = socket.gethostbyname(hostname)
    except Exception as exc:
        node_ip = f"unresolved ({exc})"

    logger.info(f"cpu={platform.processor()}   kernel={platform_info['release']}")
    logger.info(f"cpu_limits:{get_cpu_limits()}  memory_limits={get_mem_limits_in_gb()}")
    logger.info(f"node={platform_info['node']}  ip={node_ip}")

def dict_to_json_string(d : dict) -> str:
    return json.dumps(d, indent = 4)
