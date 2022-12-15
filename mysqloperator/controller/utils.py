# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import datetime
import time
import os
import string
import random
import base64
import threading
import json
import hashlib

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


def isotime() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"


def timestamp(dash: bool = True, four_digit_year: bool = True) -> str:
    dash_str = "-" if dash else ""
    year_str = "%Y" if four_digit_year else "%y"
    return datetime.datetime.utcnow().replace(microsecond=0).strftime(f"{year_str}%m%d{dash_str}%H%M%S")


def merge_patch_object(base: dict, patch: dict, prefix: str = "", key: str = "") -> None:
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
                    merge_patch_object(ov, v, prefix+"."+k)
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
                                        o, elem, prefix+"."+k+"["+str(i)+"]")
                                else:
                                    ov.append(elem)

            elif type(ov) not in (dict, list) and type(v) in (dict, list):
                raise ValueError(f"Invalid type in {prefix}")
            else:
                base[k] = v
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


def log_banner(path: str, logger) -> None:
    import pkg_resources
    from . import config

    kopf_version = pkg_resources.get_distribution('kopf').version
    ts = datetime.datetime.fromtimestamp(os.stat(path).st_mtime).isoformat()

    path = os.path.basename(path)
    logger.info(
        f"MySQL Operator/{path}={config.OPERATOR_VERSION} timestamp={ts} kopf={kopf_version} uid={os.getuid()}")


def dict_to_json_string(d : dict) -> str:
    return json.dumps(d, indent = 4)
