# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS

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
        # TODO need to wait for readiness instead of sleep for replica to be ready
        sleep(5)
        check_sidecar_health(test, ns, replica_pod["metadata"]["name"])

    with mutil.MySQLPodSession(ns, f"{cluster_name}-0", user, password) as s:
        res = s.query_sql(f"select count(*) from mysql_innodb_cluster_metadata.v2_instances where instance_type = 'read-replica' and label like '{cluster_name}-{rr_name}-%'")
        test.assertEqual(res.fetch_one()[0], 1)


class ClusterReadReplicaDefaults(tutil.OperatorTest):
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

    def test_00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
    instances: 0
    podLabels:
      router-label13: "router-label13-val"
      router-label42: "router-label42-val"
    podAnnotations:
      router.mycluster.example.com/ann13: "ann13-value"
      router.mycluster.example.com/ann42: "ann42-value"
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podLabels:
    mycluster-label1: "mycluster-label1-value"
    mycluster-label2: "mycluster-label2-value"
  podAnnotations:
    mycluster.example.com/ann1: "ann1-value"
    mycluster.example.com/ann2: "ann2-value"
  readReplicas:
  - name: trr
    instances: 1
    baseServerId: 500
    podLabels:
      mycluster-rr-label1: "mycluster-label1-value"
      mycluster-rr-label2: "mycluster-label2-value"
    podAnnotations:
      mycluster.example.com/rr-ann1: "ann1-value"
      mycluster.example.com/rr-ann2: "ann2-value"
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.wait_pod("mycluster-trr-0", "Running")

        check_all(self, self.ns, "mycluster", "trr", instances=1)

    def test_01_check_labels_and_annotations(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['mycluster-label1'], 'mycluster-label1-value')
            self.assertEqual(pod['metadata']['labels']['mycluster-label2'], 'mycluster-label2-value')
            self.assertEqual(pod['metadata']['annotations']['mycluster.example.com/ann1'], 'ann1-value')
            self.assertEqual(pod['metadata']['annotations']['mycluster.example.com/ann2'], 'ann2-value')

        replica_pods = kutil.ls_po(self.ns, pattern=f"mycluster-trr-\d")
        pod_names = [replica["NAME"] for replica in replica_pods]
        for pod_name in pod_names:
            pod = kutil.get_po(self.ns, pod_name)
            self.assertEqual(pod['metadata']['labels']['mycluster-rr-label1'], 'mycluster-label1-value')
            self.assertEqual(pod['metadata']['labels']['mycluster-rr-label2'], 'mycluster-label2-value')
            self.assertEqual(pod['metadata']['annotations']['mycluster.example.com/rr-ann1'], 'ann1-value')
            self.assertEqual(pod['metadata']['annotations']['mycluster.example.com/rr-ann2'], 'ann2-value')

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")


