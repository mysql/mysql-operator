# Copyright (c) 2020, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import datetime
import time
import os
import string
import random
import base64
import threading


def b64decode(s: str) -> str:
    return base64.b64decode(s).decode("utf8")


def b64encode(s: str) -> str:
    return base64.b64encode(bytes(s, "utf8")).decode("ascii")


class EphemeralState:
    # State that's not persisted between operator restarts
    # Use only if get() returning None is interpreted as "skip optimization"
    def __init__(self):
        self.data = {}
        self.lock = threading.Lock()

    def get(self, obj, key: str):
        key = obj.namespace+"/"+obj.name+"/"+key
        with self.lock:
            return self.data.get(key)

    def testset(self, obj, key: str, value):
        key = obj.namespace+"/"+obj.name+"/"+key
        with self.lock:
            old = self.data.get(key)
            if old is None:
                self.data[key] = value
        return old

    def set(self, obj, key: str, value) -> None:
        key = obj.namespace+"/"+obj.name+"/"+key
        with self.lock:
            self.data[key] = value


g_ephemeral_pod_state = EphemeralState()


def isotime() -> str:
    return datetime.datetime.now().replace(microsecond=0).isoformat()+"Z"


def timestamp() -> str:
    return datetime.datetime.now().replace(microsecond=0).strftime("%Y%m%d-%H%M%S")


def merge_patch_object(base: dict, patch: dict, prefix: str = "", key: str = "") -> None:
    assert not key, "not implemented"  # TODO support key

    if type(base) != type(patch):
        raise ValueError(f"Invalid type in {prefix}")
    if type(base) != dict:
        raise ValueError(f"Invalid type in {prefix}")

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


def indent(s: str, spaces: int) -> str:
    ind = "\n" + " "*spaces
    return " " * spaces + ind.join(s.split("\n"))


def log_banner(path: str, logger) -> None:
    import pkg_resources
    from . import config

    kopf_version = pkg_resources.get_distribution('kopf').version
    ts = datetime.datetime.fromtimestamp(os.stat(path).st_mtime).isoformat()

    path = os.path.basename(path)
    logger.info(
        f"MySQL Operator/{path}={config.OPERATOR_VERSION} timestamp={ts} kopf={kopf_version}")
