# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
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

from utils import kutil


# Check connect through router

router_rwport: int = 6446
router_roport: int = 6447
router_rwxport: int = 6448
router_roxport: int = 6449


def test_read_only_routing(test, host, port, user, password):
    pass


def test_read_write_routing(test, host, port, user, password):
    pass


def check_routing_direct(test, router_pod, user, password):
    """
    Ensure that connecting to the incoming router ports will get us to an
    expected instance.
    """
    host = None

    test_read_write_routing(test, host, router_rwport, user, password)
    test_read_write_routing(test, host, router_rwxport, user, password)
    test_read_only_routing(test, host, router_roport, user, password)
    test_read_only_routing(test, host, router_roxport, user, password)


def check_routing_service(test, router_pod, user, password):
    pass


def check_routing(test, ic, user, password):
    pass


def check_pods(test, ns, name, num_pods):
    pods = kutil.ls_po(ns, pattern=f"{name}-router-.*")
    test.assertEqual(len(pods), num_pods)


# Check


# Check direct connect to each member


# Check DNS-SRV records


def check_pod_reachable(test, ns, cluster_name, pod):
    pass


def check_pod_unreachable(test, ns, cluster_name, pod):
    pass
