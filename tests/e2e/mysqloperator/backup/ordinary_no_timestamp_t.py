# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS


class OrdinaryBackupNoTimestamp(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name = "mycluster"
    volume_name = "ote-backup-no-timestamp-vol"
    profile_name = "backup-no-timestamp-backup"
    dump_name = "backup-no-timestamp"

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-0")

        super().tearDownClass()

    def test_0_create(self):
        kutil.create_default_user_secrets(self.ns)

        backupdir = "/tmp/backups"

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: 1
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  backupProfiles:
  - name: {self.profile_name}
    dumpInstance:
      storage:
        persistentVolumeClaim:
          claimName: {self.volume_name}
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", 1)

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
        self.assertEqual(status["output"], dump_name)

    def test_1_backup_to_volume(self):
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: {self.cluster_name}
  backupProfileName: {self.profile_name}
  addTimestampToBackupDirectory: false
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
        self.assertEqual(r["OUTPUT"], dump_name)

        self.check_mbk(dump_name)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pod_gone(f"{self.cluster_name}-0")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_mbk(self.ns, self.dump_name)
        kutil.delete_pvc(self.ns, self.volume_name)
        kutil.delete_pv(self.volume_name)

        kutil.delete_secret(self.ns, "mypwds")
