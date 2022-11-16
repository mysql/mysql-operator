# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import json
import logging
from e2e.mysqloperator.cluster import check_apiobjects
from e2e.mysqloperator.cluster.cluster_t import check_all
from utils.auxutil import isotime
from utils import kutil
from utils import tutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log
from setup.config import g_ts_cfg

class Handle29Base(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_size = 3
    routers_count = 2

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    # --------------------

    def create_cluster(self, version_tag):
        kutil.create_default_user_secrets(self.ns)

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
    name: mycluster
spec:
    instances: {self.cluster_size}
    router:
        instances: {self.routers_count}
    secretName: mypwds
    tlsUseSelfSigned: true
    version: "{version_tag}"
"""
        kutil.apply(self.ns, yaml)


    def change_cluster_version(self, version_tag):
        update_time = isotime()

        kutil.patch_ic(self.ns, "mycluster", {"spec": {
            "version": version_tag
        }}, type="merge")

        return update_time


    def destroy_cluster(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    # --------------------

    def get_pod_version(self, pod):
        pod_info = json.loads(pod["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}"))
        return pod_info.get("version", "")

    def verify_pod_version(self, pod, version_tag):
        pod_version = self.get_pod_version(pod)
        self.assertEqual(pod_version, version_tag)

    def verify_pod_images(self, pod, version_tag):
        cont = check_apiobjects.check_pod_container(
            self, pod, "mysql", None, True)
        self.assertEqual(cont["image"], g_ts_cfg.get_server_image(version_tag))

        cont = check_apiobjects.check_pod_container(
            self, pod, "sidecar", None, True)
        self.assertEqual(cont["image"], g_ts_cfg.get_operator_image())

    def verify_router_image(self, router_name, version_tag):
        router = kutil.get_po(self.ns, router_name)
        cont = check_apiobjects.check_pod_container(
            self, router, "router", None, True)
        self.assertEqual(cont["image"],g_ts_cfg.get_router_image(version_tag))

    def verify_pod_version_n_images(self, pod_name, version_tag):
        pod = kutil.get_po(self.ns, pod_name)
        self.verify_pod_version(pod, version_tag)
        self.verify_pod_images(pod, version_tag)

    def verify_cluster_version(self, version_tag):
        pods = ["mycluster-0", "mycluster-1", "mycluster-2"]
        for pod_name in pods:
            self.verify_pod_version_n_images(pod_name, version_tag)

        routers = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        for router in routers:
            self.verify_router_image(router["NAME"], version_tag)


    def verify_update_rejected(self, update_time, from_version, to_version):
        # e.g.
        # 16m  Normal  Logging   innodbcluster/mycluster  Propagating spec.version=8.0.29 for namespace/mycluster (was 8.0.28)
        # 16m  Error   Logging   innodbcluster/mycluster  Handler 'on_innodbcluster_field_version/spec.version' failed permanently: Support for MySQL 8.0.29 is disabled. Please see http://....
        # 16m  Normal  Logging   innodbcluster/mycluster  Updating is processed: 0 succeeded; 1 failed.
        self.wait_got_cluster_event(
            "mycluster", after=update_time, type="Normal",
            reason="Logging",
            msg=rf"Propagating spec.version={to_version} for {self.ns}/mycluster \(was {from_version}\)")
        self.wait_got_cluster_event(
            "mycluster", after=update_time, type="Error",
            reason="Logging",
            msg=rf"Handler 'on_innodbcluster_field_version/spec.version' failed permanently\: Support for MySQL {to_version} is disabled. Please see https\://dev.mysql.com/doc/relnotes/mysql-operator/en/news-8-0-29.html")
        self.wait_got_cluster_event(
            "mycluster", after=update_time, type="Normal",
            reason="Logging",
            msg=r"Updating is processed\: 0 succeeded; 1 failed.")

    # --------------------

    def verify_cluster_running(self):
        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", num_online=self.cluster_size)

        self.wait_routers("mycluster-router-*", self.routers_count)

        check_all(self, self.ns, "mycluster",
            instances=self.cluster_size, routers=self.routers_count)


    def wait_pod_version(self, pod_name, version_tag):
        def poll_pod_version():
            pod = kutil.get_po(self.ns, pod_name, check=False)
            if pod:
                return self.get_pod_version(pod)
            return None

        self.wait(poll_pod_version,
            check=lambda s: s == version_tag, timeout=300, delay=10)

    def verify_cluster_updated(self, version_tag):
        pods = ["mycluster-2", "mycluster-1", "mycluster-0"]
        for pod_name in pods:
            self.wait_pod_version(pod_name, version_tag)


    def verify_cluster_invalid(self):
        self.wait_ic("mycluster", "INVALID", num_online=0)
