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
    _cluster_size = 2
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def tearDown(self):
        # none of the tests should create anything
        #self.assertEqual([], kutil.ls_ic(self.ns))
        #self.assertEqual([], kutil.ls_sts(self.ns))
        #self.assertEqual([], kutil.ls_po(self.ns))

        return super().tearDown()

    def test_00_create(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  metrics:
      enable: true
      image: {g_ts_cfg.get_image(Config.Image.METRICS)}
  podSpec:
    terminationGracePeriodSeconds: 10
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=f"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

        for instance in range(1, self.cluster_size):
            expected_msg = f"Joining {self.cluster_name}-{instance} to cluster"
            with self.subTest(expected_msg):
                self.assertGotClusterEvent(self.cluster_name, after=apply_time, type="Normal", reason=r"Join", msg=expected_msg)

    def test_02_check_metrics_reachable_via_service_and_connects_to_db(self):
        with kutil.PortForward(self.ns,f"{self.cluster_name}-instances", "metrics", "service") as port:
            resp = requests.get(f'http://127.0.0.1:{port}/metrics')
            self.assertEqual(resp.status_code, 200)

            self.assertIn("mysql_global_variables_gtid_mode 1", resp.text,
                          msg="Expected config setting not reported, maybe database connection failed?")

    def test_04_mysql_user_created_on_all_pods(self):
        for instance in range(0, self.cluster_size):
            pod = f"{self.cluster_name}-{instance}"
            with self.subTest(pod):
                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s:
                    user = s.query_sql(
                        "SELECT User, Host, plugin"
                        " FROM mysql.user"
                        " WHERE User='mysqlmetrics'").fetch_all()
                    self.assertListEqual([("mysqlmetrics", "localhost", "auth_socket")], user)

    def _test_enable_config(self, cm_web_config_name: str):
        # configuration for the exporter using alice:alice as credentials
        web_config = """
web.config: |
    basic_auth_users:
      alice: '$2y$10$v4kAPAxETqQGmNlwUrqsN.a46uwg3MBDcNew.2KQA8M73azAGEJ2O'
"""
        kutil.create_cm(self.ns, cm_web_config_name, web_config)

        patch = {
            "spec": {
                "metrics": {
                    "webConfig": cm_web_config_name
                }
            }
        }

        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=20)
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        for instance in reversed(range(0, self.cluster_size)):
            with self.subTest():
                pod = f"{self.cluster_name}-{instance}"
                self.wait_pod(pod, "Running")

                podspec = kutil.get_po(self.ns, pod)

                self.assertEqual(3, len(podspec["spec"]["containers"]))
                metrics_container_spec = [c for c in podspec["spec"]["containers"] if c["name"] == "metrics"][0]
                metrics_container_volume_mounts = [mount["name"] for mount in metrics_container_spec["volumeMounts"]]
                print(metrics_container_volume_mounts)
                self.assertIn("metrics-web-config", metrics_container_volume_mounts)

                self.assertIn("metrics-web-config",
                            map(lambda volume: volume["name"],
                                podspec["spec"]["volumes"]))

                metrics_volume = [v for v in podspec["spec"]["volumes"] if v["name"] == "metrics-web-config"][0]
                self.assertEqual(metrics_volume["configMap"]["name"], cm_web_config_name)

                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s:
                    user = s.query_sql(
                        "SELECT User, Host, plugin"
                        " FROM mysql.user"
                        " WHERE User='mysqlmetrics'").fetch_all()
                    self.assertListEqual([("mysqlmetrics", "localhost", "auth_socket")], user)


                with kutil.PortForward(self.ns, pod, "metrics") as port:
                    resp = requests.get(f'http://127.0.0.1:{port}/metrics')
                    self.assertEqual(resp.status_code, 401)

                    resp = requests.get(f'http://127.0.0.1:{port}/metrics',
                                        auth=("alice", "alice"))
                    self.assertEqual(resp.status_code, 200)

    def test_06_enable_config(self):
        self._test_enable_config("cm-web-config")

    def test_08_disable(self, web_config_is_none: bool = False):
        patch = {
            "spec": {
                "metrics": {
                    "enable": False
                }
            }
        }
        if web_config_is_none:
            patch["spec"]["metrics"]["webConfig"] = None

        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=20)
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        for instance in reversed(range(0, self.cluster_size)):
            pod = f"{self.cluster_name}-{instance}"
            self.wait_pod(pod, "Running")

        for instance in range(0, self.cluster_size):
            pod = f"{self.cluster_name}-{instance}"
            with self.subTest(pod):
                podspec = kutil.get_po(self.ns, pod)
                print(podspec["spec"]["containers"])
                self.assertEqual(2, len(podspec["spec"]["containers"]))
                container_names = [c["name"] for c in podspec["spec"]["containers"]]
                self.assertNotIn("metrics", container_names)

                volumes_names = [v["name"] for v in podspec["spec"]["volumes"]]
                print(volumes_names)
                self.assertNotIn("metrics-web-config", volumes_names)

                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s:
                    user = s.query_sql(
                        "SELECT User, Host, plugin, authentication_string"
                        " FROM mysql.user"
                        " WHERE User='mysqlmetrics'").fetch_all()
                    self.assertListEqual([], user)

        svc = kutil.get_svc(self.ns, f"{self.cluster_name}-instances")
        print(svc["spec"]["ports"])
        self.assertNotIn("metrics", (p["name"] for p in svc["spec"]["ports"]))


    def test_10_enable(self, la_index = 9, web_config_was_none: bool = False):
        patch = {
            "spec": {
                "metrics": {
                    "enable": True
                },
                "podLabels": {
                    f"server-label{la_index}": f"myc-server-label{la_index}-value"
                },
                "podAnnotations": {
                    f"server.myc.example.com/ann{la_index}": f"server-ann{la_index}-value"
                }
            }
        }

        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=20)
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()

        for instance in reversed(range(0, self.cluster_size)):
            pod = f"{self.cluster_name}-{instance}"
            self.wait_pod(pod, "Running")

        for instance in range(0, self.cluster_size):
            pod = f"{self.cluster_name}-{instance}"
            with self.subTest(pod):
                podspec = kutil.get_po(self.ns, pod)
                print(podspec["spec"]["containers"])
                self.assertEqual(3, len(podspec["spec"]["containers"]))
                container_names = [c["name"] for c in podspec["spec"]["containers"]]
                self.assertIn("metrics", container_names)

                volumes_names = [v["name"] for v in podspec["spec"]["volumes"]]
                print(volumes_names)
                if web_config_was_none:
                    self.assertNotIn("metrics-web-config", volumes_names)
                else:
                    self.assertIn("metrics-web-config", volumes_names)

                self.assertEqual(podspec['metadata']['labels'][f'server-label{la_index}'], f'myc-server-label{la_index}-value')
                self.assertEqual(podspec['metadata']['annotations'][f'server.myc.example.com/ann{la_index}'], f'server-ann{la_index}-value')

                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila") as s:
                    user = s.query_sql(
                        "SELECT User, Host, plugin"
                        " FROM mysql.user"
                        " WHERE User='mysqlmetrics'").fetch_all()
                    self.assertListEqual([("mysqlmetrics", "localhost", "auth_socket")], user)

        #self._test_enable_config("configmap-web-config")

        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            with self.subTest(pod):
                pod = kutil.get_po(self.ns, pod_name)
                self.assertEqual(pod['metadata']['labels']['server-label9'], 'myc-server-label9-value')
                self.assertEqual(pod['metadata']['annotations']['server.myc.example.com/ann9'], 'server-ann9-value')

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)


    def test_12_disable_again(self):
        self.test_08_disable(True)

    def __test_14_enable_again(self):
        self.test_10_enable(3, "cm-web-config3")

    def __test_16_disable_again(self):
        self.test_08_disable(False)

    def __test_18_metrics_user_will_be_created_on_clone(self):
        """Make sure a clone will reset metrics user credentials
        The server we are cloning from got no 'mysqlmetrics' user, thus
        after clone there will be no user and it ahs to be reset"""

        kutil.create_user_secrets(self.ns, f"{self.cluster_secret_name}-donorpwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}-copycluster
spec:
  instances: 1
  router:
    instances: 0
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  initDB:
    clone:
      donorUrl: root@{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306
      secretKeyRef:
        name: {self.cluster_secret_name}-donorpwds
  metrics:
      enable: true
      image: {g_ts_cfg.get_image(Config.Image.METRICS)}
  podSpec:
    terminationGracePeriodSeconds: 10
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(f"{self.cluster_name}-copycluster-0", "Running")

        self.wait_ic(f"{self.cluster_name}-copycluster", "ONLINE", 1, timeout=300)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-copycluster-0", "root", "sakila") as s:
            user = s.query_sql(
                "SELECT User, Host, plugin"
                " FROM mysql.user"
                " WHERE User='mysqlmetrics'").fetch_all()
            self.assertListEqual([("mysqlmetrics", "localhost", "auth_socket")], user)


    def test_99_shutdown(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_ic(self.ns, f"{self.cluster_name}-copycluster")
        self.wait_pods_gone(f"{self.cluster_name}-copycluster-*")
        self.wait_routers_gone(f"{self.cluster_name}-copycluster-router-*")
        self.wait_ic_gone(f"{self.cluster_name}-copycluster")
        kutil.delete_cm(self.ns, "cm-web-config")

        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_secret(self.ns, f"{self.cluster_secret_name}-donorpwds")

        kutil.delete_pvc(self.ns, None)
