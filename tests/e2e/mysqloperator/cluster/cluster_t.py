# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils.auxutil import isotime
from utils import tutil
from utils import kutil
from utils import mutil
from setup import defaults
import logging
import json
from . import check_apiobjects
from . import check_group
from . import check_adminapi
from . import check_routing
from utils.tutil import g_full_log
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS
import os
import unittest

# TODO
# setup with ssl
# always check that the data (and GTIDs) are still there in all members after recovery
# create 2 clusters in the same namespace (should reject?)
# multinode test where 1 of the nodes get drained, make sure data matches everywhere
# ensure that crashed/stopped members don't get router traffic

g_target_old_version = defaults.MIN_SUPPORTED_MYSQL_VERSION


def check_sidecar_health(test, ns, pod):
    logs = kutil.logs(ns, [pod, "sidecar"])
    # check that the sidecar is running and waiting for events
    test.assertIn("Waiting for Operator requests...", logs)


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
            test.assertTrue(pod["status"]["containerStatuses"][0]["image"].endswith(
                version), pod["metadata"]["name"]+"="+pod["status"]["containerStatuses"][0]["image"])

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

    return all_pods


def cross_sync_gtids(ns, pods, user, password):
    sessions = []

    for pod in pods:
        sessions.append(mutil.MySQLPodSession(ns, pod, user, password))

    s0 = sessions[0]

    for s in sessions[1:]:
        gs = s.session.query_sql("select @@gtid_executed").fetch_one()[0]
        assert s0.session.query_sql(
            "select WAIT_FOR_EXECUTED_GTID_SET(%s, 0)", (gs,)).fetch_one()[0] == 0

    for s in sessions[1:]:
        gs = s0.session.query_sql("select @@gtid_executed").fetch_one()[0]
        assert s.session.query_sql(
            "select WAIT_FOR_EXECUTED_GTID_SET(%s, 1)", (gs,)).fetch_one()[0] == 0

    for s in sessions:
        s.close()


# Test 1 member cluster with all default configs
class Cluster1Defaults(tutil.OperatorTest):
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
  instances: 1
  secretName: mypwds
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE")

        check_all(self, self.ns, "mycluster",
                  instances=1, routers=0, primary=0)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg="Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def test_1_check_accounts(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            accts = set([row[0] for row in s.query_sql(
                "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])
            self.assertSetEqual(accts, set(["root@%",
                                            "localroot@localhost", "mysqladmin@%", "mysqlbackup@%", "mysqlrouter@%",
                                            "mysqlhealthchecker@localhost", "mysql_innodb_cluster_1000@%"] + DEFAULT_MYSQL_ACCOUNTS))

    def test_1_bad_changes(self):
        return  # TODO
        # this should trigger an error and no changes
        # changes after this should continue working normally
        kutil.patch_ic(self.ns, "mycluster", {
                       "spec": {"instances": 22}}, type="merge")

        # check that the error appears in describe ic output

        # check that nothing changed
        check_all(self, self.ns, "mycluster",
                  instances=1, routers=0, primary=0)

    def test_1_grow_2(self):
        kutil.patch_ic(self.ns, "mycluster", {
                       "spec": {"instances": 2}}, type="merge")

        self.wait_pod("mycluster-1", "Running")

        self.wait_ic("mycluster", "ONLINE", 2)

        self.logger.info(kutil.ls_ic(self.ns))

        check_all(self, self.ns, "mycluster", instances=2, primary=0)

    def test_2_addrouters(self):
        kutil.patch_ic(self.ns, "mycluster", {
                       "spec": {"router": {"instances": 3}}}, type="merge")

        def routers_ready():
            pods = kutil.ls_po(self.ns)
            return 3 == len([pod for pod in pods if pod["NAME"].startswith("mycluster-router-")])

        self.wait(routers_ready, timeout=30)

        check_all(self, self.ns, "mycluster",
                  instances=2, routers=3, primary=0)

        # TODO add traffic, check routing

    def test_3_grow_3(self):
        kutil.patch_ic(self.ns, "mycluster", {
                       "spec": {"instances": 3}}, type="merge")

        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", 3)

        self.logger.info(kutil.ls_ic(self.ns))

        check_all(self, self.ns, "mycluster", instances=3, primary=0)

    def test_5_shrink1(self):
        kutil.patch_ic(self.ns, "mycluster", {
                       "spec": {"instances": 1}}, type="merge")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")

        self.wait_ic("mycluster", "ONLINE", 1)

        self.logger.info(kutil.ls_ic(self.ns))

        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    def test_6_recover_crash_1(self):
        """
        Force a mysqld process crash.
        The only thing expected to happen is that mysql restarts and the
        cluster is resumed.
        """

        pod = kutil.get_po(self.ns, "mycluster-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        apply_time = isotime()

        # kill mysqld (pid 1)
        kutil.kill(self.ns, ("mycluster-0", "mysql"), 11, 1)

        # wait for operator to notice it gone
        self.wait_ic("mycluster", ["OFFLINE", "OFFLINE_UNCERTAIN"])

        # wait for operator to restore it
        self.wait_ic("mycluster", "ONLINE", 1)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster")

        # ensure persisted config didn't change after recovery
        config = json.loads(kutil.cat(self.ns, ("mycluster-0", "mysql"),
                                      "/var/lib/mysql/mysqld-auto.cnf"))
        self.assertEqual("OFF", config["mysql_server"]["mysql_server_static_options"]
                         ["group_replication_start_on_boot"]["Value"])

        pod = kutil.get_po(self.ns, "mycluster-0")
        check_apiobjects.check_pod_container(
            self, pod, "mysql", mysql_cont["restartCount"]+1, True)
        check_apiobjects.check_pod_container(
            self, pod, "sidecar", sidecar_cont["restartCount"], True)

        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    def test_6_recover_sidecar_crash_1(self):
        """
        Force a sidecar process crash.
        Nothing is expected to happen other than sidecar restarting and
        going back to ready state, since the sidecar is idle.
        # TODO should add a test for this when the sidecar is doing something
        # like doing a backup
        """

        # killing the sidecar isn't working somehow
        return

        pod = kutil.get_po(self.ns, "mycluster-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        config = json.loads(kutil.cat(self.ns, ("mycluster-0", "mysql"),
                                      "/var/lib/mysql/mysqld-auto.cnf"))
        self.assertEqual("OFF", config["mysql_server"]["mysql_server_static_options"]
                         ["group_replication_start_on_boot"]["Value"])

        # kill sidecar (pid 1)
        kutil.kill(self.ns, ["mycluster-0", "sidecar"], 11, 1)

        def ready():
            pod = kutil.get_po(self.ns, "mycluster-0")
            print(check_apiobjects.get_pod_container(
                pod, "sidecar")["restartCount"])
            return check_apiobjects.get_pod_container(pod, "sidecar")["restartCount"] == sidecar_cont["restartCount"]+1

        self.wait(ready)

        pod = kutil.get_po(self.ns, "mycluster-0")
        check_apiobjects.check_pod_container(
            self, pod, "mysql", mysql_cont["restartCount"], True)
        check_apiobjects.check_pod_container(
            self, pod, "sidecar", sidecar_cont["restartCount"]+1, True)

        # ensure persisted config didn't change after recovery (regression test)
        config = json.loads(kutil.cat(self.ns, ("mycluster-0", "mysql"),
                                      "/var/lib/mysql/mysqld-auto.cnf"))
        self.assertEqual("OFF", config["mysql_server"]["mysql_server_static_options"]
                         ["group_replication_start_on_boot"]["Value"])

        # check that all containers are OK
        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    def test_6_recover_restart_1(self):
        pod = kutil.get_po(self.ns, "mycluster-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        apply_time = isotime()

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            s.exec_sql("restart")

        # wait for operator to notice it gone
        self.wait_ic("mycluster", ["OFFLINE", "OFFLINE_UNCERTAIN"])

        # wait/ensure pod restarted
        pod = kutil.get_po(self.ns, "mycluster-0")
        self.assertEqual(check_apiobjects.get_pod_container(pod, "mysql")[
                         "restartCount"], mysql_cont["restartCount"]+1)

        # ensure sidecar didn't restart
        self.assertEqual(check_apiobjects.get_pod_container(pod, "sidecar")[
                         "restartCount"], sidecar_cont["restartCount"])

        # wait for operator to restore it
        self.wait_ic("mycluster", "ONLINE", 1)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster")

        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    def test_6_recover_shutdown_1(self):
        pod = kutil.get_po(self.ns, "mycluster-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        apply_time = isotime()

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            s.exec_sql("shutdown")

        # wait for operator to notice it gone
        self.wait_ic("mycluster", ["OFFLINE", "OFFLINE_UNCERTAIN"])

        # wait/ensure pod restarted
        pod = kutil.get_po(self.ns, "mycluster-0")
        self.assertEqual(check_apiobjects.get_pod_container(pod, "mysql")[
                         "restartCount"], mysql_cont["restartCount"]+1)

        # ensure sidecar didn't restart
        self.assertEqual(check_apiobjects.get_pod_container(pod, "sidecar")[
                         "restartCount"], sidecar_cont["restartCount"])

        # wait for operator to restore it
        self.wait_ic("mycluster", "ONLINE", 1)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster")

        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    # TODO also test deleting the only pod + pvc - check that it detects
    # the instance changed through the server_uuid change
    # TODO also test that deleting the only pod + pvc is detected as a
    # complete data wipe/replacement

    def test_6_recover_delete_1(self):
        kutil.delete_po(self.ns, "mycluster-0", timeout=200)

        apply_time = isotime()

        self.wait_ic("mycluster", "OFFLINE", 0)

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE", 1)

        pod1 = kutil.get_po(self.ns, "mycluster-0")

        # the pod was deleted, so restarts resets to 0
        check_apiobjects.check_pod_container(self, pod1, "mysql", 0, True)
        check_apiobjects.check_pod_container(self, pod1, "sidecar", 0, True)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster")

        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    def test_6_recover_stop_1(self):
        return
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0:
            s0.exec_sql("stop group_replication")

        # wait for operator to notice it OFFLINE
        self.wait_ic("mycluster", "OFFLINE")

        # check status of the downed pod
        # TODO

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE")

        check_all(self, self.ns, "mycluster", instances=1, primary=0)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")


# Test 3 member cluster with default configs
class Cluster3Defaults(tutil.OperatorTest):
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
        kutil.create_user_secrets(
            self.ns, "mypwds", root_pass="sakila", root_host="%")

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
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, "mycluster", 0)

        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE", 1)
        # now that at least the seed is ONLINE, the router should be deployed
        check_routing.check_pods(self, self.ns, "mycluster", 2)

        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", 3)

        check_all(self, self.ns, "mycluster",
                  instances=3, routers=2, primary=0)

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
        self.assertIn(":"+defaults.DEFAULT_VERSION_TAG, image, "initmysql")
        self.assertIn(defaults.MYSQL_SERVER_IMAGE+":", image, "initmysql")

        image = container_spec(
            pod["spec"]["initContainers"], "initconf")["image"]
        self.assertIn(":"+defaults.DEFAULT_OPERATOR_VERSION_TAG, image, "initconf")
        self.assertIn(defaults.MYSQL_OPERATOR_IMAGE+":", image, "initconf")

        image = container_spec(pod["spec"]["containers"], "mysql")["image"]
        self.assertIn(":"+defaults.DEFAULT_VERSION_TAG, image, "mysql")
        self.assertIn(defaults.MYSQL_SERVER_IMAGE+":", image, "mysql")

        image = container_spec(pod["spec"]["containers"], "sidecar")["image"]
        self.assertIn(":"+defaults.DEFAULT_OPERATOR_VERSION_TAG, image, "sidecar")
        self.assertIn(defaults.MYSQL_OPERATOR_IMAGE+":", image, "sidecar")

        # check router version and edition
        p = kutil.ls_po(self.ns, pattern="mycluster-router-.*")[0]
        pod = kutil.get_po(self.ns, p["NAME"])
        image = container_spec(pod["spec"]["containers"], "router")["image"]
        self.assertIn(":"+defaults.DEFAULT_VERSION_TAG, image, "router")
        self.assertIn(defaults.MYSQL_ROUTER_IMAGE + ":", image, "router")

    def test_1_check_accounts(self):
        expected_accounts = set(["root@%",
                                 "localroot@localhost", "mysqladmin@%", "mysqlbackup@%", "mysqlrouter@%",
                                 "mysqlhealthchecker@localhost", "mysql_innodb_cluster_1000@%",
                                 "mysql_innodb_cluster_1001@%", "mysql_innodb_cluster_1002@%"] + DEFAULT_MYSQL_ACCOUNTS)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            accts = set([row[0] for row in s.query_sql(
                "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])
            self.assertSetEqual(accts, expected_accounts)

        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s:
            accts = set([row[0] for row in s.query_sql(
                "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])
            self.assertSetEqual(accts, expected_accounts)

        with mutil.MySQLPodSession(self.ns, "mycluster-2", "root", "sakila") as s:
            accts = set([row[0] for row in s.query_sql(
                "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])
            self.assertSetEqual(accts, expected_accounts)

    @unittest.skipIf(not os.getenv("OPERATOR_TEST_JS_ENABLED"), "js disabled")
    def test_1_routing(self):
        """
        Check routing from a standalone pod in a different namespace
        """
        # create a pod to connect from (as an app)

        yaml = f"""
apiVersion: v1
kind: Pod
metadata:
  name: testpod
spec:
  containers:
    - name: shell
      image: "{defaults.DEFAULT_IMAGE_REPOSITORY}/mysql-shell:{defaults.DEFAULT_OPERATOR_VERSION_TAG}"
      command: ["mysqlsh", "--js", "-e", "os.sleep(600)"]
"""
        kutil.create_ns("appns")

        kutil.apply("appns", yaml)
        self.wait_pod("testpod", "Running", ns="appns")

        shell = mutil.MySQLInteractivePodSession(
            "appns", "testpod", user="root", password="sakila", host="mycluster.testns.svc.cluster.local:6446")
        # check classic session to R/W port
        # shell.execute(
        #     f"\\connect mysql://root:sakila@mycluster.testns.svc.cluster.local:6446")
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertEqual(
            result['r'], "mycluster-0.mycluster-instances.testns.svc.cluster.local:3306")

        r = shell.execute(
            f"\\connect mysql://root:sakila@mycluster.testns.svc.cluster.local:6446")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertEqual(
            result['r'], "mycluster-0.mycluster-instances.testns.svc.cluster.local:3306")

        # check classic session to R/O port
        r = shell.execute(
            f"\\connect mysql://root:sakila@mycluster.testns.svc.cluster.local:6447")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertIn(
            result['r'], ["mycluster-1.mycluster-instances.testns.svc.cluster.local:3306",
                          "mycluster-2.mycluster-instances.testns.svc.cluster.local:3306"])

        r = shell.execute(
            f"\\connect mysql://root:sakila@mycluster.testns.svc.cluster.local:6447")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertIn(
            result['r'], ["mycluster-1.mycluster-instances.testns.svc.cluster.local:3306",
                          "mycluster-2.mycluster-instances.testns.svc.cluster.local:3306"])

        # check X session to R/W port
        r = shell.execute(
            f"\\connect mysqlx://root:sakila@mycluster.testns.svc.cluster.local:6448")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertEqual(
            result['r'], "mycluster-0.mycluster-instances.testns.svc.cluster.local:3306")
        print(shell.execute("\\status"))

        r = shell.execute(
            f"\\connect mysqlx://root:sakila@mycluster.testns.svc.cluster.local:6448")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertEqual(
            result['r'], "mycluster-0.mycluster-instances.testns.svc.cluster.local:3306")

        # check X session to R/O port
        r = shell.execute(
            f"\\connect mysqlx://root:sakila@mycluster.testns.svc.cluster.local:6449")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertIn(
            result['r'], ["mycluster-1.mycluster-instances.testns.svc.cluster.local:3306",
                          "mycluster-2.mycluster-instances.testns.svc.cluster.local:3306"])

        r = shell.execute(
            f"\\connect mysqlx://root:sakila@mycluster.testns.svc.cluster.local:6449")
        print(r)
        result = shell.query_dict(
            "select concat(@@report_host, ':', @@port) as r;")[0]
        self.assertIn(
            result['r'], ["mycluster-1.mycluster-instances.testns.svc.cluster.local:3306",
                          "mycluster-2.mycluster-instances.testns.svc.cluster.local:3306"])

        kutil.delete_po("appns", "testpod")
        kutil.delete_ns("appns")

    def test_4_recover_crash_1_of_3(self):
        # kill mysqld (pid 1)
        kutil.kill(self.ns, ("mycluster-0", "mysql"), 11, 1)

        apply_time = isotime()

        # wait for operator to notice it gone
        self.wait_ic("mycluster", ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], 2)

        # check status of crashed pod
        # TODO

        # wait for operator to restore it
        self.wait_ic("mycluster", "ONLINE", 3)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to ONLINE_PARTIAL. 2 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to ONLINE. 3 member\(s\) ONLINE")

        check_all(self, self.ns, "mycluster", instances=3, primary=None)

    def test_4_recover_crash_2_of_3(self):
        # TODO add a loadchecker

        apply_time = isotime()

        # kill mysqld (pid 1)
        kutil.kill(self.ns, ("mycluster-0", "mysql"), 11, 1)
        kutil.kill(self.ns, ("mycluster-1", "mysql"), 11, 1)

        # wait for operator to notice them gone
        self.wait_ic("mycluster", "NO_QUORUM")

        # check status of crashed pods
        # TODO

        # wait for operator to restore it
        self.wait_ic("mycluster", "ONLINE", 3)

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to NO_QUORUM. 0 member\(s\) ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="RestoreQuorum", msg="Restoring quorum of cluster")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to ONLINE_PARTIAL. 2 member\(s\) ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Rejoin", msg="Rejoining mycluster-0 to cluster")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to ONLINE. 3 member\(s\) ONLINE")

        check_all(self, self.ns, "mycluster", instances=3, primary=2)

    def test_4_recover_crash_3_of_3(self):
        # kill mysqld (pid 1)
        kutil.kill(self.ns, ("mycluster-0", "mysql"), 11, 1)
        kutil.kill(self.ns, ("mycluster-1", "mysql"), 11, 1)
        kutil.kill(self.ns, ("mycluster-2", "mysql"), 11, 1)

        # wait for operator to notice them gone
        self.wait_ic("mycluster", "OFFLINE", 0)

        self.wait_ic("mycluster", ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], 1)

        self.wait_ic("mycluster", ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], 2)
        # wait for operator to restore it
        self.wait_ic("mycluster", "ONLINE", 3)

        check_all(self, self.ns, "mycluster", instances=3, primary=0)

    def test_4_recover_delete_1_of_3(self):
        # delete the PRIMARY
        kutil.delete_po(self.ns, "mycluster-0")

        self.wait_ic("mycluster", ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], 2)

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE", 3)

        pod0 = kutil.get_po(self.ns, "mycluster-0")

        # the pod was deleted, so restarts resets to 0
        self.assertEqual(pod0["status"]["containerStatuses"]
                         [0]["restartCount"], 0)

        check_all(self, self.ns, "mycluster", instances=3, primary=None)

        kutil.exec(self.ns, ("mycluster-0", "sidecar"),
                   ["mysqlsh", "root:sakila@localhost", "--",
                    "cluster", "set-primary-instance",
                    "mycluster-0.mycluster-instances.testns.svc.cluster.local:3306"])

        cross_sync_gtids(
            self.ns, ["mycluster-0", "mycluster-1", "mycluster-2"],
            "root", "sakila")

        all_pods = check_all(self, self.ns, "mycluster",
                             instances=3, primary=0)

        check_group.check_data(self, all_pods, primary=0)

    def test_4_recover_delete_2_of_3(self):
        p0ts = kutil.get_po(
            self.ns, "mycluster-0")["metadata"]["creationTimestamp"]
        p1ts = kutil.get_po(
            self.ns, "mycluster-1")["metadata"]["creationTimestamp"]

        apply_time = isotime()

        kutil.delete_po(self.ns, "mycluster-0")

        # extra timeout because the deletion of the 2nd pod will be blocked by
        # the busy handlers from the 1st deletion
        kutil.delete_po(self.ns, "mycluster-1", timeout=200)

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE", 3)

        # the pods were deleted, which means they would cleanly shutdown and
        # removed from the cluster
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Join", msg="Joining mycluster-0 to cluster")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange",
            msg=r"Cluster status changed to ONLINE. 2 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="Join", msg="Joining mycluster-1 to cluster")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="StatusChange",
            msg=r"Cluster status changed to ONLINE. 3 member\(s\) ONLINE")

        pod0 = kutil.get_po(self.ns, "mycluster-0")
        pod1 = kutil.get_po(self.ns, "mycluster-1")

        # make sure that the pods were actually deleted and recreated
        self.assertNotEqual(p0ts, pod0["metadata"]["creationTimestamp"])
        self.assertNotEqual(p1ts, pod1["metadata"]["creationTimestamp"])

        # the pod was deleted, so restarts resets to 0
        self.assertEqual(pod0["status"]["containerStatuses"]
                         [0]["restartCount"], 0)
        self.assertEqual(pod1["status"]["containerStatuses"]
                         [0]["restartCount"], 0)

        cross_sync_gtids(
            self.ns, ["mycluster-2", "mycluster-0", "mycluster-1"],
            "root", "sakila")
        cross_sync_gtids(
            self.ns, ["mycluster-1", "mycluster-2", "mycluster-0"],
            "root", "sakila")
        cross_sync_gtids(
            self.ns, ["mycluster-0", "mycluster-2", "mycluster-1"],
            "root", "sakila")

        all_pods = check_all(self, self.ns, "mycluster",
                             instances=3, primary=2)
        check_group.check_data(self, all_pods)

        kutil.exec(self.ns, ("mycluster-0", "sidecar"), ["mysqlsh", "root:sakila@localhost", "--", "cluster",
                                                         "set-primary-instance", "mycluster-0.mycluster-instances.testns.svc.cluster.local:3306"])

    def test_4_recover_delete_and_wipe_1_of_3(self):
        # delete the pv and pvc first, which will block because until the pod
        # is deleted

        # delete a secondary
        kutil.delete_po(self.ns, "mycluster-1")

        self.wait_ic("mycluster", ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], 2)

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE", 3)

        pod1 = kutil.get_po(self.ns, "mycluster-1")

        # the pod was deleted, so restarts resets to 0
        self.assertEqual(pod1["status"]["containerStatuses"]
                         [0]["restartCount"], 0)

        all_pods = check_all(self, self.ns, "mycluster",
                             instances=3, primary=0)

        check_group.check_data(self, all_pods, primary=0)

    def test_4_recover_stop_1_of_3(self):
        """
        Manually stop GR in 1 instance out of 3.

        TODO decide what to do, leave alone or restore?
        """
        return
        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s0:
            s0.exec_sql("stop group_replication")

        # TODO ensure router traffic is not ending up there

        # wait for operator to notice it OFFLINE
        self.wait_ic("mycluster", "ONLINE_PARTIAL", 2)

        # restart GR and wait until everything is back to normal
        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s0:
            s0.exec_sql("start group_replication")

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE", 3)

        # TODO ensure router traffic is resumed

        check_all(self, self.ns, "mycluster", instances=3, primary=0)

    def test_4_recover_stop_2_of_3(self):
        return
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, "mycluster-2", "root", "sakila") as s2:
            s0.exec_sql("stop group_replication")
            s2.exec_sql("stop group_replication")

        # wait for operator to notice it ONLINE_PARTIAL
        self.wait_ic("mycluster", "ONLINE_PARTIAL", 1)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE")

        check_all(self, self.ns, "mycluster", instances=3, primary=1)

    def test_4_recover_stop_3_of_3(self):
        return
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s1,\
                mutil.MySQLPodSession(self.ns, "mycluster-2", "root", "sakila") as s2:
            s0.exec_sql("stop group_replication")
            s1.exec_sql("stop group_replication")
            s2.exec_sql("stop group_replication")

        # wait for operator to notice it OFFLINE
        self.wait_ic("mycluster", "OFFLINE", 0)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE")

        check_all(self, self.ns, "mycluster", instances=3, primary=0)

    def test_4_recover_restart_1_of_3(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0:
            s0.exec_sql("restart")

        # wait for operator to notice it OFFLINE
        self.wait_ic("mycluster", "ONLINE_PARTIAL")

        # check status of the restarted pod
        # TODO

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE")

        check_all(self, self.ns, "mycluster", instances=3, primary=None)

    def test_4_recover_restart_2_of_3(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, "mycluster-2", "root", "sakila") as s2:
            s0.exec_sql("restart")
            s2.exec_sql("restart")

        # wait for operator to notice it ONLINE_PARTIAL
        self.wait_ic("mycluster", "ONLINE_PARTIAL", 1)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE")

        check_all(self, self.ns, "mycluster", instances=3, primary=1)

    def test_4_recover_restart_3_of_3(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s1,\
                mutil.MySQLPodSession(self.ns, "mycluster-2", "root", "sakila") as s2:
            s0.exec_sql("restart")
            s1.exec_sql("restart")
            s2.exec_sql("restart")

        # wait for operator to notice it OFFLINE
        self.wait_ic("mycluster", "OFFLINE", 0)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic("mycluster", "ONLINE")

        all_pods = check_all(self, self.ns, "mycluster",
                             instances=3, primary=0)
        check_group.check_data(self, all_pods)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")


class ClusterRaces(tutil.OperatorTest):
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

    def test_1_create_and_delete(self):
        """
        Create and delete a cluster immediately, before it becomes ONLINE.
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
  instances: 1
  secretName: mypwds
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING"])

        # deleting a cluster right after it's created and before it's ONLINE
        # was causing kopf to enter a loop and not finish deleting before
        # kopf 0.28.2 (github/nolar/kopf issue #601)
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_ic_gone("mycluster")
        self.wait_pod_gone("mycluster-0")


class Cluster3Defaults2Nodes(tutil.OperatorTest):
    pass


class TwoClustersOneNamespace(tutil.OperatorTest):
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

    def test_0_create_1(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_pass="sakila", root_host="%")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2alpha1
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
    instances: 1
  secretName: mypwds
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, "mycluster", 0)

        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE", 1)

        check_all(self, self.ns, "mycluster",
                  instances=1, routers=1, primary=0)

    def test_0_create_2(self):
        kutil.create_user_secrets(
            self.ns, "mypwds2", root_pass="sakilax", root_host="%")
        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2alpha1
kind: InnoDBCluster
metadata:
  name: mycluster2
spec:
  instances: 1
  router:
    instances: 2
  secretName: mypwds2
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, "mycluster2", 0)

        self.wait_pod("mycluster2-0", "Running")

        self.wait_ic("mycluster2", "ONLINE", 1)

        check_all(self, self.ns, "mycluster2", instances=1, routers=2,
                  primary=0, password="sakilax", shared_ns=True)

    def test_1_destroy_1(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        # mycluster2 should still be fine
        check_all(self, self.ns, "mycluster2", instances=1,
                  routers=2, password="sakilax", primary=0)

    def test_1_destroy_2(self):
        kutil.delete_ic(self.ns, "mycluster2")

        self.wait_pod_gone("mycluster2-0")
        self.wait_ic_gone("mycluster2")


class ClusterCustomConf(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    cluster_name = "myvalid-cluster-name-28-char"

    @classmethod
    def setUpClass(cls):
        assert len(cls.cluster_name) == 28

        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_create(self):
        """
        Checks:
        - cluster name can be 28chars long
        - root user name and host can be customized
        - base server id can be changed
        - version can be customized
        - mycnf can be specified
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="admin", root_host="%", root_pass="secret")

        # create cluster with mostly default configs, but a specific server version
        yaml = f"""
apiVersion: mysql.oracle.com/v2alpha1
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: 2
  secretName: mypwds
  version: "{g_target_old_version}"
  baseServerId: 3210
  mycnf: |
    [mysqld]
    admin_port=3333
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(self.cluster_name+"-0", "Running")
        self.wait_pod(self.cluster_name+"-1", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", 2)

        check_all(self, self.ns, self.cluster_name, instances=2, routers=0,
                  primary=0, user="admin", password="secret")

        with mutil.MySQLPodSession(self.ns, self.cluster_name+"-0", user="admin", password="secret") as session:
            aport, sid, ver = session.query_sql(
                "select @@admin_port, @@server_id, @@version").fetch_one()
            self.assertEqual(aport, 3333)
            self.assertEqual(sid, 3210)
            self.assertEqual(ver, g_target_old_version)

            users = list(session.query_sql(
                "select user,host from mysql.user where user='root'").fetch_all())
            self.assertEqual(users, [])

        with mutil.MySQLPodSession(self.ns, self.cluster_name+"-1", user="admin", password="secret") as session:
            aport, sid, ver = session.query_sql(
                "select @@admin_port, @@server_id, @@version").fetch_one()
            self.assertEqual(aport, 3333)
            self.assertEqual(sid, 3211)
            self.assertEqual(ver, g_target_old_version)

            users = list(session.query_sql(
                "select user,host from mysql.user where user='root'").fetch_all())
            self.assertEqual(users, [])

        pod = kutil.get_po(self.ns, self.cluster_name+"-0")
        cont = check_apiobjects.check_pod_container(
            self, pod, "mysql", None, True)
        self.assertEqual(
            cont["image"], f"mysql/mysql-server:{g_target_old_version}")
        cont = check_apiobjects.check_pod_container(
            self, pod, "sidecar", None, True)
        self.assertEqual(
            cont["image"],
            f"mysql/mysql-shell:{defaults.DEFAULT_OPERATOR_VERSION_TAG}")

        # check version of router images
        pods = kutil.ls_po(self.ns, pattern=self.cluster_name+"-.*-router")
        for p in pods:
            pod = kutil.get_po(self.ns, p["NAME"])
            cont = check_apiobjects.check_pod_container(
                self, pod, None, None, True)
            self.assertEqual(
                cont["image"],
                f"mysql/mysql-router:{defaults.DEFAULT_VERSION_TAG}", p["NAME"])

    # TODO config change in spec (and decide what to do)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pod_gone(self.cluster_name+"-1")
        self.wait_pod_gone(self.cluster_name+"-0")
        self.wait_ic_gone(self.cluster_name)


class ClusterCustomImageConf(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-1")

        super().tearDownClass()

    def test_0_create(self):
        """
        Checks:
        - imagePullSecrets is propagated
        - version is propagated
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="admin", root_host="%", root_pass="secret")

        # create cluster with mostly default configs, but a specific server version
        yaml = f"""
apiVersion: mysql.oracle.com/v2alpha1
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
    instances: 1
  version: "{g_target_old_version}"
  secretName: mypwds
  imagePullSecrets:
    - name: pullsecrets
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE", 1)

        check_all(self, self.ns, "mycluster", instances=1, routers=1,
                  primary=0, user="admin", password="secret")

        # check server pod
        pod = kutil.get_po(self.ns, "mycluster-0")

        self.assertEqual(pod["spec"]["imagePullSecrets"],
                         [{"name": "pullsecrets"}])

        cont = check_apiobjects.check_pod_container(
            self, pod, "mysql", None, True)
        self.assertEqual(
            cont["image"], f"mysql/mysql-server:{g_target_old_version}")
        cont = check_apiobjects.check_pod_container(
            self, pod, "sidecar", None, True)
        self.assertEqual(
            cont["image"],
            f"mysql/mysql-shell:{defaults.DEFAULT_OPERATOR_VERSION_TAG}")

        # check router pod
        pods = kutil.ls_po(self.ns, pattern="mycluster-.*-router")
        for p in pods:
            pod = kutil.get_po(self.ns, p["NAME"])

            self.assertEqual(pod["spec"]["imagePullSecrets"], [
                             {"name": "pullsecrets"}])

            cont = check_apiobjects.check_pod_container(
                self, pod, None, None, True)
            self.assertEqual(
                cont["image"],
                f"mysql/mysql-router:{defaults.DEFAULT_VERSION_TAG}", p["NAME"])

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


# ClusterErrors():
# TODO test error creating cluster, adding instance, removing, rejoining
# restoring cluster etc

# class UnreachablePods(tutil.OperatorTest):
#

# class ClusterChangeRaces(tutil.OperatorTest):
#    pass

# class ClusterSSLCertificates(tutil.OperatorTest):
#    pass
# try to join a member with bad certificates to the group and ensure it's rejected
