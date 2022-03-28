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

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "myrouterspec-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "myrouterspec-0")

        super().tearDownClass()

    def test_1_router_spec_affinity(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: myrouterspec
spec:
  instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
  router:
    instances: 1
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
        check_routing.check_pods(self, self.ns, "myrouterspec", 0)

        self.wait_pod("myrouterspec-0", "Running")

        self.wait_ic("myrouterspec", "ONLINE", 1)

        self.wait_routers("myrouterspec-router-*", 1)

        check_all(self, self.ns, "myrouterspec", instances=1, routers=1)

        p = kutil.ls_po(self.ns, pattern="myrouterspec-router-.*")[0]
        routerPod = kutil.get_po(self.ns, p["NAME"])

        podAntiAffinity = routerPod["spec"]["affinity"]["podAntiAffinity"]
        preferredDuringSchedulingIgnoredDuringExecution0 = podAntiAffinity["preferredDuringSchedulingIgnoredDuringExecution"][0]
        self.assertEqual(preferredDuringSchedulingIgnoredDuringExecution0["weight"], 2)
        podAffinityTerm = preferredDuringSchedulingIgnoredDuringExecution0["podAffinityTerm"]
        self.assertEqual(podAffinityTerm["topologyKey"], "foo")
        self.assertEqual(podAffinityTerm["labelSelector"]["matchLabels"]["a.label.nobody.sets"], "just_a_test")


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "myrouterspec", 180)

        self.wait_pod_gone("myrouterspec-0")
        self.wait_ic_gone("myrouterspec")

        kutil.delete_secret(self.ns, "mypwds")
