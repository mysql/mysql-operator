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

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-0")
        g_full_log.stop_watch(cls.ns, "mycluster-1")

        super().tearDownClass()

    def test_00_create(self):
        """
        Create cluster, check posted events.
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
  instances: 2
  router:
    instances: 1
  readReplicas:
  - name: trr
    instances: 1
    baseServerId: 500
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  # This template will remain a template, this is no f-string!
  serviceFqdnTemplate: '{service}.{namespace}'
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.wait_pod("mycluster-trr-0", "Running")

    def test_01_check_report_host(self):
        for pod in ("mycluster-0", "mycluster-1"):
            with self.subTest(pod_name=pod):
                with mutil.MySQLPodSession(self.ns, pod, "root", "sakila",
                                           3306) as s:
                    res = s.query_sql("SELECT @@report_host")
                    self.assertEqual(res.fetch_one()[0],
                                     f"{pod}.mycluster-instances.{self.ns}")

    def test_02_check_router_config(self):
        deployment = kutil.get_deploy(self.ns, "mycluster-router")
        envs = deployment["spec"]["template"]["spec"]["containers"][0]["env"]

        found = False
        for env in envs:
            if env["name"] == "MYSQL_HOST":
                found = True
                self.assertEqual(env["value"], f"mycluster-instances.{self.ns}")

        self.assertTrue(found)

    def test_01_check_read_replica(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-trr-0", "root", "sakila",
                                    3306) as s:
            res = s.query_sql("SELECT @@report_host")
            self.assertEqual(res.fetch_one()[0],
                                f"mycluster-trr-0.mycluster-trr-instances.{self.ns}")


    def test_99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_pod_gone("mycluster-1")
        self.wait_ic_gone("mycluster")
