# Copyright (c) 2023, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import json
import logging
import unittest

import requests

from utils import tutil, kutil, mutil

from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log
from setup.config import g_ts_cfg, Config
from utils.auxutil import isotime

@unittest.skipIf(not g_ts_cfg.get_image(Config.Image.METRICS), "No image for metrics sidecar specified")
class ClusterMetricsTest(tutil.OperatorTest):
    """
    spec errors checked during admission (by CRD schema or webhook)
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    instances = 2

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    def tearDown(self):
        # none of the tests should create anything
        #self.assertEqual([], kutil.ls_ic(self.ns))
        #self.assertEqual([], kutil.ls_sts(self.ns))
        #self.assertEqual([], kutil.ls_po(self.ns))

        return super().tearDown()

    def test_00_create(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%",
            root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {self.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  metrics:
      enable: true
      image: {g_ts_cfg.get_image(Config.Image.METRICS)}
  podSpec:
    terminationGracePeriodSeconds: 10
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])
        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")
        self.wait_ic("mycluster", "ONLINE", num_online=self.instances)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=f"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

        for instance in range(1, self.instances):
            expected_msg = f"Joining mycluster-{instance} to cluster"
            with self.subTest(expected_msg):
                self.assertGotClusterEvent("mycluster", after=apply_time, type="Normal", reason=r"Join", msg=expected_msg)

    def test_02_check_metrics_reachable_via_service_and_connects_to_db(self):
        with kutil.PortForward(self.ns, "mycluster-instances", "metrics", "service") as port:
            resp = requests.get(f'http://127.0.0.1:{port}/metrics')
            self.assertEqual(resp.status_code, 200)

            self.assertIn("mysql_global_variables_gtid_mode 1", resp.text,
                          msg="Expected config setting not reported, maybe database connection failed?")

    def test_04_mysql_user_created_on_all_pods(self):
        for pod in ('mycluster-0', 'mycluster-1'):
            with self.subTest(pod):
                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s:
                    user = s.query_sql(
                        "SELECT User, Host, plugin, authentication_string"
                        " FROM mysql.user"
                        " WHERE User='mysqlmetrics'").fetch_all()
                    self.assertListEqual([("mysqlmetrics", "localhost", "auth_socket", b"daemon")], user)

    def test_06_enable_config(self):
        old_pod_uid = kutil.get_po(self.ns, "mycluster-0")["metadata"]["uid"]

        # configuration for the exporter using alice:alice as credentials
        web_config = """
web.config: |
    basic_auth_users:
      alice: '$2y$10$v4kAPAxETqQGmNlwUrqsN.a46uwg3MBDcNew.2KQA8M73azAGEJ2O'
"""
        kutil.create_cm(self.ns, "cm-web-config", web_config)

        patch = {
            "spec": {
                "metrics": {
                    "webConfig": "cm-web-config"
                }
            }
        }

        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=300, delay=20)
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        waiter()
        self.wait_ic("mycluster", "ONLINE", num_online=self.instances)

        for instance in reversed(range(0, self.instances)):
            pod = f"mycluster-{instance}"
            self.wait_pod(pod, "Running")

            podspec = kutil.get_po(self.ns, pod)

            self.assertEqual(3, len(podspec["spec"]["containers"]))

            self.assertIn("metrics-web-config",
                          map(lambda volume: volume["name"],
                              podspec["spec"]["volumes"]))

            with kutil.PortForward(self.ns, pod, "metrics") as port:
                resp = requests.get(f'http://127.0.0.1:{port}/metrics')
                self.assertEqual(resp.status_code, 401)

                resp = requests.get(f'http://127.0.0.1:{port}/metrics',
                                    auth=("alice", "alice"))
                self.assertEqual(resp.status_code, 200)

    def test_08_disable(self):
        old_pod_uid = kutil.get_po(self.ns, "mycluster-0")["metadata"]["uid"]

        patch = {
            "spec": {
                "metrics": {
                    "enable": False
                }
            }
        }

        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=300, delay=20)
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        waiter()
        self.wait_ic("mycluster", "ONLINE", num_online=self.instances)

        for instance in reversed(range(0, self.instances)):
            pod = f"mycluster-{instance}"
            self.wait_pod(pod, "Running")

        for instance in range(0, self.instances):
            pod = f"mycluster-{instance}"
            with self.subTest(pod):
                podspec = kutil.get_po(self.ns, pod)
                self.assertEqual(2, len(podspec["spec"]["containers"]))
                self.assertNotIn("metrics", (c["name"] for c in podspec["spec"]["containers"]))

                print(podspec["spec"]["volumes"])
                self.assertNotIn("metrics-web-config", (v["name"] for v in podspec["spec"]["containers"]))

                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s:
                    user = s.query_sql(
                        "SELECT User, Host, plugin, authentication_string"
                        " FROM mysql.user"
                        " WHERE User='mysqlmetrics'").fetch_all()
                    self.assertListEqual([], user)

        svc = kutil.get_svc(self.ns, "mycluster-instances")
        print(svc["spec"]["ports"])
        self.assertNotIn("metrics", (p["name"] for p in svc["spec"]["ports"]))


    def test_10_metrics_user_will_be_created_on_clone(self):
        """Make sure a clone will reset metrics user credentials
        The server we are cloning from got no 'mysqlmetrics' user, thus
        after clone there will be no user and it ahs to be reset"""

        kutil.create_user_secrets(
            self.ns, "donorpwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: copycluster
spec:
  instances: 1
  router:
    instances: 0
  secretName: mypwds
  tlsUseSelfSigned: true
  initDB:
    clone:
      donorUrl: root@mycluster-0.mycluster-instances.{self.ns}.svc.cluster.local:3306
      secretKeyRef:
        name: donorpwds
  metrics:
      enable: true
      image: {g_ts_cfg.get_image(Config.Image.METRICS)}
  podSpec:
    terminationGracePeriodSeconds: 10
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("copycluster-0", "Running")

        self.wait_ic("copycluster", "ONLINE", 1, timeout=300)

        with mutil.MySQLPodSession(self.ns, "copycluster-0", "root", "sakila") as s:
            user = s.query_sql(
                "SELECT User, Host, plugin, authentication_string"
                " FROM mysql.user"
                " WHERE User='mysqlmetrics'").fetch_all()
            self.assertListEqual([("mysqlmetrics", "localhost", "auth_socket", b"daemon")], user)


    def test_99_shutdown(self):
        kutil.delete_ic(self.ns, "mycluster")
        kutil.delete_ic(self.ns, "copycluster")
        kutil.delete_cm(self.ns, "cm-web-config")
        kutil.delete_cm(self.ns, "donorpwds")
        kutil.delete_default_secret(self.ns)
        self.wait_pods_gone("mycluster-*")
        self.wait_pods_gone("copycluster-*")
        self.wait_ic_gone("mycluster")
        self.wait_ic_gone("copycluster")
