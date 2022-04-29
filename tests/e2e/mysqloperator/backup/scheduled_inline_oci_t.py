# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
from utils import ociutil
import os
import logging
import unittest
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import COMMON_OPERATOR_ERRORS


@unittest.skipIf(g_ts_cfg.oci_skip or not g_ts_cfg.oci_config_path or not g_ts_cfg.oci_bucket_name,
  "OCI scheduled backup config path and/or bucket name not set")
class ScheduledBackupInlineOci(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name = "mycluster"
    schedule_name = "inlined-schedule-oci"
    scheduled_dump_prefix = f"{cluster_name}-{schedule_name}"
    exclude_schema = "countries"
    backup_apikey = "backup-apikey"
    oci_storage_prefix = f"/e2etest/{g_ts_cfg.get_worker_label()}"
    oci_storage_output = None

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

    def test_0_create(self):
        kutil.create_default_user_secrets(self.ns)

        kutil.create_apikey_secret(self.ns, self.backup_apikey, g_ts_cfg.oci_config_path, "BACKUP")

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
      deleteBackupData: false
      enabled: true
      backupProfile:
        dumpInstance:
          dumpOptions:
            excludeSchemas: ["{self.exclude_schema}"]
          storage:
            ociObjectStorage:
              prefix: {self.oci_storage_prefix}
              bucketName: {g_ts_cfg.oci_bucket_name}
              credentials: {self.backup_apikey}
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
        ociObjectStorage = dumpInstance["storage"]["ociObjectStorage"]
        self.assertEqual(ociObjectStorage["prefix"], self.oci_storage_prefix)
        self.assertEqual(ociObjectStorage["bucketName"], g_ts_cfg.oci_bucket_name)
        self.assertEqual(ociObjectStorage["credentials"], self.backup_apikey)

    def check_mbk(self, dump_name):
        mbk = kutil.get_mbk(self.ns, dump_name)

        # spec
        spec = mbk["spec"]
        self.assertEqual(spec["clusterName"], self.cluster_name)
        self.assertFalse(spec["deleteBackupData"])
        backupProfile = spec["backupProfile"]
        dumpInstance = backupProfile["dumpInstance"]
        self.assertEqual(dumpInstance["dumpOptions"]["excludeSchemas"][0], self.exclude_schema)
        ociObjectStorage = dumpInstance["storage"]["ociObjectStorage"]
        self.assertEqual(ociObjectStorage["prefix"], self.oci_storage_prefix)
        self.assertEqual(ociObjectStorage["bucketName"], g_ts_cfg.oci_bucket_name)
        self.assertEqual(ociObjectStorage["credentials"], self.backup_apikey)

        # status
        status = mbk["status"]
        self.assertIsNotNone(status["startTime"])
        self.assertIsNotNone(status["completionTime"])
        self.assertGreaterEqual(status["completionTime"], status["startTime"])
        self.assertIsNotNone(status["elapsedTime"])
        self.assertEqual(status["status"], "Completed")
        self.assertEqual(status["method"], "dump-instance/oci-bucket")
        self.assertIsNotNone(status["ociTenancy"])
        self.assertEqual(status["bucket"], g_ts_cfg.oci_bucket_name)
        self.assertTrue(status["output"].startswith(self.scheduled_dump_prefix))
        self.assertGreater(len(status["output"]), len(self.scheduled_dump_prefix))

    def test_1_backup_to_oci_bucket(self):
        # wait until backup is completed
        def check_mbk(l):
            for item in l:
                if item["OUTPUT"].startswith(self.scheduled_dump_prefix) and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        dump_name = r["NAME"]
        output = r["OUTPUT"]
        self.assertTrue(dump_name.startswith(self.scheduled_dump_prefix))
        self.assertEqual(r["CLUSTER"], self.cluster_name)
        self.assertEqual(r["STATUS"], "Completed")
        self.assertGreater(len(output), len(self.scheduled_dump_prefix))

        self.check_ic()
        self.check_mbk(dump_name)

        if output:
            self.__class__.oci_storage_output = os.path.join(self.oci_storage_prefix, output)


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pod_gone(f"{self.cluster_name}-1")
        self.wait_pod_gone(f"{self.cluster_name}-0")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_mbks(self.ns, self.scheduled_dump_prefix)

        kutil.delete_secret(self.ns, "mypwds")

        if self.__class__.oci_storage_output:
            ociutil.bulk_delete("DELETE", g_ts_cfg.oci_bucket_name, self.__class__.oci_storage_output)
