# Copyright (c) 2023, Oracle and/or its affiliates.
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

SQL_MY_ROLE = """
    SELECT MEMBER_ROLE
      FROM performance_schema.replication_group_members
     WHERE MEMBER_HOST = @@report_host
     """

class ClusterService(tutil.OperatorTest):
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
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  service:
    defaultPort: mysql-rw-split
    labels:
        x-mylabel: "l-value"
    annotations:
      mycluster.example.com/ann: "ann-value"
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")

        self.wait_ic("mycluster", "ONLINE")

    def test_01_check_rw_split(self):
        with mutil.MySQLPodSession(self.ns, "mycluster", "root", "sakila",
                                   3306, "service") as s:
            # ensure we are in autocommit mode to be predicatble
            s.exec_sql("set autocommit=1")
            res = s.query_sql(SQL_MY_ROLE)
            self.assertEqual(res.fetch_one()[0], "SECONDARY")
            s.exec_sql("begin")
            res = s.query_sql(SQL_MY_ROLE)
            self.assertEqual(res.fetch_one()[0], "PRIMARY")

    def test_02_check_annotation_and_label(self):
        service = kutil.get_svc(self.ns, "mycluster")

        self.assertEqual(
            service['metadata']['annotations']['mycluster.example.com/ann'],
            'ann-value')

        self.assertEqual(
            service['metadata']['labels']['x-mylabel'], 'l-value')

    def test_03_read_write(self):
        patch = {
            "spec": {
                "service": {
                    "defaultPort": "mysql-rw"
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        sleep(1)
        with mutil.MySQLPodSession(self.ns, "mycluster", "root", "sakila",
                                   3306, "service") as s:
            res = s.query_sql(SQL_MY_ROLE)
            self.assertEqual(res.fetch_one()[0], "PRIMARY")


    def test_04_read_only(self):
        patch = {
            "spec": {
                "service": {
                    "defaultPort": "mysql-ro"
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        sleep(1)
        with mutil.MySQLPodSession(self.ns, "mycluster", "root", "sakila",
                                   3306, "service") as s:
            res = s.query_sql(SQL_MY_ROLE)
            self.assertEqual(res.fetch_one()[0], "SECONDARY")

    def test_05_patch_annotation(self):
        patch = {
            "spec": {
                "service": {
                    "annotations": {
                        "mycluster.example.com/ann": "new-value"
                    }
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        sleep(1)
        service = kutil.get_svc(self.ns, "mycluster")

        self.assertEqual(
            service['metadata']['annotations']['mycluster.example.com/ann'],
            "new-value")

    def test_05_patch_label(self):
        patch = {
            "spec": {
                "service": {
                    "labels": {
                        "one-more": "new-value"
                    }
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        sleep(1)
        service = kutil.get_svc(self.ns, "mycluster")

        self.assertEqual(service['metadata']['labels']['one-more'], 'new-value')

    def test_06_service_type_load_balancer(self):
        patch = {
            "spec": {
                "service": {
                    "type": "LoadBalancer"
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        sleep(1)
        service = kutil.get_svc(self.ns, "mycluster")

        # we don't have any guarantee that we got an external IP and that we
        # can route there from anywhere, we can only verify we set the right
        # type
        self.assertEqual(service["spec"]["type"], "LoadBalancer")

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_pod_gone("mycluster-1")
        self.wait_ic_gone("mycluster")
