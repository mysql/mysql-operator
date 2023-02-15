# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

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
from setup.config import g_ts_cfg
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS


class ScheduledBackupRef(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name = "mycluster"
    volume_name = "ote-scheduled-ref-backup-vol"
    profile_name = "scheduled-ref-test-backup"
    schedule_name = "schedule-ref"
    dump_name_prefix = f"{cluster_name}-{schedule_name}"
    exclude_schema = "countries"

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-0")
        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-1")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-1")
        g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-0")

        super().tearDownClass()

    def create_volume(self):
        backup_dir = "/tmp/scheduled_backups"

        # create a test volume to store scheduled backups
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
    path: "{backup_dir}"
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
  backupProfiles:
  - name: {self.profile_name}
    dumpInstance:
      dumpOptions:
        excludeTables: ["{self.exclude_schema}.country"]
      storage:
        persistentVolumeClaim:
          claimName: {self.volume_name}
    podAnnotations:
      backupProfileAnn1: ann1-{self.profile_name}
      backupProfileAnn2: ann2-{self.profile_name}
    podLabels:
      backupProfileLabel1: label1-{self.profile_name}
      backupProfileLabel2: label2-{self.profile_name}
  backupSchedules:
    - name: {self.schedule_name}
      schedule: "*/1 0-23 * * *"
      deleteBackupData: false
      backupProfileName: {self.profile_name}
      enabled: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_pod(f"{self.cluster_name}-1", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", 2)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, (f"{self.cluster_name}-0", "mysql"), script)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql(f"create schema {self.exclude_schema}")
            s.exec_sql(f"create table {self.exclude_schema}.country like sakila.country")
            s.exec_sql(f"insert into {self.exclude_schema}.country select * from sakila.country")


    def check_ic(self):
        ic = kutil.get_ic(self.ns, self.cluster_name)
        spec = ic["spec"]

        # backupProfile
        backupProfile = spec["backupProfiles"][0]
        dumpInstance = backupProfile["dumpInstance"]
        self.assertEqual(dumpInstance["dumpOptions"]["excludeTables"][0], f"{self.exclude_schema}.country")
        self.assertEqual(dumpInstance["storage"]["persistentVolumeClaim"]["claimName"], self.volume_name)
        self.assertEqual(backupProfile["name"], self.profile_name)

        self.assertTrue("podAnnotations" in backupProfile)
        self.assertTrue("backupProfileAnn1" in backupProfile["podAnnotations"])
        self.assertTrue("backupProfileAnn2" in backupProfile["podAnnotations"])
        self.assertEqual(len(backupProfile["podAnnotations"]), 2)
        self.assertEqual(backupProfile["podAnnotations"]["backupProfileAnn1"], f"ann1-{self.profile_name}")
        self.assertEqual(backupProfile["podAnnotations"]["backupProfileAnn2"], f"ann2-{self.profile_name}")
        self.assertTrue("podLabels" in backupProfile)
        self.assertTrue("backupProfileLabel1" in backupProfile["podLabels"])
        self.assertTrue("backupProfileLabel2" in backupProfile["podLabels"])
        self.assertEqual(len(backupProfile["podLabels"]), 2)
        self.assertEqual(backupProfile["podLabels"]["backupProfileLabel1"], f"label1-{self.profile_name}")
        self.assertEqual(backupProfile["podLabels"]["backupProfileLabel2"], f"label2-{self.profile_name}")

        # backupSchedule
        backupSchedule = spec["backupSchedules"][0]
        self.assertEqual(backupSchedule["backupProfileName"], self.profile_name)
        self.assertFalse(backupSchedule["deleteBackupData"])
        self.assertTrue(backupSchedule["enabled"])
        self.assertEqual(backupSchedule["name"], self.schedule_name)
        self.assertEqual(backupSchedule["schedule"], "*/1 0-23 * * *")

    def check_mbk(self, dump_name):
        mbk = kutil.get_mbk(self.ns, dump_name)

        # spec
        spec = mbk["spec"]
        self.assertEqual(spec["backupProfileName"], self.profile_name)
        self.assertEqual(spec["clusterName"], self.cluster_name)
        self.assertFalse(spec["deleteBackupData"])

        # status
        status = mbk["status"]
        self.assertIsNotNone(status["startTime"])
        self.assertIsNotNone(status["completionTime"])
        self.assertGreaterEqual(status["completionTime"], status["startTime"])
        self.assertIsNotNone(status["elapsedTime"])
        self.assertEqual(status["status"], "Completed")
        self.assertNotEqual(status["spaceAvailable"], "")
        self.assertNotEqual(status["size"], "")
        self.assertEqual(status["method"], "dump-instance/volume")
        self.assertTrue(status["output"].startswith(self.dump_name_prefix))
        self.assertGreater(len(status["output"]), len(self.dump_name_prefix))

    def check_backup_pods(self):
        pods = kutil.ls_po(self.ns, pattern=f"{self.dump_name_prefix}.*")
        for pod_name in [pod["NAME"] for pod in pods]:
            # keep this to quickly test "create backup pods"
            if pod_name.startswith(f"{self.dump_name_prefix}-cb"):
                # this is the create mysqlbackup object pod and not the actual backup pod itself
                pass

            pod = kutil.get_po(self.ns, pod_name)

            self.assertTrue("annotations" in pod["metadata"])
            self.assertTrue("backupProfileAnn1" in pod["metadata"]["annotations"])
            self.assertTrue("backupProfileAnn2" in pod["metadata"]["annotations"])
            self.assertEqual(pod["metadata"]["annotations"]["backupProfileAnn1"], f"ann1-{self.profile_name}")
            self.assertEqual(pod["metadata"]["annotations"]["backupProfileAnn2"], f"ann2-{self.profile_name}")
            self.assertTrue("labels" in pod["metadata"])
            self.assertTrue("backupProfileLabel1" in pod["metadata"]["labels"])
            self.assertTrue("backupProfileLabel2" in pod["metadata"]["labels"])
            self.assertEqual(pod["metadata"]["labels"]["backupProfileLabel1"], f"label1-{self.profile_name}")
            self.assertEqual(pod["metadata"]["labels"]["backupProfileLabel2"], f"label2-{self.profile_name}")

    def test_1_backup_to_volume(self):
        # wait until backup is completed
        def check_mbk(l):
            for item in l:
                if item["NAME"].startswith(self.dump_name_prefix) and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        self.assertTrue(r["NAME"].startswith(self.dump_name_prefix))
        self.assertEqual(r["CLUSTER"], self.cluster_name)
        self.assertEqual(r["STATUS"], "Completed")
        self.assertGreater(len(r["OUTPUT"]), len(self.dump_name_prefix))

        self.check_ic()
        self.check_mbk(r["NAME"])
        self.check_backup_pods()

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pod_gone(f"{self.cluster_name}-1")
        self.wait_pod_gone(f"{self.cluster_name}-0")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_mbks(self.ns, self.dump_name_prefix)
        kutil.delete_pvc(self.ns, self.volume_name)
        kutil.delete_pv(self.volume_name)

        kutil.delete_secret(self.ns, "mypwds")
