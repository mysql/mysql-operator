# Copyright (c) 2022, 2025, Oracle and/or its affiliates.
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


class ScheduledBackupDisabledRef(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    disabled_volume_name = "ote-disabled-scheduled-backup-vol"
    disabled_profile_name = "disabled-ref-scheduled-backup"
    disabled_schedule_name = "disabled-schedule-ref"
    initial_schedule = "*/1 * 1-31 1-12 *"
    new_schedule = "*/15 * 31 12 *"
    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        cls.disabled_dump_name_prefix = f"{cls.cluster_name}-{cls.disabled_schedule_name}"

        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

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
        kutil.create_default_user_secrets(self.ns, name=self.cluster_secret_name)

        self.create_volume()

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
  - name: {self.disabled_profile_name}
    dumpInstance:
      storage:
        persistentVolumeClaim:
          claimName: {self.disabled_volume_name}
  backupSchedules:
    - name: {self.disabled_schedule_name}
      schedule: "{self.initial_schedule}"
      deleteBackupData: true
      backupProfileName: {self.disabled_profile_name}
      enabled: false
"""

        kutil.apply(self.ns, yaml)
        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

    def check_schedule(self, enabled, schedule):
        ic = kutil.get_ic(self.ns, self.cluster_name)
        spec = ic["spec"]

        # backupProfile
        backupProfile = spec["backupProfiles"][0]
        self.assertEqual(backupProfile["name"], self.disabled_profile_name)
        dumpInstance = backupProfile["dumpInstance"]
        self.assertEqual(dumpInstance["storage"]["persistentVolumeClaim"]["claimName"], self.disabled_volume_name)

        # backupSchedule
        backupSchedule = spec["backupSchedules"][0]
        self.assertEqual(backupSchedule["backupProfileName"], self.disabled_profile_name)
        self.assertTrue(backupSchedule["deleteBackupData"])
        self.assertEqual(backupSchedule["enabled"], enabled)
        self.assertEqual(backupSchedule["name"], self.disabled_schedule_name)
        self.assertEqual(backupSchedule["schedule"], schedule)

    def test_2_dont_backup_to_volume(self):
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

        self.check_schedule(enabled = False, schedule = self.initial_schedule)

    def test_4_change_schedule(self):
        patch = [
            {
                "op":"replace",
                "path":"/spec/backupSchedules/0/schedule",
                "value": self.new_schedule
            }
        ]

        kutil.patch_ic(self.ns, self.cluster_name, patch, type="json", data_as_type='json')
        self.check_schedule(enabled = False, schedule = self.new_schedule)

    def test_6_enable_schedule(self):
        patch = [
            {
                "op":"replace",
                "path":"/spec/backupSchedules/0/enabled",
                "value": True
            }
        ]

        kutil.patch_ic(self.ns, self.cluster_name, patch, type="json", data_as_type='json')
        self.check_schedule(enabled = True, schedule = self.new_schedule)

    def test_9_destroy(self):
        kutil.delete_mbks(self.ns, self.disabled_dump_name_prefix)

        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_pvc(self.ns, self.disabled_volume_name)
        kutil.delete_pv(self.disabled_volume_name)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
