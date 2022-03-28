# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS


def check_sidecar_health(test, ns, pod):
    logs = kutil.logs(ns, [pod, "sidecar"])
    # check that the sidecar is running and waiting for events
    test.assertIn("Starting Operator request handler...", logs)



# Test 1 member cluster with all default configs
class Router(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-0")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-2")

        super().tearDownClass()

    def test_0_create(self):
        """
        Create cluster with router
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 3
  router:
    instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "ONLINE", num_online=3)

        self.wait_routers("mycluster-router-.*", 1)

        # check that router pods didn't restart, which could be a side-effect
        # of router replicaset being created before the cluster is ready
        pods = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        self.assertEqual(1, len(pods))
        self.assertEqual('0', pods[0]["RESTARTS"])


    def test_1_load_distribution(self):
        def check(port, hosts_expected):
            # checks that we connect to both secondaries at least once
            h = tutil.run_from_operator_pod(f"mysql://root:sakila@mycluster.testns.svc.cluster.local:{port}",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
            print(h)
            hosts_expected.remove(h)
            return len(hosts_expected) == 0

        # ensure connecting to 6446 takes us to the primary
        hosts_expected = set(["mycluster-0"])
        self.wait(check, (6446, hosts_expected))

        # ensure connecting to 6447 takes us to all secondaries
        hosts_expected = set(["mycluster-1", "mycluster-2"])
        self.wait(check, (6447, hosts_expected))


    def test_2_stopped_members(self):
        """
        Ensure that we don't get to a non-ONLINE member when connecting.
        """

        def check(secondaries_seen):
            # checks that we connect to both secondaries at least once
            h = tutil.run_from_operator_pod("mysql://root:sakila@mycluster.testns.svc.cluster.local:6447",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
            secondaries_seen.add(h)
            return len(secondaries_seen) == 2

        # stop GR on one of the members
        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s:
            s.exec_sql("stop group_replication")

        # connect through the router
        h = tutil.run_from_operator_pod("root:sakila@mycluster.testns.svc.cluster.local:6446",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
        self.assertEqual("mycluster-0", h)

        for _ in range(5):
            h = tutil.run_from_operator_pod("root:sakila@mycluster.testns.svc.cluster.local:6447",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
            self.assertIn(h, ["mycluster-2"])

        # start GR back and ensure it's returned to the pool
        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s:
            s.exec_sql("start group_replication")

        secondaries_seen = set()
        self.wait(check, (secondaries_seen,), timeout=300)



    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
