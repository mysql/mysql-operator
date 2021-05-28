# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

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
