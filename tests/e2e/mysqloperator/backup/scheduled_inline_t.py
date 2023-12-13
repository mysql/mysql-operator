# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS


class ScheduledBackupInline(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name = "mycluster"
    volume_name = "ote-scheduled-backup-vol"
    schedule_name = "inlined-schedule"
    scheduled_dump_prefix = f"{cluster_name}-{schedule_name}"
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
  backupSchedules:
    - name: {self.schedule_name}
      schedule: "*/1 0-23 * * *"
      timeZone: Antarctica/Davis
      deleteBackupData: false
      enabled: true
      backupProfile:
        dumpInstance:
          dumpOptions:
            excludeSchemas: ["{self.exclude_schema}"]
          storage:
            persistentVolumeClaim:
              claimName: {self.volume_name}
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
            s.exec_sql(
                f"insert into {self.exclude_schema}.country select * from sakila.country")


    def check_ic(self):
        ic = kutil.get_ic(self.ns, self.cluster_name)
        spec = ic["spec"]

        # backupSchedule
        backupSchedule = spec["backupSchedules"][0]
        self.assertFalse(backupSchedule["deleteBackupData"])
        self.assertTrue(backupSchedule["enabled"])
        self.assertEqual(backupSchedule["name"], self.schedule_name)
        self.assertEqual(backupSchedule["schedule"], "*/1 0-23 * * *")

        # backupProfile
        backupProfile = backupSchedule["backupProfile"]
        dumpInstance = backupProfile["dumpInstance"]
        self.assertEqual(dumpInstance["dumpOptions"]["excludeSchemas"][0], self.exclude_schema)
        self.assertEqual(dumpInstance["storage"]["persistentVolumeClaim"]["claimName"], self.volume_name)


    def check_mbk(self, dump_name):
        mbk = kutil.get_mbk(self.ns, dump_name)

        # spec
        spec = mbk["spec"]
        self.assertEqual(spec["clusterName"], self.cluster_name)
        self.assertFalse(spec["deleteBackupData"])
        backupProfile = spec["backupProfile"]
        dumpInstance = backupProfile["dumpInstance"]
        self.assertEqual(dumpInstance["dumpOptions"]["excludeSchemas"][0], self.exclude_schema)
        self.assertEqual(dumpInstance["storage"]["persistentVolumeClaim"]["claimName"], self.volume_name)

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
        self.assertTrue(status["output"].startswith(self.scheduled_dump_prefix))
        self.assertGreater(len(status["output"]), len(self.scheduled_dump_prefix))

    def check_cj(self):
        cj = kutil.get_cj(self.ns, f"{self.cluster_name}-{self.schedule_name}-cb")

        spec = cj["spec"]
        self.assertEqual(spec["schedule"], "*/1 0-23 * * *")
        if kutil.server_version() >= '1.27':
            # TimeZone support was added with Kubernetes 1.27 older versions
            # ignore it
            self.assertEqual(spec["timeZone"], "Antarctica/Davis")

    def test_1_backup_to_volume(self):
        # wait until backup is completed
        def check_mbk(l):
            for item in l:
                if item["OUTPUT"].startswith(self.scheduled_dump_prefix) and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        self.assertTrue(r["NAME"].startswith(self.scheduled_dump_prefix))
        self.assertEqual(r["CLUSTER"], self.cluster_name)
        self.assertEqual(r["STATUS"], "Completed")
        self.assertTrue(r["OUTPUT"].startswith(self.scheduled_dump_prefix))
        self.assertGreater(len(r["OUTPUT"]), len(self.scheduled_dump_prefix))

        self.check_ic()
        self.check_mbk(r["NAME"])
        self.check_cj()

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pod_gone(f"{self.cluster_name}-1")
        self.wait_pod_gone(f"{self.cluster_name}-0")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_mbks(self.ns, self.scheduled_dump_prefix)
        kutil.delete_pvc(self.ns, self.volume_name)
        kutil.delete_pv(self.volume_name)

        kutil.delete_secret(self.ns, "mypwds")
