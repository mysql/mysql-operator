# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS


class OrdinaryBackupTimestamp(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    volume_name = "ote-backup-timestamp-vol"
    profile_name = "backup-timestamp-backup"
    dump_name = "backup-timestamp"
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

    def test_0_create(self):
        kutil.create_default_user_secrets(self.ns, name=self.cluster_secret_name)

        backupdir = "/tmp/backups"

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
  backupProfiles:
  - name: {self.profile_name}
    dumpInstance:
      storage:
        persistentVolumeClaim:
          claimName: {self.volume_name}
"""

        kutil.apply(self.ns, yaml)

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, (f"{self.cluster_name}-0", "mysql"), script)

        # create a test volume to store backups
        yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {self.volume_name}
  labels:
    type: local
spec:
  storageClassName: manual
  capacity:
    storage: 2Gi
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: "{backupdir}"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {self.volume_name}
spec:
  storageClassName: manual
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
"""
        kutil.apply(self.ns, yaml)

    def check_mbk(self, dump_name):
        mbk = kutil.get_mbk(self.ns, dump_name)
        status = mbk["status"]
        self.assertTrue(status["output"].startswith(dump_name))
        self.assertGreater(len(status["output"]), len(dump_name))

    def test_1_backup_to_volume(self):
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: {self.cluster_name}
  backupProfileName: {self.profile_name}
  addTimestampToBackupDirectory: true
"""
        kutil.apply(self.ns, yaml)

        # wait for backup to be done
        def check_mbk(l):
            for item in l:
                if item["NAME"] == self.dump_name and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        dump_name = r["NAME"]
        self.assertEqual(r["CLUSTER"], self.cluster_name)
        self.assertEqual(r["STATUS"], "Completed")
        self.assertTrue(r["OUTPUT"].startswith(dump_name))
        self.assertGreater(len(r["OUTPUT"]), len(dump_name))

        self.check_mbk(dump_name)

    def test_9_destroy(self):
        kutil.delete_mbk(self.ns, self.dump_name)

        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_pvc(self.ns, self.volume_name)
        kutil.delete_pv(self.volume_name)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
