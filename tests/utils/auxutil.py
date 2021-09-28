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
