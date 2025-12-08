# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from utils.auxutil import isotime
from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS
from .cluster_t import check_all

# TODO test edition change and upgrades


@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class ClusterEnterprise(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 3
    _routers_count = 2

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

    def test_0_create(self):
        """
        Create cluster, check posted events.
        """
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
  edition: enterprise
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def test_1_check_accounts(self):
        for instance in range(0, self.cluster_size):
            pod_name = f"{self.cluster_name}-{instance}"
            with self.subTest(pod_name):
                with mutil.MySQLPodSession(self.ns, pod_name, "root", "sakila") as s:
                    accts = set([row[0] for row in s.query_sql(
                        "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])

                    expected_accounts = set(["root@%",
                                        "localroot@localhost",
                                        "mysqladmin-[\w\d]{10}@%",
                                        "mysqlbackup@%",
                                        "mysqlrouter-[\w\d]{10}@%",
                                        "mysql_innodb_cs_[\w\d]+@%", # comes from mysqlsh
                                        "mysqlhealthchecker@localhost",
                                        "mysql_innodb_cluster_1000@%",
                                        "mysql_innodb_cluster_1001@%",
                                        "mysql_innodb_cluster_1002@%"] + DEFAULT_MYSQL_ACCOUNTS)
                    self.assertSetEqualRegex(expected_accounts, accts, "expected accounts", "existing accounts")

    def test_1_check_version(self):
        def container_spec(l, name):
            for cont in l:
                if cont["name"] == name:
                    return cont
            return None

        for instance in range(0, self.cluster_size):
            pod_name = f"{self.cluster_name}-{instance}"
            with self.subTest(pod_name):
                # ensure containers have the right version and edition
                pod = kutil.get_po(self.ns, pod_name)
                image = container_spec(
                    pod["spec"]["initContainers"], "initmysql")["image"]
                self.assertIn(":"+g_ts_cfg.version_tag, image, "initmysql")
                self.assertIn(g_ts_cfg.server_ee_image_name+":", image, "initmysql")

                image = container_spec(
                    pod["spec"]["initContainers"], "initconf")["image"]
                self.assertIn(":"+g_ts_cfg.operator_version_tag, image, "initconf")
                self.assertIn(g_ts_cfg.operator_ee_image_name+":", image, "initconf")

                image = container_spec(pod["spec"]["containers"], "mysql")["image"]
                self.assertIn(":"+g_ts_cfg.version_tag, image, "mysql")
                self.assertIn(g_ts_cfg.server_ee_image_name+":", image, "mysql")

                image = container_spec(pod["spec"]["containers"], "sidecar")["image"]
                self.assertIn(":"+g_ts_cfg.operator_version_tag, image, "sidecar")
                self.assertIn(g_ts_cfg.operator_ee_image_name+":", image, "sidecar")

        # check routers version and edition
        router_pods = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        for router_pod in router_pods:
            with self.subTest(router_pod["NAME"]):
                pod = kutil.get_po(self.ns, router_pod["NAME"])
                image = container_spec(pod["spec"]["containers"], "router")["image"]
                self.assertIn(":"+g_ts_cfg.version_tag, image, "router")
                self.assertIn(g_ts_cfg.router_ee_image_name + ":", image, "router")

    def test_1_check_enterprise_plugin(self):
        for instance in range(0, self.cluster_size):
            pod_name = f"{self.cluster_name}-{instance}"
            with self.subTest(pod_name):
                with mutil.MySQLPodSession(self.ns, pod_name, "root", "sakila") as session:
                    res = {
                        row[0]: row[1] for row in  session.query_sql(
                            "SELECT dl, COUNT(*) FROM mysql.func WHERE dl IN ('data_masking.so') GROUP BY dl UNION SELECT 'encryption', COUNT(*) FROM mysql.component WHERE component_urn = 'file://component_enterprise_encryption' ").fetch_all()
                    }
                    self.assertDictEqual(res, {
                        "data_masking.so": 14,
                        "encryption": 1
                    })

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
