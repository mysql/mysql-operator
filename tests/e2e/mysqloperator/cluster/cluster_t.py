# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
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
import json
import re
from . import check_apiobjects
from . import check_group
from . import check_adminapi
from . import check_routing
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

    router_pods = [
        pod
        for pod in kutil.ls_po(ns, pattern=f"{name}-router-.*")
        if tutil._is_active_pod_row(pod)
    ]
    if routers is not None:
        test.assertEqual(
            len(router_pods),
            routers,
            f"router_pods={router_pods}",
        )
        for router in router_pods:
            test.assertEqual(router["STATUS"], "Running", router["NAME"])

            router_pod = kutil.get_po(ns, router["NAME"])
            check_apiobjects.check_router_pod(test, router_pod)

    return (all_pods, router_pods)


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


class Cluster1FinalizerRemoval(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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
  imagePullPolicy: Always
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=f"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

    def test_01_check_labels_and_annotations(self):
        # We need to restart the pod so we can after that delete the IC and check for a race
        # in cluster finalizer removal / hanging finalizer
        patch = {
                    "spec": {
                        "imagePullPolicy": "IfNotPresent"
                    }
                }
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=self.cluster_size*300, delay=50)
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


class Cluster1FixDatadir(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        cls.manifest_template = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {cls.cluster_name}
spec:
  instances: {cls.get_ts_var("cluster_size")}
  router:
    instances: {cls.get_ts_var("routers_count")}
  secretName: {cls.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 1
"""
        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def do_create(self, yaml):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=f"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

    def do_checks(self, container_expected: bool, fsgroup_change_policy_extected: str):
        print(f"Checking for {container_expected=} and {fsgroup_change_policy_extected=}")
        server_pods = kutil.ls_po(self.ns, pattern=f'{self.cluster_name}-\d')
        pod_names = [server['NAME'] for server in server_pods]
        for pod_name in pod_names:
            with self.subTest(pod_name):
                pod = kutil.get_po(self.ns, pod_name)
                print(pod['spec']['securityContext'])
                self.assertTrue('runAsGroup' in pod['spec']['securityContext'])
                self.assertEqual(pod['spec']['securityContext']['runAsGroup'], 27)
                self.assertTrue('runAsUser' in pod['spec']['securityContext'])
                self.assertEqual(pod['spec']['securityContext']['runAsUser'], 27)
                self.assertTrue('fsGroup' in pod['spec']['securityContext'])
                self.assertEqual(pod['spec']['securityContext']['fsGroup'], 27)
                self.assertTrue('runAsNonRoot' in pod['spec']['securityContext'])
                self.assertEqual(pod['spec']['securityContext']['runAsNonRoot'], True)

                init_containers = [initc['name'] for initc in pod['spec']['initContainers']]
                self.assertEqual('fixdatadir' in init_containers, container_expected)

                fsgroup_change_policy_found = 'fsGroupChangePolicy' in pod['spec']['securityContext']
                self.assertTrue(fsgroup_change_policy_found == bool(fsgroup_change_policy_extected))
                if fsgroup_change_policy_extected and fsgroup_change_policy_found:
                    self.assertEqual(pod['spec']['securityContext']['fsGroupChangePolicy'], fsgroup_change_policy_extected)

    def test_00(self):
        manifest = f"""{self.manifest_template}
  datadirPermissions:
    setRightsUsingInitContainer: false
    fsGroupChangePolicy: "Always"
"""
        self.do_create(manifest)

        self.do_checks(False, "Always")

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        # don't delete self.cluster_secret_name

    def test_02(self):
        manifest = f"""{self.manifest_template}
  datadirPermissions:
    setRightsUsingInitContainer: true
    fsGroupChangePolicy: "OnRootMismatch"
"""
        self.do_create(manifest)

        self.do_checks(True, "OnRootMismatch")

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        # don't delete self.cluster_secret_name

    def test_04(self):
        manifest = f"""{self.manifest_template}
  datadirPermissions:
    setRightsUsingInitContainer: true
"""
        self.do_create(manifest)

        self.do_checks(True, "")

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        # don't delete self.cluster_secret_name

    def test_06(self):
        """
        Testing defaults
        """
        manifest = self.manifest_template

        self.do_create(manifest)

        self.do_checks(True, "")

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        # don't delete self.cluster_secret_name

    def test_99(self):
        kutil.delete_secret(self.ns, self.cluster_secret_name)


class Cluster1ServiceAccountDefault(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        cls.sa_name = f"{cls.cluster_name}-sidecar-sa"

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
  # don't specify serviceAccountName and see if the default one will be created
  podSpec:
    terminationGracePeriodSeconds: 1
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=f"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

    def test_01_check_labels_and_annotations(self):
        sas = [sa["NAME"] for sa in kutil.ls_sa(self.ns)]
        self.assertTrue('default' in sas)
        self.assertTrue(self.sa_name in sas)
        cluster_sa = kutil.get_sa(self.ns, self.sa_name)
        self.assertTrue('imagePullSecrets' not in cluster_sa)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


class Cluster1ServiceAccountDefaultWithPullSecret(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    priv_registry_secret_name = "priv-reg-secret"
    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        cls.sa_name = f"{cls.cluster_name}-sidecar-sa"

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

        # {"auths":{"https://192.168.20.198:5000/v2/":{"username":"user","password":"pass","email":"user@example.com","auth":"dXNlcjpwYXNz"}}}  # user:pass
        yaml = f"""
apiVersion: v1
kind: Secret
type: kubernetes.io/dockerconfigjson
metadata:
  name: {self.priv_registry_secret_name}
data:
  .dockerconfigjson: eyJhdXRocyI6eyJodHRwczovLzE5Mi4xNjguMjAuMTk4OjUwMDAvdjIvIjp7InVzZXJuYW1lIjoidXNlciIsInBhc3N3b3JkIjoicGFzcyIsImVtYWlsIjoidXNlckBleGFtcGxlLmNvbSIsImF1dGgiOiJkWE5sY2pwd1lYTnoifX19
"""
        kutil.apply(self.ns, yaml)

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
  imagePullSecrets:
  - name : {self.priv_registry_secret_name}
  podSpec:
    terminationGracePeriodSeconds: 1
"""
        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg="Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def test_01_check_labels_and_annotations(self):
        sas = [sa["NAME"] for sa in kutil.ls_sa(self.ns)]
        self.assertTrue('default' in sas)
        self.assertTrue(self.sa_name in sas)
        cluster_sa = kutil.get_sa(self.ns, self.sa_name)
        self.assertTrue('imagePullSecrets' in cluster_sa)
        found = False
        for pull_secret in cluster_sa['imagePullSecrets']:
            if "name" in pull_secret and pull_secret["name"] == self.priv_registry_secret_name:
                found = True
        if not found:
            print(cluster_sa)

        self.assertTrue(found)
        print(cluster_sa)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_secret(self.ns, self.priv_registry_secret_name)


class Cluster1ServiceAccountNamed(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        cls.sa_name = f"{cls.cluster_name}-named-sa"

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
  serviceAccountName: {self.sa_name}
  podSpec:
    terminationGracePeriodSeconds: 1
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg="Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def test_01_check_labels_and_annotations(self):
        sas = [sa["NAME"] for sa in kutil.ls_sa(self.ns)]
        self.assertTrue('default' in sas)
        self.assertTrue(self.sa_name in sas)
        cluster_sa = kutil.get_sa(self.ns, self.sa_name)
        self.assertTrue('imagePullSecrets' not in cluster_sa)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


class Cluster1ServiceAccountNamedWithPullSecret(tutil.OperatorTest):
    priv_registry_secret_name = "priv-reg-secret"
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        cls.sa_name = f"{cls.cluster_name}-named-sa"

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

        # {"auths":{"https://192.168.20.198:5000/v2/":{"username":"user","password":"pass","email":"user@example.com","auth":"dXNlcjpwYXNz"}}}  # user:pass
        yaml = f"""
apiVersion: v1
kind: Secret
type: kubernetes.io/dockerconfigjson
metadata:
  name: {self.priv_registry_secret_name}
data:
  .dockerconfigjson: eyJhdXRocyI6eyJodHRwczovLzE5Mi4xNjguMjAuMTk4OjUwMDAvdjIvIjp7InVzZXJuYW1lIjoidXNlciIsInBhc3N3b3JkIjoicGFzcyIsImVtYWlsIjoidXNlckBleGFtcGxlLmNvbSIsImF1dGgiOiJkWE5sY2pwd1lYTnoifX19
"""
        kutil.apply(self.ns, yaml)

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
  serviceAccountName: {self.sa_name}
  imagePullSecrets:
  - name : {self.priv_registry_secret_name}
  podSpec:
    terminationGracePeriodSeconds: 1
"""
        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg="Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def test_01_check_labels_and_annotations(self):
        sas = [sa["NAME"] for sa in kutil.ls_sa(self.ns)]
        self.assertTrue('default' in sas)
        self.assertTrue(self.sa_name in sas)
        cluster_sa = kutil.get_sa(self.ns, self.sa_name)
        self.assertTrue('imagePullSecrets' in cluster_sa)
        found = False
        for pull_secret in cluster_sa['imagePullSecrets']:
            if "name" in pull_secret and pull_secret["name"] == self.priv_registry_secret_name:
                found = True
        if not found:
            print(cluster_sa)

        self.assertTrue(found)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_secret(self.ns, self.priv_registry_secret_name)


class Cluster1AnnotationsAndLabelsUpdate(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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
  podLabels:
    server-label1: "myc-server-label1-value"
    server-label2: "myc-server-label2-value"
  podAnnotations:
    server.myc.example.com/ann1: "server-ann1-value"
    server.myc.example.com/ann2: "server-ann2-value"
    server.myc.example.com/ann3: "server-ann3-value"
  podSpec:
    terminationGracePeriodSeconds: 1
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg="Cluster status changed to ONLINE. 1 member\(s\) ONLINE")


    def test_01_check_labels_and_annotations(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['server-label1'], 'myc-server-label1-value')
            self.assertEqual(pod['metadata']['labels']['server-label2'], 'myc-server-label2-value')
            self.assertEqual(pod['metadata']['annotations']['server.myc.example.com/ann1'], 'server-ann1-value')
            self.assertEqual(pod['metadata']['annotations']['server.myc.example.com/ann2'], 'server-ann2-value')
            self.assertEqual(pod['metadata']['annotations']['server.myc.example.com/ann3'], 'server-ann3-value')

    def test_03_patch_labels_and_annotations(self):
        patch = [
            {
                "op":"replace",
                "path":"/spec/podLabels",
                "value": {
                    "server-label1" : "myc-server-label11-value",
                    "server-label222": "myc-router-label222-value",
                }
            },
            {
                "op":"replace",
                "path":"/spec/podAnnotations",
                "value": {
                    "server.myc.example.com/ann1": "server-ann111-value",
                    "server.myc.example.com/ann2": "server-ann222-value",
                    "server.myc.example.com/ann333": "server-ann333-value",
                }
            },
        ]
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="json", data_as_type='json')
        waiter()
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            self.wait_pod(pod_name, "Running")

    def test_05_check_ic(self):
        labels = kutil.get_ic(self.ns, self.cluster_name)["spec"]["podLabels"]

        self.assertTrue('server-label1' in labels)
        self.assertTrue('server-label222' in labels)
        self.assertFalse('server-label2' in labels)
        self.assertEqual(labels['server-label1'], 'myc-server-label11-value')
        self.assertEqual(labels['server-label222'], 'myc-router-label222-value')

        annotations = kutil.get_ic(self.ns, self.cluster_name)["spec"]["podAnnotations"]
        self.assertTrue('server.myc.example.com/ann1' in annotations)
        self.assertTrue('server.myc.example.com/ann2' in annotations)
        self.assertTrue('server.myc.example.com/ann333' in annotations)
        self.assertFalse('server.myc.example.com/ann3' in annotations)
        self.assertEqual(annotations['server.myc.example.com/ann1'], 'server-ann111-value')
        self.assertEqual(annotations['server.myc.example.com/ann2'], 'server-ann222-value')
        self.assertEqual(annotations['server.myc.example.com/ann333'], 'server-ann333-value')

    def test_07_check_sts(self):
        labels = kutil.get_sts(self.ns, self.cluster_name)["spec"]["template"]["metadata"]["labels"]

        self.assertTrue('server-label1' in labels)
        self.assertTrue('server-label222' in labels)
        self.assertFalse('server-label2' in labels)
        self.assertEqual(labels['server-label1'], 'myc-server-label11-value')
        self.assertEqual(labels['server-label222'], 'myc-router-label222-value')

        annotations = kutil.get_sts(self.ns, self.cluster_name)["spec"]["template"]["metadata"]["annotations"]
        self.assertTrue('server.myc.example.com/ann1' in annotations)
        self.assertTrue('server.myc.example.com/ann2' in annotations)
        self.assertTrue('server.myc.example.com/ann333' in annotations)
        self.assertFalse('server.myc.example.com/ann3' in annotations)
        self.assertEqual(annotations['server.myc.example.com/ann1'], 'server-ann111-value')
        self.assertEqual(annotations['server.myc.example.com/ann2'], 'server-ann222-value')
        self.assertEqual(annotations['server.myc.example.com/ann333'], 'server-ann333-value')

    def test_09_check_pod(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            labels = pod['metadata']['labels']
            self.assertTrue('server-label1' in labels)
            self.assertTrue('server-label222' in labels)
            self.assertFalse('server-label2' in labels)
            self.assertEqual(labels['server-label1'], 'myc-server-label11-value')
            self.assertEqual(labels['server-label222'], 'myc-router-label222-value')

            annotations = pod['metadata']['annotations']
            self.assertTrue('server.myc.example.com/ann1' in annotations)
            self.assertTrue('server.myc.example.com/ann2' in annotations)
            self.assertTrue('server.myc.example.com/ann333' in annotations)
            self.assertFalse('server.myc.example.com/ann3' in annotations)
            self.assertEqual(annotations['server.myc.example.com/ann1'], 'server-ann111-value')
            self.assertEqual(annotations['server.myc.example.com/ann2'], 'server-ann222-value')
            self.assertEqual(annotations['server.myc.example.com/ann333'], 'server-ann333-value')

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


# Test 1 member cluster with all default configs
class Cluster1Defaults(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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

        self.set_ts_var("cluster_size", self.cluster_size)

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
    podLabels:
      router-label13: "router-label13-val"
      router-label42: "router-label42-val"
    podAnnotations:
      router.myc.example.com/ann13: "ann13-value"
      router.myc.example.com/ann42: "ann42-value"
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podLabels:
    myc-label1: "myc-label1-value"
    myc-label2: "myc-label2-value"
  podAnnotations:
    myc.example.com/ann1: "ann1-value"
    myc.example.com/ann2: "ann2-value"
"""

        apply_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        # TODO - this event not getting posted, check if normal
        #self.assertGotClusterEvent(
        #    self.cluster_name, after=apply_time, type="Normal",
        #    reason=r"StatusChange", msg="Cluster status changed to INITIALIZING. 0 member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=f"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

    def test_01_check_labels_and_annotations(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['myc-label1'], 'myc-label1-value')
            self.assertEqual(pod['metadata']['labels']['myc-label2'], 'myc-label2-value')
            self.assertEqual(pod['metadata']['annotations']['myc.example.com/ann1'], 'ann1-value')
            self.assertEqual(pod['metadata']['annotations']['myc.example.com/ann2'], 'ann2-value')

        router_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-router-.*")
        pod_names = [router["NAME"] for router in router_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['router-label13'], 'router-label13-val')
            self.assertEqual(pod['metadata']['labels']['router-label42'], 'router-label42-val')
            self.assertEqual(pod['metadata']['annotations']['router.myc.example.com/ann13'], 'ann13-value')
            self.assertEqual(pod['metadata']['annotations']['router.myc.example.com/ann42'], 'ann42-value')

    def test_03_check_accounts(self):
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            accts = set([row[0] for row in s.query_sql(
                "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])

            expected_accounts = set(["root@%",
                                     "localroot@localhost",
                                     "mysqladmin-[\w\d]{10}@%",
                                     "mysqlbackup@%",
                                     "mysqlrouter-[\w\d]{10}@%",
                                     "mysql_innodb_cs_[\w\d]+@%", # comes from mysqlsh
                                     "mysqlhealthchecker@localhost",
                                     "mysql_innodb_cluster_1000@%"] + DEFAULT_MYSQL_ACCOUNTS)
            self.assertSetEqualRegex(expected_accounts, accts, "expected accounts", "existing accounts")\

    @unittest.skip("TODO: This test needs to be fixed")
    def test_05_bad_changes(self):
        # this should trigger an error and no changes
        # changes after this should continue working normally
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": 22}}, type="merge")

        # check that the error appears in describe ic output

        # check that nothing changed
        check_all(self, self.ns, self.cluster_name,
                  instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_07_grow_2(self):
        self.cluster_size = 2
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.logger.info(kutil.ls_ic(self.ns))

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_08_check_labels_and_annotations(self):
        self.test_01_check_labels_and_annotations()

    def test_09_addrouters(self):
        self.routers_count = 3
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"router": {"instances": self.routers_count}}}, type="merge")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=120)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        # TODO add traffic, check routing

    def test_10_check_labels_and_annotations(self):
        self.test_01_check_labels_and_annotations()

    def test_11_check_security(self):
        """
        Ensure PodSecurityContext has required restrictions.
        """

        def check_pod(pod, uid, user, process):
            # kubectl exec runs as the mysql user
            out = kutil.execp(self.ns, pod, ["id"])
            self.assertEqual(f"uid={uid}({user}) gid={uid}({user}) groups={uid}({user})", out.strip().decode("utf-8"))

            # cmdline of process 1 is mysqld
            out = kutil.execp(self.ns, pod, ["cat", "/proc/1/cmdline"])
            self.assertEqual(process, out.split(b"\0")[0].decode("utf-8"))

            # /proc/1 is owned by (runs as) uid=mysql/27, gid=mysql/27
            out = kutil.execp(self.ns, pod,  ["stat", "/proc/1"])
            access = [line for line in out.split(b"\n") if line.startswith(b"Access")][0].strip().decode("utf-8")
            self.assertEqual(f"Access: (0555/dr-xr-xr-x)  Uid: ({uid:5}/{user:>8})   Gid: ({uid:5}/{user:>8})", access)

        def check_mysql_pod(pod, uid, user, process):
            check_pod(pod, uid, user, process)

            out = kutil.execp(self.ns, pod,  ["stat", "-c%n %U %a", "/var/lib/mysql"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql {user} 700", line)

        check_mysql_pod([f"{self.cluster_name}-0", "mysql"], 27, "mysql", "mysqld")

        for router_instance in range(0, self.routers_count):
            with self.subTest(router_instance):
                p = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-router-.*")[router_instance]["NAME"]
                check_pod(p, 999, "mysqlrouter", "mysqlrouter")

    def test_13_grow_3(self):
        self.cluster_size = 3
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.logger.info(kutil.ls_ic(self.ns))

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_14_check_labels_and_annotations(self):
        self.test_01_check_labels_and_annotations()

    def test_15_shrink1(self):
        old_cluster_size = self.cluster_size
        self.cluster_size = 1
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")

        for instance in reversed(range(self.cluster_size, old_cluster_size)):
            self.wait_pod_gone(f"{self.cluster_name}-{instance}")

        self.wait_ic(self.cluster_name, "ONLINE_PARTIAL", num_online=self.cluster_size)

        self.logger.info(kutil.ls_ic(self.ns))

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_17_recover_crash_1(self):
        """
        Force a mysqld process crash.
        The only thing expected to happen is that mysql restarts and the
        cluster is resumed.
        """

        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        apply_time = isotime()

        # kill mysqld (pid 1)
        kutil.kill(self.ns, (f"{self.cluster_name}-0", "mysql"), 11, 1)

        # wait for operator to notice it gone
        self.wait_ic(self.cluster_name, ["OFFLINE", "OFFLINE_UNCERTAIN"])

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster through pod 0")

        # ensure persisted config didn't change after recovery
        config = json.loads(kutil.cat(self.ns, (f"{self.cluster_name}-0", "mysql"),
                                      "/var/lib/mysql/mysqld-auto.cnf"))
        # mysqlsh < 8.0.27 was not handling start_on_boot correctly
        if g_ts_cfg.operator_shell_version_num >= 80027:
            if g_ts_cfg.server_version_num >= 80029:
                self.assertEqual("OFF", config["mysql_static_variables"]
                        ["group_replication_start_on_boot"]["Value"])
            else:
                self.assertEqual("OFF", config["mysql_server"]["mysql_server_static_options"]
                        ["group_replication_start_on_boot"]["Value"])

        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        check_apiobjects.check_pod_container(
            self, pod, "mysql", mysql_cont["restartCount"]+1, True)
        check_apiobjects.check_pod_container(
            self, pod, "sidecar", sidecar_cont["restartCount"], True)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    @unittest.skip("TODO: This test needs to be fixed")
    def test_19_recover_sidecar_crash_1(self):
        """
        Force a sidecar process crash.
        Nothing is expected to happen other than sidecar restarting and
        going back to ready state, since the sidecar is idle.
        # TODO should add a test for this when the sidecar is doing something
        # like doing a backup
        """

        # killing the sidecar isn't working somehow

        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        config = json.loads(kutil.cat(self.ns, (f"{self.cluster_name}-0", "mysql"),
                                      "/var/lib/mysql/mysqld-auto.cnf"))
        self.assertEqual("OFF", config["mysql_server"]["mysql_server_static_options"]
                         ["group_replication_start_on_boot"]["Value"])

        # kill sidecar (pid 1)
        kutil.kill(self.ns, [f"{self.cluster_name}-0", "sidecar"], 11, 1)

        def ready():
            pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
            self.logger.debug(check_apiobjects.get_pod_container(
                pod, "sidecar")["restartCount"])
            return check_apiobjects.get_pod_container(pod, "sidecar")["restartCount"] == sidecar_cont["restartCount"]+1

        self.wait(ready)

        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        check_apiobjects.check_pod_container(
            self, pod, "mysql", mysql_cont["restartCount"], True)
        check_apiobjects.check_pod_container(
            self, pod, "sidecar", sidecar_cont["restartCount"]+1, True)

        # ensure persisted config didn't change after recovery (regression test)
        config = json.loads(kutil.cat(self.ns, (f"{self.cluster_name}-0", "mysql"),
                                      "/var/lib/mysql/mysqld-auto.cnf"))
        self.assertEqual("OFF", config["mysql_server"]["mysql_server_static_options"]
                         ["group_replication_start_on_boot"]["Value"])

        # check that all containers are OK
        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_21_recover_restart_1(self):
        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        apply_time = isotime()

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql("RESTART")

        # wait for operator to notice it gone
        self.wait_ic(self.cluster_name, ["OFFLINE", "OFFLINE_UNCERTAIN"])

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        # wait/ensure pod restarted
        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        self.assertEqual(check_apiobjects.get_pod_container(pod, "mysql")[
                         "restartCount"], mysql_cont["restartCount"]+1)

        # ensure sidecar didn't restart
        self.assertEqual(check_apiobjects.get_pod_container(pod, "sidecar")[
                         "restartCount"], sidecar_cont["restartCount"])

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster through pod 0")

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_23_recover_shutdown_1(self):
        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        mysql_cont = check_apiobjects.get_pod_container(pod, "mysql")
        sidecar_cont = check_apiobjects.get_pod_container(pod, "sidecar")

        apply_time = isotime()

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql("SHUTDOWN")

        # wait for operator to notice it gone
        self.wait_ic(self.cluster_name, ["OFFLINE", "OFFLINE_UNCERTAIN"])

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=1)

        # wait/ensure pod restarted
        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        self.assertEqual(check_apiobjects.get_pod_container(pod, "mysql")[
                         "restartCount"], mysql_cont["restartCount"]+1)

        # ensure sidecar didn't restart
        self.assertEqual(check_apiobjects.get_pod_container(pod, "sidecar")[
                         "restartCount"], sidecar_cont["restartCount"])

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster through pod 0")

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    # TODO also test deleting the only pod + pvc - check that it detects
    # the instance changed through the server_uuid change
    # TODO also test that deleting the only pod + pvc is detected as a
    # complete data wipe/replacement

    def test_25_recover_delete_1(self):
        kutil.delete_po(self.ns, f"{self.cluster_name}-0", timeout=200)

        apply_time = isotime()

        self.wait_ic(self.cluster_name, "OFFLINE", num_online=0)

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        pod1 = kutil.get_po(self.ns, f"{self.cluster_name}-0")

        # the pod was deleted, so restarts resets to 0
        check_apiobjects.check_pod_container(self, pod1, "mysql", 0, True)
        check_apiobjects.check_pod_container(self, pod1, "sidecar", 0, True)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Rebooting", msg="Restoring OFFLINE cluster through pod 0")

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    @unittest.skip("TODO: This test needs to be fixed")
    def test_27_recover_stop_1(self):
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0:
            s0.exec_sql("stop group_replication")

        # wait for operator to notice it OFFLINE
        self.wait_ic(self.cluster_name, "OFFLINE", num_online=0)

        # check status of the downed pod
        # TODO

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


class Cluster1SidecarKopfScanningDisabled(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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

    def _get_sidecar_logs(self):
        return kutil.logs(
            self.ns,
            [f"{self.cluster_name}-0", "sidecar"],
            cmd_output_log=kutil.KubectlCmdOutputLogging.MUTE)

    def _wait_sidecar_started(self):
        def started():
            logs = self._get_sidecar_logs()
            return logs if "Starting Operator request handler..." in logs else None

        return self.wait(
            started,
            timeout=120,
            delay=2,
            timeout_diagnostics=lambda: kutil.store_pod_diagnostics(
                self.ns, f"{self.cluster_name}-0"))

    def test_00_create_and_check_sidecar_logs(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

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
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])
        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self._wait_sidecar_started()
        sleep(5)
        sidecar_logs = self._get_sidecar_logs()

        self.assertIn("Starting Operator request handler...", sidecar_logs)
        self.assertNotRegex(
            sidecar_logs,
            r"APIForbiddenError|['\"]code['\"]:\s*403|cannot (list|watch) resource "
            r"\"(customresourcedefinitions|namespaces)\"|"
            r"(customresourcedefinitions|namespaces).* is forbidden")
        self.assertIn("sidecar: Kopf scanning disabled", sidecar_logs)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


# Test 3 member cluster with default configs
class Cluster3Defaults(tutil.OperatorTest):
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
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_pass="sakila", root_host="%")

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
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, self.cluster_name, 0)

        self.wait_pod(f"{self.cluster_name}-0", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=1)
        # no router pods expected yet
        check_routing.check_pods(self, self.ns, self.cluster_name, 0)

        for instance in range(1, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        check_routing.check_pods(self, self.ns, self.cluster_name, self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_1_check_version(self):
        def container_spec(l, name):
            for cont in l:
                if cont["name"] == name:
                    return cont
            return None

        # ensure containers have the right version and edition
        for instance in range(1, self.cluster_size):
            with self.subTest(f"{self.cluster_name}-{instance}"):
                pod = kutil.get_po(self.ns, f"{self.cluster_name}-{instance}")
                image = container_spec(pod["spec"]["initContainers"], "initmysql")["image"]

                init_containers_image_hashes = {c_status["name"]:c_status["imageID"].split("@")[1].split(':')[1] for c_status in pod["status"]["initContainerStatuses"] }
                print(init_containers_image_hashes)

                containers_image_hashes = {c_status["name"]:c_status["imageID"].split("@")[1].split(':')[1] for c_status in pod["status"]["containerStatuses"] }
                print(containers_image_hashes)

                self.assertIn(":"+g_ts_cfg.version_tag, image, "initmysql")
                self.assertIn(g_ts_cfg.server_image_name+":", image, "initmysql")

                image = container_spec(pod["spec"]["initContainers"], "initconf")["image"]
                self.assertIn(":"+g_ts_cfg.operator_version_tag, image, "initconf")
                self.assertIn(g_ts_cfg.operator_image_name+":", image, "initconf")

                image = container_spec(pod["spec"]["containers"], "mysql")["image"]
                self.assertIn(":"+g_ts_cfg.version_tag, image, "mysql")
                self.assertIn(g_ts_cfg.server_image_name+":", image, "mysql")

                image = container_spec(pod["spec"]["containers"], "sidecar")["image"]
                self.assertIn(":"+g_ts_cfg.operator_version_tag, image, "sidecar")
                self.assertIn(g_ts_cfg.operator_image_name+":", image, "sidecar")

        # check router version and edition
        for router_instance in range(1, self.routers_count):
            p = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-router-.*")[router_instance]
            with self.subTest(p["NAME"]):
                pod = kutil.get_po(self.ns, p["NAME"])
                image = container_spec(pod["spec"]["containers"], "router")["image"]
                self.assertIn(":"+g_ts_cfg.version_tag, image, "router")
                self.assertIn(g_ts_cfg.router_image_name + ":", image, "router")


    def test_1_check_accounts(self):
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

        for instance in range(1, self.cluster_size):
            with self.subTest(f"{self.cluster_name}-{instance}"):
                with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-{instance}", "root", "sakila") as s:
                    accts = set([row[0] for row in s.query_sql(
                        "SELECT concat(user,'@',host) FROM mysql.user").fetch_all()])
                    self.assertSetEqualRegex(expected_accounts, accts, "expected accounts", "existing accounts")

    def test_1_check_binlog_name(self):
        expected_name = f"/var/lib/mysql/{self.cluster_name}"

        for instance in range(1, self.cluster_size):
            with self.subTest(f"{self.cluster_name}-{instance}"):
                with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-{instance}", "root", "sakila") as s:
                    name = s.query_sql("SELECT @@log_bin_basename").fetch_all()[0][0]
                    self.assertEqual(name, expected_name)

    def run_verify_routing_session(self, address, expected_routing_settings):
        shell = mutil.MySQLInteractivePodSession(
            "appns", "testpod", user="root", password="sakila", host=address)

        query_result = shell.query_dict("select concat(@@report_host, ':', @@port) as r;")
        if not query_result:
            return False
        self.logger.debug(query_result)

        result = query_result[0]
        self.logger.debug(result)

        if type(expected_routing_settings) is str:
            self.assertEqual(result['r'], expected_routing_settings)
        else:
            self.assertIn(result['r'], expected_routing_settings)
        return True

    def verify_routing(self, address, expected_routing_settings):
        communicated = False
        trial = 0
        MAX_TRIAL = 5
        for trial in range(MAX_TRIAL):
            try:
                if self.run_verify_routing_session(address, expected_routing_settings):
                    communicated = True
                    break
            except BaseException as err:
                self.logger.error("Unexpected err={}, type(err)={}".format(err, type(err)))

        self.assertTrue(communicated, f"couldn't communicate with the host {address}")

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
      image: "{g_ts_cfg.get_operator_image()}"
      command: ["mysqlsh", "--js", "-e", "os.sleep(600)"]
      env:
        - name: MYSQLSH_USER_CONFIG_HOME
          value: /tmp
"""
        kutil.create_ns("appns", g_ts_cfg.get_custom_test_ns_labels())

        kutil.apply("appns", yaml)
        self.wait_pod("testpod", "Running", ns="appns")

        # check classic session to R/W port
        self.verify_routing(
            f"{self.cluster_name}.{self.ns}.svc.cluster.local:3306",
            f"{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306")

        # check classic session to alternate R/W port
        self.verify_routing(
            f"{self.cluster_name}.{self.ns}.svc.cluster.local:6446",
            f"{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306")

        # check classic session to R/O port
        self.verify_routing(
            f"{self.cluster_name}.{self.ns}.svc.cluster.local:6447",
            [f"{self.cluster_name}-1.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306",
                f"{self.cluster_name}-2.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306"])

        # check X session to R/W port
        self.verify_routing(
            f"{self.cluster_name}.{self.ns}.svc.cluster.local:33060",
            f"{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306")

        # check X session to alternate R/W port
        self.verify_routing(
            f"{self.cluster_name}.{self.ns}.svc.cluster.local:6448",
            f"{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306")

        # check X session to R/O port
        self.verify_routing(
            f"{self.cluster_name}.{self.ns}.svc.cluster.local:6449",
            [f"{self.cluster_name}-1.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306",
                f"{self.cluster_name}-2.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306"])

        kutil.delete_po("appns", "testpod")
        kutil.delete_ns("appns")

    def test_4_recover_crash_1_of_3(self):
        # kill mysqld (pid 1)
        kutil.kill(self.ns, (f"{self.cluster_name}-0", "mysql"), 11, 1)

        apply_time = isotime()

        # wait for operator to notice it gone
        self.wait_ic(self.cluster_name, ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], self.cluster_size - 1)

        # check status of crashed pod
        # TODO

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange", msg=rf"Cluster status changed to ONLINE_PARTIAL. {self.cluster_size - 1} member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange", msg=rf"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=None)

    def test_4_recover_crash_2_of_3(self):
        # TODO add a loadchecker

        apply_time = isotime()

        # kill mysqld (pid 1)
        for instance in range(0, self.cluster_size - 1):
            kutil.kill(self.ns, (f"{self.cluster_name}-{instance}", "mysql"), 11, 1)

        # wait for operator to notice them gone
        self.wait_ic(self.cluster_name, "NO_QUORUM")

        # check status of crashed pods
        # TODO

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange", msg=r"Cluster status changed to NO_QUORUM. 0 member\(s\) ONLINE")

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="RestoreQuorum", msg="Restoring quorum of cluster")

        # sometimes gets skipped
        # self.assertGotClusterEvent(
        #     self.cluster_name, after=apply_time, type="Normal",
        #     reason="StatusChange", msg=r"Cluster status changed to ONLINE_PARTIAL.  member\(s\) ONLINE")

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Rejoin", msg=rf"Rejoining {self.cluster_name}-\d to cluster")

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange", msg=rf"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=self.cluster_size-1)

    def test_4_recover_crash_3_of_3(self):
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0:
            pod0_uuid = s0.query_sql("select @@server_uuid").fetch_one()[0]

        # kill mysqld (pid 1)
        for instance in range(0, self.cluster_size):
            kutil.kill(self.ns, (f"{self.cluster_name}-{instance}", "mysql"), 11, 1)

        # wait for operator to notice them gone
        self.wait_ic(self.cluster_name, "OFFLINE", 0)

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, timeout=self.cluster_size*120)

        for instance in range(0, self.cluster_size):
            self.wait_member_state(f"{self.cluster_name}-{instance}", ["ONLINE"])

        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count)

        # switch primary back to -0
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0:
            s0.exec_sql(f"do group_replication_set_as_primary('{pod0_uuid}')")

    def test_4_recover_crash_3_of_3_changed_primary(self):
        """
        Tests the case where a reboot is necessary but the PRIMARY used to be
        a member other than -0. We need to ensure the reboot started with that
        member that was the PRIMARY.
        """

        # generate transactions at {self.cluster_name}-1 while the other members are stopped
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0,\
            mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s1,\
            mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-2", "root", "sakila") as s2:
            pod0_uuid = s0.query_sql("select @@server_uuid").fetch_one()[0]

            s0.exec_sql("stop group_replication")
            s2.exec_sql("stop group_replication")
            s1.exec_sql("create schema something_something")

        # kill mysqld (pid 1)
        # (vary the scenario a little killing -2 since GR is stopped anyway)
        for instance in range(1, self.cluster_size - 1):
            kutil.kill(self.ns, (f"{self.cluster_name}-{instance}", "mysql"), 11, 1)

        # wait for operator to notice them gone
        self.wait_ic(self.cluster_name, "OFFLINE", num_online=0)

        # wait for operator to restore it
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, timeout=self.cluster_size*120)

        for instance in range(0, self.cluster_size):
            self.wait_member_state(f"{self.cluster_name}-{instance}", ["ONLINE"])

        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=1)

        # switch primary back to -0
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0:
            s0.exec_sql(f"do group_replication_set_as_primary('{pod0_uuid}')")


    def test_4_recover_delete_1_of_3(self):
        # delete the PRIMARY
        kutil.delete_po(self.ns, f"{self.cluster_name}-0")

        self.wait_ic(self.cluster_name, ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], 2)

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=3)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        pod0 = kutil.get_po(self.ns, f"{self.cluster_name}-0")

        # the pod was deleted, so restarts resets to 0
        self.assertEqual(pod0["status"]["containerStatuses"]
                         [0]["restartCount"], 0)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=None)

        kutil.exec(self.ns, (f"{self.cluster_name}-0", "sidecar"),
                   ["mysqlsh", "root:sakila@localhost", "--",
                    "cluster", "set-primary-instance",
                    f"{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306"])

        cross_sync_gtids(
            self.ns, [f"{self.cluster_name}-{instance}" for instance in range(0, self.cluster_size)],
            "root", "sakila")

        all_pods, _ = check_all(self, self.ns, self.cluster_name,
                                instances=self.cluster_size, routers=self.routers_count, primary=0)

        check_group.check_data(self, all_pods, primary=0)

    def test_4_recover_delete_2_of_3(self):
        p0ts = kutil.get_po(
            self.ns, f"{self.cluster_name}-0")["metadata"]["creationTimestamp"]
        p1ts = kutil.get_po(
            self.ns, f"{self.cluster_name}-1")["metadata"]["creationTimestamp"]

        apply_time = isotime()

        # it may take longer than the default timeout (failures on jenkins)
        kutil.delete_po(self.ns, f"{self.cluster_name}-0", timeout=200)

        # extra timeout because the deletion of the 2nd pod will be blocked by
        # the busy handlers from the 1st deletion
        kutil.delete_po(self.ns, f"{self.cluster_name}-1", timeout=200)

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, timeout=300)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        # the pods were deleted, which means they would cleanly shutdown and
        # removed from the cluster
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Join", msg=f"Joining {self.cluster_name}-0 to cluster")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange",
            msg=rf"Cluster status changed to ONLINE_PARTIAL. {self.cluster_size - 1} member\(s\) ONLINE")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="Join", msg=f"Joining {self.cluster_name}-1 to cluster")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="StatusChange",
            msg=rf"Cluster status changed to ONLINE. {self.cluster_size} member\(s\) ONLINE")

        pod0 = kutil.get_po(self.ns, f"{self.cluster_name}-0")
        pod1 = kutil.get_po(self.ns, f"{self.cluster_name}-1")

        # make sure that the pods were actually deleted and recreated
        self.assertNotEqual(p0ts, pod0["metadata"]["creationTimestamp"])
        self.assertNotEqual(p1ts, pod1["metadata"]["creationTimestamp"])

        # the pod was deleted, so restarts resets to 0
        self.assertEqual(pod0["status"]["containerStatuses"]
                         [0]["restartCount"], 0)
        self.assertEqual(pod1["status"]["containerStatuses"]
                         [0]["restartCount"], 0)

        cross_sync_gtids(
            self.ns, [f"{self.cluster_name}-2", f"{self.cluster_name}-0", f"{self.cluster_name}-1"],
            "root", "sakila")
        cross_sync_gtids(
            self.ns, [f"{self.cluster_name}-1", f"{self.cluster_name}-2", f"{self.cluster_name}-0"],
            "root", "sakila")
        cross_sync_gtids(
            self.ns, [f"{self.cluster_name}-0", f"{self.cluster_name}-2", f"{self.cluster_name}-1"],
            "root", "sakila")

        all_pods, _ = check_all(self, self.ns, self.cluster_name,
                                instances=self.cluster_size, routers=self.routers_count, primary=2)
        check_group.check_data(self, all_pods)

        kutil.exec(self.ns, (f"{self.cluster_name}-0", "sidecar"), ["mysqlsh", "root:sakila@localhost", "--", "cluster",
                                                         "set-primary-instance", f"{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306"])

    def test_4_recover_delete_and_wipe_1_of_3(self):
        # delete the pv and pvc first, which will block because until the pod
        # is deleted

        # delete a secondary
        kutil.delete_po(self.ns, f"{self.cluster_name}-1")

        self.wait_ic(self.cluster_name, ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], num_online=self.cluster_size - 1)

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        pod1 = kutil.get_po(self.ns, f"{self.cluster_name}-1")

        # the pod was deleted, so restarts resets to 0
        self.assertEqual(pod1["status"]["containerStatuses"]
                         [0]["restartCount"], 0)

        all_pods, _ = check_all(self, self.ns, self.cluster_name,
                                instances=self.cluster_size, routers=self.routers_count, primary=0)

        check_group.check_data(self, all_pods, primary=0)

    @unittest.skip("TODO: This test needs to be fixed")
    def test_4_recover_stop_1_of_3(self):
        """
        Manually stop GR in 1 instance out of 3.

        TODO decide what to do, leave alone or restore?
        """
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s0:
            s0.exec_sql("stop group_replication")

        # TODO ensure router traffic is not ending up there

        # wait for operator to notice it OFFLINE
        self.wait_ic(self.cluster_name, "ONLINE_PARTIAL", self.cluster_size - 1)

        # restart GR and wait until everything is back to normal
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s0:
            s0.exec_sql("start group_replication")

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        # TODO ensure router traffic is resumed

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    @unittest.skip("TODO: This test needs to be fixed")
    def test_4_recover_stop_2_of_3(self):
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-2", "root", "sakila") as s2:
            s0.exec_sql("stop group_replication")
            s2.exec_sql("stop group_replication")

        # wait for operator to notice it ONLINE_PARTIAL
        self.wait_ic(self.cluster_name, "ONLINE_PARTIAL", num_online=self.cluster_size-2)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE")
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=1)

    @unittest.skip("TODO: This test needs to be fixed")
    def test_4_recover_stop_3_of_3(self):
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s1,\
                mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-2", "root", "sakila") as s2:
            s0.exec_sql("stop group_replication")
            s1.exec_sql("stop group_replication")
            s2.exec_sql("stop group_replication")

        # wait for operator to notice it OFFLINE
        self.wait_ic(self.cluster_name, "OFFLINE", num_online=self.cluster_size - 3)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_4_recover_restart_1_of_3(self):
        initial_probe_time = kutil.get_ic(self.ns, self.cluster_name)["status"]["cluster"]["lastProbeTime"]

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0:
            s0.exec_sql("restart")

        # wait for operator to notice it OFFLINE
        self.wait_ic(self.cluster_name, ["ONLINE_PARTIAL", "ONLINE"], probe_time=initial_probe_time)

        # check status of the restarted pod
        # TODO

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, timeout=300)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=None)

    def test_4_recover_restart_2_of_3(self):
        initial_probe_time = kutil.get_ic(self.ns, self.cluster_name)["status"]["cluster"]["lastProbeTime"]

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-2", "root", "sakila") as s2:
            s0.exec_sql("restart")
            s2.exec_sql("restart")

        # wait for operator to notice it ONLINE_PARTIAL
        self.wait_ic(self.cluster_name, ["ONLINE_PARTIAL", "ONLINE"], num_online=self.cluster_size - 2, probe_time=initial_probe_time)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=1)

    def test_4_recover_restart_3_of_3(self):
        initial_probe_time = kutil.get_ic(self.ns, self.cluster_name)["status"]["cluster"]["lastProbeTime"]

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0,\
                mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s1,\
                mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-2", "root", "sakila") as s2:
            s0.exec_sql("restart")
            s1.exec_sql("restart")
            s2.exec_sql("restart")

        # wait for operator to notice it OFFLINE
        self.wait_ic(self.cluster_name, "OFFLINE", num_online=self.cluster_size - 3)

        # check status of each pod

        # wait for operator to restore everything
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size, probe_time=initial_probe_time)
        self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count)

        all_pods, _ = check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count)
        check_group.check_data(self, all_pods)

    def test_9_destroy(self):
        # XXX deleting the sts shouldn't be necessary, but it's not happening when the ic is deleted
        # REALLY??
        kutil.delete_sts(self.ns, self.cluster_name)

        kutil.delete_ic(self.ns, self.cluster_name, timeout=self.cluster_size*150) #120 is the grace period
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


class ClusterRaces(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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

    def test_1_create_and_delete(self):
        """
        Create and delete a cluster immediately, before it becomes ONLINE.
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
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING"])

        # deleting a cluster right after it's created and before it's ONLINE
        # was causing kopf to enter a loop and not finish deleting before
        # kopf 0.28.2 (github/nolar/kopf issue #601)
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_ic_gone(self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

class Cluster3Defaults2Nodes(tutil.OperatorTest):
    pass


class TwoClustersOneNamespace(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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

    def test_0_create_1(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_pass="sakila", root_host="%")

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
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, self.cluster_name, num_pods=0)

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_0_create_2(self):
        kutil.create_user_secrets(self.ns, f"{self.cluster_secret_name}-2", root_pass="sakilax", root_host="%")
        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}-2
spec:
  instances: {self.cluster_size}
  router:
    instances: 2
  secretName: {self.cluster_secret_name}-2
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, f"{self.cluster_name}-2", 0)

        self.wait_pod(f"{self.cluster_name}-2-0", "Running")

        self.wait_ic(f"{self.cluster_name}-2", "ONLINE", num_online=self.cluster_size)

        self.wait_routers(f"{self.cluster_name}-2-router-*", 2)

        check_all(self, self.ns, f"{self.cluster_name}-2", instances=self.cluster_size, routers=2,
                  primary=0, password="sakilax", shared_ns=True)

    def test_1_destroy_1(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        # Don't use self.wait_pods_gone(f"{self.cluster_name}-*")
        # as this will match also the pods of the second cluster which will stay alive
        # because we haven't deleted the second IC
        # or we need a pattern that maches exastly one symbol after the dash
        for instance in range(0, self.cluster_size):
            self.wait_pod_gone(f"{self.cluster_name}-{instance}")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

        # {self.cluster_name}-2 should still be fine
        check_all(self, self.ns, f"{self.cluster_name}-2", instances=self.cluster_size, routers=2, password="sakilax", primary=0)

    def test_1_destroy_2(self):
        kutil.delete_ic(self.ns, f"{self.cluster_name}-2")
        self.wait_pods_gone(f"{self.cluster_name}-2-*")
        self.wait_routers_gone(f"{self.cluster_name}-2-router-*")
        self.wait_ic_gone(f"{self.cluster_name}-2")
        kutil.delete_secret(self.ns, f"{self.cluster_secret_name}-2")

    def test_99_destroy(self):
        kutil.delete_pvc(self.ns, None)



class ClusterCustomConf(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name_valid = "myvalid-cluster-name-28-char"
    _cluster_size = 2
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        assert len(cls.cluster_name_valid) == 28

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
        Checks:
        - cluster name can be 28chars long
        - root user name and host can be customized
        - base server id can be changed
        - version can be customized
        - mycnf can be specified
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")

        # create cluster with mostly default configs, but a specific server version
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name_valid}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  version: "{g_ts_cfg.get_current_lts_version()}"
  baseServerId: 3210
  tlsUseSelfSigned: true
  mycnf: |
    [mysqld]
    admin_port=3333
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name_valid, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name_valid}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name_valid}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name_valid, "ONLINE", num_online=self.cluster_size)

        check_all(self, self.ns, self.cluster_name_valid, instances=self.cluster_size, routers=self.routers_count,
                  primary=0, user="admin", password="secret")

        with mutil.MySQLPodSession(self.ns, self.cluster_name_valid+"-0", user="admin", password="secret") as session:
            aport, sid, ver = session.query_sql(
                "select @@admin_port, @@server_id, @@version").fetch_one()
            self.assertEqual(aport, 3333)
            self.assertEqual(sid, 3210)
            self.assertEqual(ver.split('-')[0], g_ts_cfg.get_current_lts_version())

            users = list(session.query_sql(
                "select user,host from mysql.user where user='root'").fetch_all())
            self.assertEqual(users, [])

        with mutil.MySQLPodSession(self.ns, self.cluster_name_valid+"-1", user="admin", password="secret") as session:
            aport, sid, ver = session.query_sql(
                "select @@admin_port, @@server_id, @@version").fetch_one()
            self.assertEqual(aport, 3333)
            self.assertEqual(sid, 3211)
            self.assertEqual(ver.split('-')[0], g_ts_cfg.get_current_lts_version())

            users = list(session.query_sql(
                "select user,host from mysql.user where user='root'").fetch_all())
            self.assertEqual(users, [])

        pod = kutil.get_po(self.ns, self.cluster_name_valid+"-0")
        cont = check_apiobjects.check_pod_container(self, pod, "mysql", None, True)
        self.assertEqual(cont["image"], g_ts_cfg.get_current_lts_version_server_image())

        cont = check_apiobjects.check_pod_container(self, pod, "sidecar", None, True)
        self.assertEqual(cont["image"], g_ts_cfg.get_operator_image())

        # check version of router images
        pods = kutil.ls_po(self.ns, pattern=self.cluster_name_valid+"-.*-router")
        for p in pods:
            pod = kutil.get_po(self.ns, p["NAME"])
            cont = check_apiobjects.check_pod_container(
                self, pod, None, None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_old_router_image(), p["NAME"])

    # TODO config change in spec (and decide what to do)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name_valid)

        self.wait_pods_gone(f"{self.cluster_name_valid}-*")
        self.wait_routers_gone(f"{self.cluster_name_valid}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


class ClusterCustomImageConf(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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

    def test_0_create(self):
        """
        Checks:
        - imagePullSecrets is propagated
        - version is propagated
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")

        # create cluster with mostly default configs, but a specific server version
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  version: "{g_ts_cfg.get_current_lts_version()}"
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  imagePullSecrets:
  - name: pullsecrets
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count,
                  primary=0, user="admin", password="secret")

        # check server pod
        pod = kutil.get_po(self.ns, f"{self.cluster_name}-0")

        self.assertEqual(pod["spec"]["imagePullSecrets"],
                         [{"name": "pullsecrets"}])

        cont = check_apiobjects.check_pod_container(self, pod, "mysql", None, True)
        self.assertEqual(cont["image"], g_ts_cfg.get_current_lts_version_server_image())

        cont = check_apiobjects.check_pod_container(self, pod, "sidecar", None, True)
        self.assertEqual(cont["image"],g_ts_cfg.get_operator_image())

        # check router pod
        pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-router-.*")
        for p in pods:
            pod = kutil.get_po(self.ns, p["NAME"])

            self.assertEqual(pod["spec"]["imagePullSecrets"], [
                             {"name": "pullsecrets"}])

            cont = check_apiobjects.check_pod_container(self, pod, "router", None, True)
            self.assertEqual(
                cont["image"],
                g_ts_cfg.get_current_lts_version_router_image(), p["NAME"])

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


class Cluster1CloneWorksWhenTransactionMissingFromBinlog(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
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

    def test_0_scaling_after_removing_some_binlogs_works(self):
        """
        Checks:
        - that new instance will be populated properly even if some binlogs are missing
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs, but a specific server version
        yaml =f"""
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
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql("set autocommit=1")

            # Make sure we have some transactions in the binlog
            s.exec_sql("CREATE SCHEMA foo")
            s.exec_sql("CREATE TABLE foo.t (id INT NOT NULL, PRIMARY KEY(id))")
            s.exec_sql("INSERT INTO foo.t VALUES (1)")

            # Flush binlog so we can purge
            s.exec_sql("FLUSH BINARY LOGS")
            s.exec_sql("INSERT INTO foo.t VALUES (2)")

            # Purge "old" logs
            binlogname = s.query_sql("SHOW BINARY LOGS").fetch_all().pop()[0]
            s.exec_sql(f"PURGE BINARY LOGS TO '{binlogname}'")

        # Try to scale up
        self.cluster_size = 2
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")
        self.wait_pod(f"{self.cluster_name}-1", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        # Ensure clone copied all data
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s:
            rowcount = s.query_sql("SELECT COUNT(*) FROM foo.t").fetch_one()[0]
            self.assertEqual(rowcount, 2)

        # Scale down, and  scale back up and verify replica catches up from old data
        self.cluster_size = 1
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")
        self.wait_pod_gone(f"{self.cluster_name}-1")

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql("set autocommit=1")
            s.query_sql("INSERT INTO foo.t VALUES (3)")

            # This transcation will only be seen on the replica if clone was used
            # erroneously instead of incremental
            s.exec_sql("set session sql_log_bin=0")
            s.exec_sql("INSERT INTO foo.t VALUES (4)")
            s.exec_sql("set session sql_log_bin=1")

        self.cluster_size = 2
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")
        self.wait_pod(f"{self.cluster_name}-1", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s:
            count = s.query_sql("SELECT COUNT(*) FROM foo.t").fetch_one()[0]
            self.assertEqual(count, 3)

        # If transactions are missing from the binlog the old datadir can not be recovered
        # and we have to clone
        self.cluster_size = 1
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")
        self.wait_pod_gone(f"{self.cluster_name}-1")

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql("set autocommit=1")

            # Flush binlog so we can purge
            s.exec_sql("FLUSH BINARY LOGS")
            s.exec_sql("INSERT INTO foo.t VALUES (5)")

            # Purge "old" logs
            binlogname = s.query_sql("SHOW BINARY LOGS").fetch_all().pop()[0]
            s.exec_sql(f"PURGE BINARY LOGS TO '{binlogname}'")

        self.cluster_size = 2
        kutil.patch_ic(self.ns, self.cluster_name, {
                       "spec": {"instances": self.cluster_size}}, type="merge")
        self.wait_pod(f"{self.cluster_name}-1", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s:
            count = s.query_sql("SELECT COUNT(*) FROM foo.t").fetch_one()[0]
            self.assertEqual(count, 4)

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

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
