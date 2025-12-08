# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
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
    version: "{g_ts_cfg.version_tag}"
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  version: "{g_ts_cfg.get_current_lts_version()}"
"""
        print(yaml)
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        print(kutil.ls_sts(self.ns))

        if self.routers_count:
            print(kutil.ls_deploy(self.ns))
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        _, router_pods = check_all(self, self.ns, self.cluster_name, version=g_ts_cfg.get_current_lts_version(),
                                   instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            pod_name = f"{self.cluster_name}-{instance}"
            pod = kutil.get_po(self.ns, pod_name)

            cont = check_apiobjects.check_pod_container(self, pod, "mysql", None, True)
            self.assertEqual(cont["image"], g_ts_cfg.get_current_lts_version_server_image())

            cont = check_apiobjects.check_pod_container(self, pod, "sidecar", None, True)
            self.assertEqual(cont["image"], g_ts_cfg.get_operator_image())

        for pod_name in map(lambda pod: pod["NAME"], router_pods):
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(self, pod, "router", None, True)
            self.assertEqual(cont["image"], g_ts_cfg.get_router_image())


    def test_1_upgrade_to_latest(self):
        """
        version is now LTS, but we upgrade it to 9.{VERSION}.0
        """

        kutil.patch_ic(self.ns, self.cluster_name, {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        def check_done(pod):
            po = kutil.get_po(self.ns, pod)
            # self.logger.debug(json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")))
            return json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")).get("version", "")

        for instance in reversed(range(0, self.cluster_size)):
            self.wait(check_done, args=(f"{self.cluster_name}-{instance}", ),
                      check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=300, delay=10)

        self.wait_ic(self.cluster_name, "ONLINE", self.cluster_size)

        print(kutil.ls_sts(self.ns))
        print(kutil.ls_deploy(self.ns))
        self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count)

        # TODO check that mysql is upgraded ok
        _, router_pods = check_all(self, self.ns, self.cluster_name, version=g_ts_cfg.version_tag,
                                   instances=self.cluster_size, routers=self.routers_count, primary=None)

        for instance in range(0, self.cluster_size):
            pod_name = f"{self.cluster_name}-{instance}"
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(self, pod, "mysql", None, True)
            self.assertEqual(cont["image"], g_ts_cfg.get_server_image())

            cont = check_apiobjects.check_pod_container(self, pod, "sidecar", None, True)
            self.assertEqual(cont["image"], g_ts_cfg.get_operator_image())

        for pod_name in map(lambda pod: pod["NAME"], router_pods):
            pod = kutil.get_po(self.ns, pod_name)
            cont = check_apiobjects.check_pod_container(self, pod, "router", None, True)
            self.assertEqual(cont["image"], g_ts_cfg.get_router_image())

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


# TODO bind router to an old version, then let it get upgraded automatically


# TODO test with 1 member


# TODO rolling config change

# TODO ugprade to invalid version
