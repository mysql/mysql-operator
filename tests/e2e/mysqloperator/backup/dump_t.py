# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
from utils import tutil
from utils import kutil
from utils import mutil
from utils import ociutil
import logging
import unittest
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import COMMON_OPERATOR_ERRORS


class DumpInstance(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    dump_name = "dump-test1"
    oci_dump_name = "dump-test-oci1"
    backup_volume_name = "test-backup-storage"
    oci_storage_prefix = '/e2etest'
    oci_storage_output = None

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_create(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        backupdir = "/tmp/backups"

        bucket = g_ts_cfg.oci_bucket_name

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 2
  secretName: mypwds
  tlsUseSelfSigned: true
  backupProfiles:
  - name: dump
    dumpInstance:
      dumpOptions:
        excludeSchemas: ["excludeme"]
      storage:
        persistentVolumeClaim:
          claimName: {self.backup_volume_name}
  - name: fulldump-oci
    dumpInstance:
      storage:
        ociObjectStorage:
          prefix: {self.oci_storage_prefix}
          bucketName: {bucket or "not-set"}
          credentials: backup-apikey
  - name: snapshot
    snapshot:
      storage:
        persistentVolumeClaim:
          claimName: {self.backup_volume_name}
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")

        self.wait_ic("mycluster", "ONLINE", 2)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, ("mycluster-0", "mysql"), script)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            s.exec_sql("create schema excludeme")
            s.exec_sql("create table excludeme.country like sakila.country")
            s.exec_sql(
                "insert into excludeme.country select * from sakila.country")

        # create a test volume to store backups
        yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {self.backup_volume_name}
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
  name: {self.backup_volume_name}
spec:
  storageClassName: manual
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
"""
        kutil.apply(self.ns, yaml)

    def test_1_backup_to_volume(self):
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: mycluster
  backupProfileName: dump
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
        if r["NAME"] == self.dump_name:
            self.assertEqual(r["CLUSTER"], "mycluster")
            self.assertEqual(r["STATUS"], "Completed")
            self.assertTrue(r["OUTPUT"].startswith(f"{self.dump_name}-"))

        # check status in backup object
        mbk = kutil.get_mbk(self.ns, self.dump_name)
        self.assertTrue(mbk["status"]["startTime"])
        self.assertTrue(mbk["status"]["completionTime"])
        self.assertGreaterEqual(
            mbk["status"]["completionTime"], mbk["status"]["startTime"])
        self.assertEqual(mbk["status"]["status"], "Completed")
        self.assertTrue(mbk["status"]["elapsedTime"])
        self.assertNotEqual(mbk["status"]["spaceAvailable"], "")
        self.assertNotEqual(mbk["status"]["size"], "")
        self.assertEqual(mbk["status"]["method"], "dump-instance/volume")
        # TODO add and check details about the profile used
        # check backup data, ensure that excluded DB is not included etc

    @unittest.skipIf(g_ts_cfg.oci_skip or not g_ts_cfg.oci_config_path or not g_ts_cfg.oci_bucket_name,
      "OCI backup config path and/or bucket name not set")
    def test_1_backup_to_oci_bucket(self):
        # Set this environment variable to the location of the OCI API Key
        # to use for backups to OCI Object Storage
        kutil.create_apikey_secret(self.ns, "backup-apikey", g_ts_cfg.oci_config_path, "BACKUP")

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.oci_dump_name}
spec:
  clusterName: mycluster
  backupProfileName: fulldump-oci
"""
        kutil.apply(self.ns, yaml)

        # wait for backup to be done
        def check_mbk(l):
            for item in l:
                if item["NAME"] == self.oci_dump_name and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        output = r["OUTPUT"]
        self.assertEqual(r["CLUSTER"], "mycluster")
        self.assertEqual(r["STATUS"], "Completed")
        self.assertTrue(output.startswith(f"{self.oci_dump_name}-"))

        if output:
            self.__class__.oci_storage_output = os.path.join(self.oci_storage_prefix, output)

        # check status in backup object
        mbk = kutil.get_mbk(self.ns, self.oci_dump_name)
        self.assertTrue(mbk["status"]["startTime"])
        self.assertTrue(mbk["status"]["completionTime"])
        self.assertGreater(mbk["status"]["completionTime"],
                           mbk["status"]["startTime"])
        self.assertEqual(mbk["status"]["status"], "Completed")
        self.assertTrue(mbk["status"]["elapsedTime"])
        self.assertEqual(mbk["status"]["method"], "dump-instance/oci-bucket")
        self.assertEqual(mbk["status"]["bucket"], g_ts_cfg.oci_bucket_name)
        self.assertTrue(mbk["status"]["ociTenancy"].startswith(
            "ocid1.tenancy.oc1.."))
        # secondary
        self.assertTrue(mbk["status"]["source"].startswith(""))

        # TODO check that the bucket contains the expected files

    def test_2_backup_custom_profile(self):
        pass

    def test_2_backup_added_profile(self):
        pass

    def test_3_delete_backup_keep_data(self):
        pass

    def test_4_delete_backup_oci_delete_data(self):
        pass

    def test_4_delete_backup_volume_delete_data(self):
        pass

    def test_9_destroy(self):
        kutil.delete_ic("clone", "copycluster")
        self.wait_pod_gone("copycluster-0", ns="clone")
        self.wait_ic_gone("copycluster", ns="clone")
        kutil.delete_ns("clone")

        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_mbk(self.ns, self.oci_dump_name)
        kutil.delete_mbk(self.ns, self.dump_name)
        kutil.delete_pvc(self.ns, self.backup_volume_name)
        kutil.delete_pv(self.backup_volume_name)

        kutil.delete_secret(self.ns, "backup-apikey")
        kutil.delete_secret(self.ns, "mypwds")

        if self.__class__.oci_storage_output:
            ociutil.bulk_delete("DELETE", g_ts_cfg.oci_bucket_name, self.__class__.oci_storage_output)



# TODO test that the backup is done using the backup account and fails if it's gone/missing privs but can recover when restored

# TODO invalid profile

# TODO etc


# TODO scheduling
