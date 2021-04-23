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

from mysqloperator.controller.utils import isotime
from utils import tutil
from utils import kutil
from utils import mutil
from mysqloperator.controller import config
import logging
from utils.tutil import g_full_log
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS
from .cluster_t import check_all

# TODO test edition change and upgrades


class ClusterEnterprise(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2alpha1
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 3
  router:
    instances: 2
  secretName: mypwds
  edition: enterprise
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING"])

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", num_online=3)

        check_all(self, self.ns, "mycluster",
                  instances=3, routers=2, primary=0)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def test_1_check_accounts(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            accts = set([row[0] for row in s.run_sql(
                "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])
            self.assertSetEqual(accts, set([
                "root@%", "localroot@localhost", "mysqladmin@%",
                "mysqlbackup@%", "mysqlrouter@%",
                "mysqlhealthchecker@localhost", "mysql_innodb_cluster_1000@%",
                "mysql_innodb_cluster_1001@%", "mysql_innodb_cluster_1002@%"]
                + DEFAULT_MYSQL_ACCOUNTS))

    def test_1_check_version(self):
        def container_spec(l, name):
            for cont in l:
                if cont["name"] == name:
                    return cont
            return None

        # ensure containers have the right version and edition
        pod = kutil.get_po(self.ns, "mycluster-0")
        image = container_spec(
            pod["spec"]["initContainers"], "initmysql")["image"]
        self.assertIn(":"+config.DEFAULT_VERSION_TAG, image, "initmysql")
        self.assertIn(config.MYSQL_SERVER_EE_IMAGE+":", image, "initmysql")

        image = container_spec(
            pod["spec"]["initContainers"], "initconf")["image"]
        self.assertIn(":"+config.DEFAULT_SHELL_VERSION_TAG, image, "initconf")
        self.assertIn(config.MYSQL_SHELL_EE_IMAGE+":", image, "initconf")

        image = container_spec(pod["spec"]["containers"], "mysql")["image"]
        self.assertIn(":"+config.DEFAULT_VERSION_TAG, image, "mysql")
        self.assertIn(config.MYSQL_SERVER_EE_IMAGE+":", image, "mysql")

        image = container_spec(pod["spec"]["containers"], "sidecar")["image"]
        self.assertIn(":"+config.DEFAULT_SHELL_VERSION_TAG, image, "sidecar")
        self.assertIn(config.MYSQL_SHELL_EE_IMAGE+":", image, "sidecar")

        # check router version and edition
        p = kutil.ls_po(self.ns, pattern="mycluster-router-.*")[0]
        pod = kutil.get_po(self.ns, p["NAME"])
        image = container_spec(pod["spec"]["containers"], "router")["image"]
        self.assertIn(":"+config.DEFAULT_VERSION_TAG, image, "router")
        self.assertIn(config.MYSQL_ROUTER_EE_IMAGE + ":", image, "router")

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")
