# Copyright (c) 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
from time import sleep
from utils import tutil
from utils import kutil
from utils import mutil
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS

class ClusterFQDNTest(tutil.OperatorTest):
    """Test FQDN behavior

    This test configures a FQDN without cluster domain (no .clsuter.local)
    and checks whether that is respected in various pplaces
    """

    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    readreplica_cluster_name = "trr"
    readreplica_cluster_size = 1
    _cluster_size = 3
    _routers_count = 1

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

    def test_00_create(self):
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
  readReplicas:
  - name: {self.readreplica_cluster_name}
    instances: {self.readreplica_cluster_size}
    baseServerId: 500
  # This template will remain a template, this is no f-string!
  serviceFqdnTemplate: '{{service}}.{{namespace}}'
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        for instance in range(0, self.readreplica_cluster_size):
            self.wait_pod(f"{self.cluster_name}-{self.readreplica_cluster_name}-{instance}", "Running", ready=True)

    def test_01_check_report_host(self):
        for instance in range(0, self.cluster_size):
            pod = f"{self.cluster_name}-{instance}"
            with self.subTest(pod_name=pod):
                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila", 3306) as s:
                    res = s.query_sql("SELECT @@report_host")
                    self.assertEqual(res.fetch_one()[0], f"{pod}.{self.cluster_name}-instances.{self.ns}")

    def test_02_check_router_config(self):
        deployment = kutil.get_deploy(self.ns, f"{self.cluster_name}-router")
        envs = deployment["spec"]["template"]["spec"]["containers"][0]["env"]

        found = False
        for env in envs:
            if env["name"] == "MYSQL_HOST":
                found = True
                self.assertEqual(env["value"], f"{self.cluster_name}-instances.{self.ns}")

        self.assertTrue(found)

    def test_01_check_read_replica(self):
        for instance in range(0, self.readreplica_cluster_size):
            trr_name = f"{self.cluster_name}-{self.readreplica_cluster_name}"
            pod = f"{trr_name}-{instance}"
            with self.subTest(pod):
                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila", 3306) as s:
                    res = s.query_sql("SELECT @@report_host")
                    self.assertEqual(res.fetch_one()[0], f"{pod}.{trr_name}-instances.{self.ns}")


    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_pods_gone(f"{self.cluster_name}-{self.readreplica_cluster_name}-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
