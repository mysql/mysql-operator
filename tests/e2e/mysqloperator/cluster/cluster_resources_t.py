# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from . import check_routing
from .cluster_t import check_all
import logging
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import COMMON_OPERATOR_ERRORS

class ClusterResources(tutil.OperatorTest):
    """
    cluster resource allocation/affinity/taint/podSpec
    """
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

    def test_1_router_spec_affinity(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  router:
    instances: {self.routers_count}
    version: "{g_ts_cfg.version_tag}"
    podSpec:
      affinity:
        podAntiAffinity:
          preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 2
              podAffinityTerm:
                topologyKey: foo
                labelSelector:
                  matchLabels:
                    a.label.nobody.sets: just_a_test
"""

        kutil.apply(self.ns, yaml)

        # ensure router pods don't get created until the cluster is ONLINE
        check_routing.check_pods(self, self.ns, self.cluster_name, 0)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count)

        p = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-router-.*")[0]
        routerPod = kutil.get_po(self.ns, p["NAME"])

        podAntiAffinity = routerPod["spec"]["affinity"]["podAntiAffinity"]
        preferredDuringSchedulingIgnoredDuringExecution0 = podAntiAffinity["preferredDuringSchedulingIgnoredDuringExecution"][0]
        self.assertEqual(preferredDuringSchedulingIgnoredDuringExecution0["weight"], 2)
        podAffinityTerm = preferredDuringSchedulingIgnoredDuringExecution0["podAffinityTerm"]
        self.assertEqual(podAffinityTerm["topologyKey"], "foo")
        self.assertEqual(podAffinityTerm["labelSelector"]["matchLabels"]["a.label.nobody.sets"], "just_a_test")


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
