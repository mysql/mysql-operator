# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import base64
import datetime
import socket
from urllib.parse import urlparse
from ipaddress import IPv4Address

def isotime() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

def b64encode(s: str) -> str:
    return base64.b64encode(bytes(s, "utf8")).decode("ascii")

def resolve_host_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    # it needn't be reachable
    s.connect(('10.255.255.255', 1))
    host_ip = s.getsockname()[0]
    s.close()
    return host_ip

def resolve_registry_url(registry_url):
    if "://" not in registry_url:
        registry_url = "http://" + registry_url

    parsed_registry_url = urlparse(registry_url)

    host = parsed_registry_url.hostname
    port = parsed_registry_url.port

    host_address = socket.gethostbyname(host)
    host_ip4 = IPv4Address(host_address)
    is_loopback = host_ip4.is_loopback
    return host, port, is_loopback

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
