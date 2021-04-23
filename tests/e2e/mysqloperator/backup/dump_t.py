# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

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
import mysqlsh
import unittest
from utils.tutil import g_full_log
from mysqloperator.controller.utils import b64encode
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS


class DumpInstance(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

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

        backup_volume_name = "test-backup-storage"
        backupdir = "/tmp/backups"

        bucket = os.getenv("OPERATOR_TEST_BACKUP_OCI_BUCKET")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2alpha1
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 2
  secretName: mypwds
  backupProfiles:
  - name: dump
    dumpInstance:
      dumpOptions:
        excludeSchemas: ["excludeme"]
      storage:
        persistentVolumeClaim:
          claimName: {backup_volume_name}
  - name: fulldump-oci
    dumpInstance:
      storage:
        ociObjectStorage:
          bucketName: {bucket or "not-set"}
          credentials: backup-apikey
  - name: snapshot
    snapshot:
      storage:
        persistentVolumeClaim:
          claimName: {backup_volume_name}
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")

        self.wait_ic("mycluster", "ONLINE", 2)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, ("mycluster-0", "mysql"), script)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            s.run_sql("create schema excludeme")
            s.run_sql("create table excludeme.country like sakila.country")
            s.run_sql(
                "insert into excludeme.country select * from sakila.country")

        # create a test volume to store backups
        yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {backup_volume_name}
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
  name: {backup_volume_name}
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
        yaml = """
apiVersion: mysql.oracle.com/v2alpha1
kind: MySQLBackup
metadata:
  name: dump-test1
spec:
  clusterName: mycluster
  backupProfileName: dump
"""
        kutil.apply(self.ns, yaml)

        # wait for backup to be done
        def check_mbk(l):
            for item in l:
                if item["NAME"] == "dump-test1" and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        if r["NAME"] == "dump-test1":
            self.assertEqual(r["CLUSTER"], "mycluster")
            self.assertEqual(r["STATUS"], "Completed")
            self.assertTrue(r["OUTPUT"].startswith("dump-test1-"))

        # check status in backup object
        mbk = kutil.get_mbk(self.ns, "dump-test1")
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

    @unittest.skipIf(not os.getenv("OPERATOR_TEST_BACKUP_OCI_APIKEY_PATH") or os.getenv("OPERATOR_TEST_BACKUP_OCI_BUCKET"), "OPERATOR_TEST_BACKUP_OCI_APIKEY_PATH and/or OPERATOR_TEST_BACKUP_OCI_BUCKET not set")
    def test_1_backup_to_oci_bucket(self):
        # Set this environment variable to the location of the OCI API Key
        # to use for backups to OCI Object Storage
        apikey_path = os.getenv("OPERATOR_TEST_BACKUP_OCI_APIKEY_PATH")
        if apikey_path:
            kutil.create_apikey_secret(self.ns, "backup-apikey", apikey_path)

        yaml = """
apiVersion: mysql.oracle.com/v2alpha1
kind: MySQLBackup
metadata:
  name: dump-test-oci1
spec:
  clusterName: mycluster
  backupProfileName: fulldump-oci
"""
        kutil.apply(self.ns, yaml)

        # wait for backup to be done
        def check_mbk(l):
            for item in l:
                if item["NAME"] == "dump-test-oci1" and item["STATUS"] == "Completed":
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)
        if r["NAME"] == "dump-test-oci1":
            self.assertEqual(r["CLUSTER"], "mycluster")
            self.assertEqual(r["STATUS"], "Completed")
            self.assertTrue(r["OUTPUT"].startswith("dump-test-oci1-"))

        # check status in backup object
        mbk = kutil.get_mbk(self.ns, "dump-test-oci1")
        self.assertTrue(mbk["status"]["startTime"])
        self.assertTrue(mbk["status"]["completionTime"])
        self.assertGreater(mbk["status"]["completionTime"],
                           mbk["status"]["startTime"])
        self.assertEqual(mbk["status"]["status"], "Completed")
        self.assertTrue(mbk["status"]["elapsedTime"])
        self.assertEqual(mbk["status"]["method"], "dump-instance/oci-bucket")
        self.assertEqual(mbk["status"]["bucket"], "dumps")
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

# TODO test that the backup is done using the backup account and fails if it's gone/missing privs but can recover when restored

# TODO invalid profile

# TODO etc


# TODO scheduling
