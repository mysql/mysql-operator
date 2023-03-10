# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
import logging
import json
from e2e.mysqloperator.cluster import check_apiobjects
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import COMMON_OPERATOR_ERRORS
from .cluster_t import check_all


class BadVersionChanges(tutil.OperatorTest):
    pass
    # TODO check events that appear on describe ic


class UpgradeToLatest(tutil.OperatorTest):
    pass


class UpgradeToNext(tutil.OperatorTest):
    # Upgrade by 1 version
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
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_create(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 3
  router:
    instances: 2
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  version: "{g_ts_cfg.get_old_version_tag()}"
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", 3)

        self.wait_routers("mycluster-router-*", 2)

        _, router_pods = check_all(self, self.ns, "mycluster", version=g_ts_cfg.get_old_version_tag(),
                                   instances=3, routers=2, primary=0)

        for pod_name in ["mycluster-0", "mycluster-1", "mycluster-2"]:
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(
                self, pod, "mysql", None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_old_server_image())
            cont = check_apiobjects.check_pod_container(
                self, pod, "sidecar", None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_operator_image())

        for pod_name in map(lambda pod: pod["NAME"], router_pods):
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(
                self, pod, "router", None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_old_router_image())


    def test_1_upgrade(self):
        """
        version is now 8.0.{VERSION}, but we upgrade it to 8.0.{VERSION+1}
        This will upgrade MySQL only, not the Router since it's already latest.
        """

        kutil.patch_ic(self.ns, "mycluster", {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        def check_done(pod):
            po = kutil.get_po(self.ns, pod)
            # self.logger.debug(json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")))
            return json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")).get("version", "")

        self.wait(check_done, args=("mycluster-2", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=300, delay=10)
        self.wait(check_done, args=("mycluster-1", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=300, delay=10)
        self.wait(check_done, args=("mycluster-0", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=300, delay=10)

        self.wait_ic("mycluster", "ONLINE", 3)

        self.wait_routers("mycluster-router-*", 2)

        # TODO check that mysql is upgraded ok
        _, router_pods = check_all(self, self.ns, "mycluster", version=g_ts_cfg.version_tag,
                                   instances=3, routers=2, primary=None)

        for pod_name in ["mycluster-0", "mycluster-1", "mycluster-2"]:
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(
                self, pod, "mysql", None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_server_image())
            cont = check_apiobjects.check_pod_container(
                self, pod, "sidecar", None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_operator_image())

        for pod_name in map(lambda pod: pod["NAME"], router_pods):
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(
                self, pod, "router", None, True)
            self.assertEqual(
                cont["image"], g_ts_cfg.get_router_image())


    def test_1_upgrade_router(self):
        pass

        # TODO check that routers were upgraded ok

        # TODO check that everything is working ok

        # TODO check no client downtime

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


# TODO bind router to an old version, then let it get upgraded automatically


# TODO test with 1 member


# TODO rolling config change

# TODO ugprade to invalid version
