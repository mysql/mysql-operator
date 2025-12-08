# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
import unittest
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS
import os
from .cluster_ssl_t import CLUSTER_SSL_NAMESPACE

def check_sidecar_health(test, ns, pod):
    logs = kutil.logs(ns, [pod, "sidecar"])
    # check that the sidecar is running and waiting for events
    test.assertIn("Starting Operator request handler...", logs)

def check_all(test, ns, cluster_name, rr_name, instances, user="root", password="sakila", shared_ns=False, version=None):
    rr_pods = kutil.ls_po(ns, pattern=f"{cluster_name}-{rr_name}-.*")
    test.assertEqual(len(rr_pods), instances)
    for replica in rr_pods:
        test.assertEqual(replica["STATUS"], "Running", replica["NAME"])

        replica_pod = kutil.get_po(ns, replica["NAME"])
        check_sidecar_health(test, ns, replica_pod["metadata"]["name"])

    with mutil.MySQLPodSession(ns, f"{cluster_name}-0", user, password) as s:
        res = s.query_sql(f"select count(*) from mysql_innodb_cluster_metadata.v2_instances where instance_type = 'read-replica' and label like '{cluster_name}-{rr_name}-%'")
        test.assertEqual(res.fetch_one()[0], instances)


class ClusterReadReplicaDefaultsBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    use_self_signed = True
    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        # The certificates the test uses are created for CLUSTER_SSL_NAMESPACE and they won't work
        # for the namespace generated from the name of the test. Thus, we need to overwrite the NS
        # see cluster_ssl_t.py for more examples
        if not cls.use_self_signed:
            super().setUpClass(CLUSTER_SSL_NAMESPACE)
            cls.cluster_name = "mycluster"
        else:
            super().setUpClass()

        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)

        super().tearDownClass()

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        if not self.use_self_signed:
            kutil.create_ssl_ca_secret(self.ns, f"{self.cluster_name}-ca",
                os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
            kutil.create_ssl_cert_secret(self.ns, f"{self.cluster_name}-tls",
                os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
                os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

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
  tlsUseSelfSigned: {self.use_self_signed}
  podLabels:
    myc-label1: "myc-label1-value"
    myc-label2: "myc-label2-value"
  podAnnotations:
    myc.example.com/ann1: "ann1-value"
    myc.example.com/ann2: "ann2-value"
  readReplicas:
  - name: trr
    instances: 1
    baseServerId: 500
    podLabels:
      myc-rr-label1: "myc-label1-value"
      myc-rr-label2: "myc-label2-value"
    podAnnotations:
      myc.example.com/rr-ann1: "ann1-value"
      myc.example.com/rr-ann2: "ann2-value"
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_pod(f"{self.cluster_name}-trr-0", "Running", ready=True)

        check_all(self, self.ns, self.cluster_name, "trr", instances=1)

    def _02_check_labels_and_annotations(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['myc-label1'], 'myc-label1-value')
            self.assertEqual(pod['metadata']['labels']['myc-label2'], 'myc-label2-value')
            self.assertEqual(pod['metadata']['annotations']['myc.example.com/ann1'], 'ann1-value')
            self.assertEqual(pod['metadata']['annotations']['myc.example.com/ann2'], 'ann2-value')

        replica_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-trr-\d")
        pod_names = [replica["NAME"] for replica in replica_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['myc-rr-label1'], 'myc-label1-value')
            self.assertEqual(pod['metadata']['labels']['myc-rr-label2'], 'myc-label2-value')
            self.assertEqual(pod['metadata']['annotations']['myc.example.com/rr-ann1'], 'ann1-value')
            self.assertEqual(pod['metadata']['annotations']['myc.example.com/rr-ann2'], 'ann2-value')

    def _04_renaming_read_replica_leads_to_recreation(self):
        patch = {
                    "spec": {
                        "readReplicas": [{
                            "name": "trr2",
                            "baseServerId": 510
                        }]
                    }
        }
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        self.wait_pod_gone(f"{self.cluster_name}-trr-0")
        self.wait_pod(f"{self.cluster_name}-trr2-0", "Running", ready=True)

        check_all(self, self.ns, self.cluster_name, "trr", instances=0)
        check_all(self, self.ns, self.cluster_name, "trr2", instances=1)

    def _06_chages_to_read_replica_respected(self):
        patch = {
                    "spec": {
                        "readReplicas": [{
                            "name": "trr2",
                            "baseServerId": 510,
                            "instances": 2
                        }]
                    }
        }
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        self.wait_pod(f"{self.cluster_name}-trr2-1", "Running", ready=True)

        check_all(self, self.ns, self.cluster_name, "trr2", instances=2)

    def _08_remove_read_replica(self):
        patch = [{"op": "remove", "path": "/spec/readReplicas"}]
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="json", data_as_type="json")
        self.wait_pods_gone(f"{self.cluster_name}-trr2-*")

        check_all(self, self.ns, self.cluster_name, "trr2", instances=0)

    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def runit(self):
        self._00_create()
        self._02_check_labels_and_annotations()
        self._04_renaming_read_replica_leads_to_recreation()
        self._06_chages_to_read_replica_respected()
        self._08_remove_read_replica()
        self._99_destroy()


class ClusterReadReplicaDefaultsSelfSigned(ClusterReadReplicaDefaultsBase):
    use_self_signed = True
    def testit(self):
        self.runit()

@unittest.skip("Needs WL16123 - GR cert auth support for RR")
class ClusterReadReplicaDefaultsSSL(ClusterReadReplicaDefaultsBase):
    use_self_signed = False

    def testit(self):
        self.runit()
