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

    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    _cluster_size = 1
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        cls.primary_cluster_name = f"{cls.cluster_name}-1"
        cls.replica_1_cluster_name = f"{cls.cluster_name}-20"
        cls.replica_2_cluster_name = f"{cls.cluster_name}-21"

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.primary_cluster_name}-{instance}")
        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.replica_1_cluster_name}-{instance}")
        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.replica_2_cluster_name}-{instance}")


    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.replica_2_cluster_name}-{instance}")
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.replica_1_cluster_name}-{instance}")
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.primary_cluster_name}-{instance}")

        super().tearDownClass()

    def cluster_definition_primary(self, cluster_name: str, cluster_size: int, routers_count: int) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
spec:
  instances: {cluster_size}
  router:
    instances: {routers_count}
  secretName: {self.cluster_secret_name}
  edition: enterprise
  tlsUseSelfSigned: true
  baseServerId: 1000
"""

    #@classmethod
    def cluster_definition_replica(self, cluster_name: str, cluster_size: int, routers_count: int, base_server_id, primary_name, primary_ns) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cluster_name}
spec:
  instances: {cluster_size}
  router:
    instances: {routers_count}
  secretName: {self.cluster_secret_name}
  edition: enterprise
  tlsUseSelfSigned: true
  baseServerId: {base_server_id}
  initDB:
    clusterSet:
      targetUrl: {primary_name}-0.{primary_name}-instances.{primary_ns}.svc.cluster.local
      secretKeyRef:
        name: {self.cluster_secret_name}
"""

    def _create_cluster(self, cluster_name, cluster_manifest):

        apply_time = isotime()
        kutil.apply(self.ns, cluster_manifest)

        self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)
        self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")


    def _00_create(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        self._create_cluster(self.primary_cluster_name, self.cluster_definition_primary(self.primary_cluster_name, self.cluster_size, self.routers_count))

        self._create_cluster(self.replica_1_cluster_name, self.cluster_definition_replica(self.replica_1_cluster_name, self.cluster_size, self.routers_count, 2000, self.primary_cluster_name, self.ns))

        self._create_cluster(self.replica_2_cluster_name, self.cluster_definition_replica(self.replica_2_cluster_name, self.cluster_size, self.routers_count, 2100, self.primary_cluster_name, self.ns))

    def _clusterset_cluster_names(self):
        return (
            self.primary_cluster_name,
            self.replica_1_cluster_name,
            self.replica_2_cluster_name,
        )

    def _get_clusterset_role(self, cluster_name):
        return kutil.execp(
            self.ns,
            [f"{cluster_name}-0", "sidecar"],
            [
                "mysqlsh",
                f"{self.root_user}:{self.root_pass}@localhost",
                "--js",
                "-e",
                "var status = dba.getCluster().status({extended:1}); print(status.clusterRole || 'PRIMARY')",
                "--quiet-start=2",
            ],
        ).decode("utf8").strip()

    def _wait_clusterset_role(self, cluster_name, expected_type, timeout=300):
        def cluster_role_matches():
            try:
                return self._get_clusterset_role(cluster_name) == expected_type
            except Exception:
                return False

        self.wait(cluster_role_matches, timeout=timeout, delay=5)

    def _wait_switchover_job_succeeded(self, job_name, timeout=300):
        def job_succeeded():
            job = kutil.get(self.ns, "job", job_name, check=False)
            if not job:
                return False

            status = job.get("status", {})
            if status.get("failed", 0):
                raise AssertionError(f"Switchover Job {self.ns}/{job_name} failed: {status}")

            return status.get("succeeded", 0) == 1

        self.wait(job_succeeded, timeout=timeout, delay=5)

    def _assert_one_instance_clusterset_roles(self, primary_cluster_name):
        for cluster_name in self._clusterset_cluster_names():
            expected_type = "PRIMARY" if cluster_name == primary_cluster_name else "REPLICA"
            self.wait_ic(cluster_name, "ONLINE", num_online=1)
            self._wait_clusterset_role(cluster_name, expected_type)
            self.wait_routers(f"{cluster_name}-router-.*", num_online=1, timeout=180)

    def _write_and_verify_row(self, current_primary_cluster_name, value):
        row_text = f"value-{value}"

        with mutil.MySQLPodSession(self.ns, f"{current_primary_cluster_name}-0", self.root_user, self.root_pass) as session:
            session.exec_sql(
                "INSERT INTO clusterset.t1 (a, b) VALUES (%s, %s)",
                (value, row_text),
            )
            session.exec_sql("COMMIT")

        def row_visible_on_all_clusters():
            for cluster_name in self._clusterset_cluster_names():
                with mutil.MySQLPodSession(self.ns, f"{cluster_name}-0", self.root_user, self.root_pass) as session:
                    row = session.query_sql(
                        "SELECT a, b FROM clusterset.t1 WHERE a = %s",
                        (value,),
                    ).fetch_one()

                if row is None:
                    return False

                self.assertEqual(row, (value, row_text))

            return True

        self.wait(row_visible_on_all_clusters, timeout=180, delay=5)

    def _cleanup_switchover_resources(self, job_name, timeout=180):
        kutil.delete(self.ns, "mysqlclustersetfailover", job_name, timeout=timeout)
        kutil.delete(self.ns, "job", job_name, timeout=timeout)

        def switchover_resources_gone():
            failover = kutil.get(self.ns, "mysqlclustersetfailover", job_name, check=False)
            job = kutil.get(self.ns, "job", job_name, check=False)
            pods = kutil.ls_po(self.ns, pattern=f"{job_name}-.*")
            return failover is None and job is None and not pods

        self.wait(switchover_resources_gone, timeout=timeout, delay=5)

    def _apply_switchover(self, job_name, target_cluster_name, expected_primary_cluster_name):
        switchover_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLClusterSetFailover
metadata:
  name: {job_name}
spec:
  clusterName: {target_cluster_name}
  force: false
"""
        apply_time = isotime()
        kutil.apply(self.ns, switchover_manifest)
        self.wait_got_cluster_event(
            target_cluster_name,
            after=apply_time,
            type="Normal",
            reason="FailOverObjectCreated",
            msg=f"Switching over to {self.ns}/{target_cluster_name} from .+",
        )
        self._wait_switchover_job_succeeded(job_name)
        self._assert_one_instance_clusterset_roles(expected_primary_cluster_name)
        self._write_and_verify_row(expected_primary_cluster_name, self._next_write_value)
        self._next_write_value += 1
        self._cleanup_switchover_resources(job_name)


    def _02_test_inserts(self):
          pod_name = f"{self.primary_cluster_name}-0"
          with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s1:
              s1.query_sql('CREATE SCHEMA clusterset')
              s1.query_sql('CREATE TABLE clusterset.t1(a INT PRIMARY KEY, b VARCHAR(100))')
              s1.query_sql('INSERT INTO clusterset.t1 VALUES(42, "fourtytwo")')
              primary_data = s1.query_sql('SELECT @@report_host, a, b FROM clusterset.t1').fetch_one()
              s1.query_sql('COMMIT')

          sleep(10)

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
        if self.routers_count:
            self.wait_routers_gone(f"{self.replica_1_cluster_name}-router-*")
        self.wait_ic_gone(self.replica_1_cluster_name)

        kutil.delete_ic(self.ns, self.replica_2_cluster_name)
        self.wait_pods_gone(f"{self.replica_2_cluster_name}-*")
        if self.routers_count:
            self.wait_routers_gone(f"{self.replica_2_cluster_name}-router-*")
        self.wait_ic_gone(self.replica_2_cluster_name)

        kutil.delete_ic(self.ns, self.primary_cluster_name)
        self.wait_pods_gone(f"{self.primary_cluster_name}-*")
        if self.routers_count:
            self.wait_routers_gone(f"{self.primary_cluster_name}-router-*")
        self.wait_ic_gone(self.primary_cluster_name)

        for switchover_name in (
            "incident-switchover-to-replica1",
            "incident-switchover-to-replica2",
            "incident-switchover-back-to-primary",
        ):
            self._cleanup_switchover_resources(switchover_name)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

        kutil.delete_pvc(self.ns, None)

    def runit(self):
        self._00_create()
        self._02_test_inserts()
        self._04_switchover()
        self._99_destroy()

    def runit_one_instance(self):
        self._00_create()
        self._02_test_inserts()
        self._next_write_value = 43
        self._assert_one_instance_clusterset_roles(self.primary_cluster_name)
        self._apply_switchover(
            "incident-switchover-to-replica1",
            self.replica_1_cluster_name,
            self.replica_1_cluster_name,
        )
        self._apply_switchover(
            "incident-switchover-to-replica2",
            self.replica_2_cluster_name,
            self.replica_2_cluster_name,
        )
        self._apply_switchover(
            "incident-switchover-back-to-primary",
            self.primary_cluster_name,
            self.primary_cluster_name,
        )
        self._99_destroy()


@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class ClusterSetWithOneInstance(ClusterSetBase):
    _cluster_size = 1
    _routers_count = 1
    def testit(self):
        self.runit_one_instance()


@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class ClusterSetWithThreeInstances(ClusterSetBase):
    _cluster_size = 3
    _routers_count = 1
    def testit(self):
        self.runit()
