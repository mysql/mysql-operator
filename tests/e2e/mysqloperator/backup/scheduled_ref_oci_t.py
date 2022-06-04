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
from e2e.mysqloperator.cluster import check_apiobjects
from e2e.mysqloperator.cluster import check_group
from e2e.mysqloperator.cluster import check_adminapi
from e2e.mysqloperator.cluster import check_routing
import unittest
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS


@unittest.skipIf(g_ts_cfg.oci_skip or not g_ts_cfg.oci_config_path or not g_ts_cfg.oci_bucket_name,
  "OCI scheduled backup config path and/or bucket name not set")
class ScheduledBackupRefOci(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_name = "mycluster"
    profile_name = "scheduled-test-backup-oci"
    schedule_name = "schedule-ref-oci"
    dump_name_prefix = f"{cluster_name}-{schedule_name}"
    backup_apikey = "backup-apikey"
    oci_storage_prefix = f"/e2etest/{g_ts_cfg.get_worker_label()}"
    oci_storage_output = None

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-0")
        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-1")
        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-2")
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
  instances: 3
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  backupProfiles:
  - name: {self.profile_name}
    dumpInstance:
      storage:
        ociObjectStorage:
          prefix: {self.oci_storage_prefix}
          bucketName: {g_ts_cfg.oci_bucket_name}
          credentials: {self.backup_apikey}
  backupSchedules:
    - name: {self.schedule_name}
      schedule: "*/1 0-23 * 1-12 *"
      deleteBackupData: true
      backupProfileName: {self.profile_name}
      enabled: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_pod(f"{self.cluster_name}-1", "Running")
        self.wait_pod(f"{self.cluster_name}-2", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", 3)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, (f"{self.cluster_name}-0", "mysql"), script)


    def check_ic(self):
        ic = kutil.get_ic(self.ns, self.cluster_name)
        spec = ic["spec"]

        # backupProfile
        backupProfile = spec["backupProfiles"][0]
        self.assertEqual(backupProfile["name"], self.profile_name)
        ociObjectStorage = backupProfile["dumpInstance"]["storage"]["ociObjectStorage"]
        self.assertEqual(ociObjectStorage["prefix"], self.oci_storage_prefix)
        self.assertEqual(ociObjectStorage["bucketName"], g_ts_cfg.oci_bucket_name)
        self.assertEqual(ociObjectStorage["credentials"], self.backup_apikey)

        # backupSchedule
        backupSchedule = spec["backupSchedules"][0]
        self.assertEqual(backupSchedule["backupProfileName"], self.profile_name)
        self.assertTrue(backupSchedule["deleteBackupData"])
        self.assertTrue(backupSchedule["enabled"])
        self.assertEqual(backupSchedule["name"], self.schedule_name)
        self.assertEqual(backupSchedule["schedule"], "*/1 0-23 * 1-12 *")

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
        self.assertEqual(status["method"], "dump-instance/oci-bucket")
        self.assertIsNotNone(status["ociTenancy"])
        self.assertEqual(status["bucket"], g_ts_cfg.oci_bucket_name)
        self.assertTrue(status["output"].startswith(self.dump_name_prefix))
        self.assertGreater(len(status["output"]), len(self.dump_name_prefix))

    def test_1_backup_to_oci_bucket(self):
        # wait until backup is completed
        def check_mbk(l):
            for item in l:
                if item["NAME"].startswith(self.dump_name_prefix) and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        output = r["OUTPUT"]
        self.assertTrue(r["NAME"].startswith(self.dump_name_prefix))
        self.assertEqual(r["CLUSTER"], self.cluster_name)
        self.assertEqual(r["STATUS"], "Completed")
        self.assertTrue(output.startswith(self.dump_name_prefix))
        self.assertGreater(len(output), len(self.dump_name_prefix))

        self.check_ic()
        self.check_mbk(r["NAME"])

        if output:
            self.__class__.oci_storage_output = os.path.join(self.oci_storage_prefix, output)


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pod_gone(f"{self.cluster_name}-2")
        self.wait_pod_gone(f"{self.cluster_name}-1")
        self.wait_pod_gone(f"{self.cluster_name}-0")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_mbks(self.ns, self.dump_name_prefix)

        kutil.delete_secret(self.ns, "mypwds")

        if self.__class__.oci_storage_output:
            ociutil.bulk_delete("DELETE", g_ts_cfg.oci_bucket_name, self.__class__.oci_storage_output)
