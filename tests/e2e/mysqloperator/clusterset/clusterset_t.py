# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from time import sleep
from asyncio import subprocess
from utils.auxutil import isotime
from utils import tutil
from utils import kutil
from utils import mutil
from setup import defaults
import logging
import yaml
from ..cluster import check_apiobjects
from ..cluster import check_group
from ..cluster import check_adminapi
from ..cluster import check_routing
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS
import unittest

# TODO
# setup with ssl
# always check that the data (and GTIDs) are still there in all members after recovery
# create 2 clusters in the same namespace (should reject?)
# multinode test where 1 of the nodes get drained, make sure data matches everywhere
# ensure that crashed/stopped members don't get router traffic

def check_sidecar_health(test, ns, pod):
    logs = kutil.logs(ns, [pod, "sidecar"])
    # check that the sidecar is running and waiting for events
    test.assertIn("Starting Operator request handler...", logs)


def check_all(test, ns, name, instances, routers=None, primary=None, count_sessions=False, user="root", password="sakila", shared_ns=False, version=None):
    icobj, all_pods = check_apiobjects.get_cluster_object(test, ns, name)

    check_apiobjects.check_cluster_spec(test, icobj, instances, routers)
    check_apiobjects.check_online_cluster(test, icobj, allow_others=shared_ns)

    info = check_group.check_group(
        test, icobj, all_pods, user=user, password=password)
    if primary is None:
        # detect primary from cluster
        primary = info["primary"]

    for i, pod in enumerate(all_pods):
        test.assertEqual(pod["metadata"]["name"], f"{name}-{i}")
        check_apiobjects.check_online_pod(
            test, icobj, pod, "PRIMARY" if i == primary else "SECONDARY")

        num_sessions = None
        if count_sessions:
            num_sessions = 0
            if i == primary:
                # PRIMARY has the GR observer session
                num_sessions += 1
            else:
                num_sessions = 0

        if version:
            for pod_status in pod["status"]["containerStatuses"]:
                if pod_status["name"] == "mysql":
                    test.assertTrue(pod_status["image"].endswith(version),
                                    pod["metadata"]["name"]+"="+pod_status["image"])


        check_group.check_instance(test, icobj, all_pods, pod, i == primary,
                                   num_sessions=num_sessions, user=user, password=password)

        # check_mysqld_health(test, ns, pod["metadata"]["name"])
        check_sidecar_health(test, ns, pod["metadata"]["name"])

    router_pods = kutil.ls_po(ns, pattern=f"{name}-router-.*")
    if routers is not None:
        test.assertEqual(len(router_pods), routers)
        for router in router_pods:
            test.assertEqual(router["STATUS"], "Running", router["NAME"])

            router_pod = kutil.get_po(ns, router["NAME"])
            check_apiobjects.check_router_pod(test, router_pod)

    return (all_pods, router_pods)


class ClusterSetBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    primary_cluster_name = "mycluster1"
    replica_1_cluster_name = "mycluster20"
    replica_2_cluster_name = "mycluster21"
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    secret_name = "mypwds"
    instances = 1
    router_instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.primary_cluster_name}-{instance}")
        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.replica_1_cluster_name}-{instance}")
        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.replica_2_cluster_name}-{instance}")


    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"{cls.replica_2_cluster_name}-{instance}")
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"{cls.replica_1_cluster_name}-{instance}")
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"{cls.primary_cluster_name}-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition_primary(cls, cluster_name) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
spec:
  instances: {cls.instances}
  router:
    instances: {cls.router_instances}
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  baseServerId: 1000
"""

    @classmethod
    def cluster_definition_replica(cls, cluster_name, baseServerId, primary_name, primary_ns) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
spec:
  instances: {cls.instances}
  router:
    instances: {cls.router_instances}
  secretName: {cls.secret_name}
  edition: community
  tlsUseSelfSigned: true
  baseServerId: {baseServerId}
  initDB:
    clusterSet:
      targetUrl: {primary_name}-0.{primary_name}-instances.{primary_ns}.svc.cluster.local
      secretKeyRef:
        name: {cls.secret_name}
"""


    def _create_cluster(self, cluster_name, cluster_manifest):

        apply_time = isotime()
        kutil.apply(self.ns, cluster_manifest)

        self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.instances):
            self.wait_pod(f"{cluster_name}-{instance}", "Running")

        if self.router_instances:
            self.wait_routers(f"{cluster_name}-router-*", self.router_instances, timeout=self.instances*120)
        self.wait_ic(cluster_name, "ONLINE")

        self.assertGotClusterEvent(
            cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")


    def _00_create(self):
        kutil.create_user_secrets(self.ns, self.secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        self._create_cluster(self.primary_cluster_name, self.cluster_definition_primary(self.primary_cluster_name))

        self._create_cluster(self.replica_1_cluster_name, self.cluster_definition_replica(self.replica_1_cluster_name, 2000, self.primary_cluster_name, self.ns))

        self._create_cluster(self.replica_2_cluster_name, self.cluster_definition_replica(self.replica_2_cluster_name, 2100, self.primary_cluster_name, self.ns))


    def _02_test_inserts(self):
          pod_name = f"{self.primary_cluster_name}-0"
          with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s1:
              s1.query_sql('CREATE SCHEMA clusterset')
              s1.query_sql('CREATE TABLE clusterset.t1(a INT PRIMARY KEY, b VARCHAR(100))')
              s1.query_sql('INSERT INTO clusterset.t1 VALUES(42, "fourtytwo")')
              primary_data = s1.query_sql('SELECT @@report_host, a, b FROM clusterset.t1').fetch_one()
              s1.query_sql('COMMIT')

          sleep(5)

          pod_name = f"{self.replica_1_cluster_name}-0"
          with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s2_1:
              replica1_data = s2_1.query_sql('SELECT @@report_host, a, b FROM clusterset.t1').fetch_one()
              print(primary_data)
              print(replica1_data)
              self.assertEqual(replica1_data[1], primary_data[1])
              self.assertEqual(replica1_data[2], primary_data[2])

          pod_name = f"{self.replica_2_cluster_name}-0"
          with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s2_2:
              replica2_data = s2_2.query_sql('SELECT @@report_host, a, b FROM clusterset.t1').fetch_one()
              print(primary_data)
              print(replica2_data)
              self.assertEqual(replica2_data[1], primary_data[1])
              self.assertEqual(replica2_data[2], primary_data[2])


    def _04_switchover(self):
        switchover_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLClusterSetFailover
metadata:
  name: incident-switchover-to-replica1
spec:
  clusterName: {self.replica_1_cluster_name}
  force: false
"""
        kutil.apply(self.ns, switchover_manifest)
        sleep(30)

        switchover_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLClusterSetFailover
metadata:
  name: incident-switchover-to-replica2
spec:
  clusterName: {self.replica_2_cluster_name}
  force: false
"""
        kutil.apply(self.ns, switchover_manifest)
        sleep(30)

        switchover_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLClusterSetFailover
metadata:
  name: incident-switchover-back-to-primary
spec:
  clusterName: {self.primary_cluster_name}
  force: false
"""
        kutil.apply(self.ns, switchover_manifest)
        sleep(10)


    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.replica_1_cluster_name)
        self.wait_pods_gone(f"{self.replica_1_cluster_name}-*")
        if self.router_instances:
            self.wait_routers_gone(f"{self.replica_1_cluster_name}-router-*")
        self.wait_ic_gone(self.replica_1_cluster_name)

        kutil.delete_ic(self.ns, self.replica_2_cluster_name)
        self.wait_pods_gone(f"{self.replica_2_cluster_name}-*")
        if self.router_instances:
            self.wait_routers_gone(f"{self.replica_2_cluster_name}-router-*")
        self.wait_ic_gone(self.replica_2_cluster_name)

        kutil.delete_ic(self.ns, self.primary_cluster_name)
        self.wait_pods_gone(f"{self.primary_cluster_name}-*")
        if self.router_instances:
            self.wait_routers_gone(f"{self.primary_cluster_name}-router-*")
        self.wait_ic_gone(self.primary_cluster_name)

        kutil.delete_secret(self.ns, self.secret_name)

        kutil.delete_pvc(self.ns, None)

    def runit(self):
        self._00_create()
        self._02_test_inserts()
        self._04_switchover()
        self._99_destroy()


class ClusterSetWithOneInstance(ClusterSetBase):
    instances = 1
    def testit(self):
        self.runit()

class ClusterSetWithThreeInstances(ClusterSetBase):
    instances = 3
    def testit(self):
        self.runit()
