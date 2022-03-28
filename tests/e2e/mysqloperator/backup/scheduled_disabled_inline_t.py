# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import time
from utils import tutil
from utils import kutil
from utils import dutil
from utils import mutil
import os
import logging
from e2e.mysqloperator.cluster import check_apiobjects
from e2e.mysqloperator.cluster import check_group
from e2e.mysqloperator.cluster import check_adminapi
from e2e.mysqloperator.cluster import check_routing
import unittest
from utils.tutil import g_full_log
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS


class ScheduledBackupDisabledInline(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name = "mycluster"
    disabled_volume_name = "ote-disabled-inline-scheduled-backup-vol"
    disabled_schedule_name = "disabled-schedule-inline"
    disabled_dump_name_prefix = f"{cluster_name}-{disabled_schedule_name}"

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-0")

        super().tearDownClass()

    def create_volume(self):
        backup_dir = "/tmp/scheduled_backups"

        # create a test volume to store scheduled backups
        yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {self.disabled_volume_name}
  labels:
    type: local
spec:
  storageClassName: manual
  capacity:
    storage: 2Gi
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: "{backup_dir}"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {self.disabled_volume_name}
spec:
  storageClassName: manual
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
"""

        kutil.apply(self.ns, yaml)


    def test_0_create(self):
        kutil.create_default_user_secrets(self.ns)

        self.create_volume()

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: 2
  secretName: mypwds
  tlsUseSelfSigned: true
  backupSchedules:
    - name: {self.disabled_schedule_name}
      schedule: "*/1 * 1-31 1-12 *"
      deleteBackupData: false
      enabled: false
      backupProfile:
        dumpInstance:
          storage:
            persistentVolumeClaim:
              claimName: {self.disabled_volume_name}
"""

        kutil.apply(self.ns, yaml)
        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", 1)


    def check_ic(self):
        ic = kutil.get_ic(self.ns, self.cluster_name)
        spec = ic["spec"]

        # backupSchedule
        backupSchedule = spec["backupSchedules"][0]
        self.assertFalse(backupSchedule["deleteBackupData"])
        self.assertFalse(backupSchedule["enabled"])
        self.assertEqual(backupSchedule["name"], self.disabled_schedule_name)
        self.assertEqual(backupSchedule["schedule"], "*/1 * 1-31 1-12 *")

        # backupProfile
        backupProfile = backupSchedule["backupProfile"]
        dumpInstance = backupProfile["dumpInstance"]
        self.assertEqual(dumpInstance["storage"]["persistentVolumeClaim"]["claimName"], self.disabled_volume_name)

    def test_1_dont_backup_to_volume(self):
        # ensure backup is not performed
        for i in range(3):
          time.sleep(60)
          mbks = kutil.ls_mbk(self.ns)
          for mbk in mbks:
              mbkName = mbkName["NAME"]
              if mbkName.startswith(self.disabled_dump_name_prefix) and mbk["STATUS"] == "Completed":
                raise Exception(f"Backup {self.ns}/{mbkName} started despite being disabled")

        mbks = kutil.ls_mbk(self.ns)
        for mbk in mbks:
            mbkName = mbkName["NAME"]
            self.assertFalse(mbkName.startswith(self.disabled_dump_name_prefix))

        self.check_ic()


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pod_gone(f"{self.cluster_name}-0")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_mbks(self.ns, self.disabled_dump_name_prefix)
        kutil.delete_pvc(self.ns, self.disabled_volume_name)
        kutil.delete_pv(self.disabled_volume_name)

        kutil.delete_secret(self.ns, "mypwds")
