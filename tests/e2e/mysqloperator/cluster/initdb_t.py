# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from utils import mutil
from utils import ociutil
import logging
from . import check_routing
import os
import unittest
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS


# TODO check same stuff as check_all() in cluster_t, specially healthness of sidecar
# TODO check if healthchecks and other stuff that rely on accounts work, specially after a clone


class ClusterFromClone(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod("cloned", "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")
        g_full_log.stop_watch("cloned", "mycluster-0")

        super().tearDownClass()

    def test_0_create(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 3
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", 3)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, ["mycluster-0", "mysql"], script)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            s.exec_sql("create user clone@'%' identified by 'clonepass'")
            s.exec_sql("grant backup_admin on *.* to clone@'%'")

    def test_1_create_clone(self):
        # TODO add support for using different root password between clusters
        kutil.create_ns("clone", g_ts_cfg.get_custom_test_ns_labels())
        kutil.create_user_secrets(
            "clone", "pwds", root_user="root", root_host="%", root_pass="sakila")
        kutil.create_user_secrets(
            "clone", "donorpwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: copycluster
spec:
  instances: 1
  router:
    instances: 1
  secretName: pwds
  tlsUseSelfSigned: true
  baseServerId: 2000
  initDB:
    clone:
      donorUrl: root@mycluster-0.mycluster-instances.{self.ns}.svc.cluster.local:3306
      secretKeyRef:
        name: donorpwds
"""

        kutil.apply("clone", yaml)

        self.wait_pod("copycluster-0", "Running", ns="clone")

        self.wait_ic("copycluster", "ONLINE", 1, ns="clone", timeout=300)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            orig_tables = [r[0] for r in s.query_sql(
                "show tables in sakila").fetch_all()]

        with mutil.MySQLPodSession("clone", "copycluster-0", "root", "sakila") as s:
            clone_tables = [r[0] for r in s.query_sql(
                "show tables in sakila").fetch_all()]

            # add some data with binlog disabled to make sure that all members of this
            # cluster are cloned
            s.exec_sql("set autocommit=1")
            s.exec_sql("set session sql_log_bin=0")
            s.exec_sql("create schema unlogged_db")
            s.exec_sql("create table unlogged_db.tbl (a int primary key)")
            s.exec_sql("insert into unlogged_db.tbl values (42)")

        self.assertEqual(set(orig_tables), set(clone_tables))

        # with self.assertRaises(mysqlsh.Error):
        #     with mutil.MySQLPodSession("clone", "copycluster-0", "root", "sakila") as s:
        #         pass

        check_routing.check_pods(self, "clone", "copycluster", 1)

        # TODO also make sure the source field in the ic says clone and not blank

    def test_2_grow(self):
        kutil.patch_ic("clone", "copycluster", {
                       "spec": {"instances": 2}}, type="merge")

        self.wait_pod("copycluster-1", "Running", ns="clone")

        self.wait_ic("copycluster", "ONLINE", 2, ns="clone")

        # check that the new instance was cloned
        with mutil.MySQLPodSession("clone", "copycluster-1", "root", "sakila") as s:
            self.assertEqual(
                str(s.query_sql("select * from unlogged_db.tbl").fetch_all()), str([(42,)]))

    def test_3_routing(self):
        pass  # TODO

    def test_9_destroy(self):
        kutil.delete_ic("clone", "copycluster")
        self.wait_pod_gone("copycluster-1", ns="clone")
        self.wait_pod_gone("copycluster-0", ns="clone")
        self.wait_ic_gone("copycluster", ns="clone")
        kutil.delete_ns("clone")

        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")


# class ClusterFromCloneErrors(tutil.OperatorTest):
#    pass
# TODO test bad params
# TODO check that errors are reported well
# TODO clone not installed in source
# TODO bad version
# TODO regression test for bug where a failed clone doesn't abort the pod

@unittest.skipIf(g_ts_cfg.oci_skip or not g_ts_cfg.oci_config_path or not g_ts_cfg.oci_bucket_name,
  "OCI config path and/or bucket name not set")
class ClusterFromDumpOCI(tutil.OperatorTest):
    """
    Create cluster and initialize from a shell dump stored in an OCI bucket.
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    dump_name = "cluster-from-dump-test-oci1"
    oci_storage_prefix = f"/e2etest/{g_ts_cfg.get_worker_label()}"
    oci_storage_output = None

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_prepare(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        bucket = g_ts_cfg.oci_bucket_name
        config_path = g_ts_cfg.oci_config_path

        # create a secret with the api key to access the bucket, which should be
        # stored in the path given in the environment variable
        kutil.create_apikey_secret(
            self.ns, "restore-apikey", config_path, "RESTORE")
        kutil.create_apikey_secret(
            self.ns, "backup-apikey", config_path, "BACKUP")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
  backupProfiles:
  - name: fulldump-oci
    dumpInstance:
      storage:
        ociObjectStorage:
          prefix: {self.oci_storage_prefix}
          bucketName: {bucket}
          credentials: backup-apikey
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_ic("mycluster", "ONLINE", 1)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, "mycluster-0", script)

        self.__class__.orig_tables = []
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            self.__class__.orig_tables = [r[0]
                                for r in s.query_sql("show tables in sakila").fetch_all()]

        # create a dump in a bucket
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: mycluster
  backupProfileName: fulldump-oci
"""
        kutil.apply(self.ns, yaml)

        # wait for backup to be done
        def check_mbk(l):
            for item in l:
                if item["NAME"] == self.dump_name and item["STATUS"] == "Completed":
                    # can't keep it in self.oci_storage_output because unittest run each function
                    # with a fresh instance
                    # after dump it shall be sth like 'cluster-from-dump-test-oci1-20211027-113626'
                    self.__class__.oci_storage_output = os.path.join(self.oci_storage_prefix, item["OUTPUT"])
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)

        # destroy the test cluster
        kutil.delete_ic(self.ns, "mycluster")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        # delete the pv and pvc for mycluster-0
        kutil.delete_pvc(self.ns, None)
        # TODO ensure the pv was deleted

        kutil.delete_secret(self.ns, "mypwds")

    def test_1_0_create_from_dump(self):
        """
        Create cluster using a shell dump stored in an OCI bucket.
        """
        kutil.create_user_secrets(
            self.ns, "newpwds", root_user="root", root_host="%", root_pass="sakila")

        bucket = g_ts_cfg.oci_bucket_name

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: newcluster
spec:
  instances: 1
  router:
    instances: 1
  secretName: newpwds
  tlsUseSelfSigned: true
  baseServerId: 2000
  initDB:
    dump:
      name: {self.dump_name}
      storage:
        ociObjectStorage:
          prefix: {self.__class__.oci_storage_output}
          bucketName: {bucket}
          credentials: restore-apikey
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("newcluster-0", "Running")

        self.wait_ic("newcluster", "ONLINE", 1, timeout=600)

        with mutil.MySQLPodSession(self.ns, "newcluster-0", "root", "sakila") as s:
            tables = [r[0]
                      for r in s.query_sql("show tables in sakila").fetch_all()]

            self.assertEqual(set(self.__class__.orig_tables), set(tables))

            # TODO: fails with the following error:
            # _mysql_connector.MySQLInterfaceError: Cannot modify @@session.sql_log_bin inside a transaction
            # add some data with binlog disabled to allow testing that new
            # members added to this cluster use clone for provisioning
            # s.exec_sql("set session sql_log_bin=0")
            # s.exec_sql("create schema unlogged_db")
            # s.exec_sql("create table unlogged_db.tbl (a int primary key)")
            # s.exec_sql("insert into unlogged_db.tbl values (42)")
            # s.exec_sql("set session sql_log_bin=1")

        check_routing.check_pods(self, self.ns, "newcluster", 1)

        # TODO also make sure the source field in the ic says clone and not blank

    def test_1_1_grow(self):
        """
        Ensures that a cluster created from a dump can be scaled up properly
        """
        kutil.patch_ic(self.ns, "newcluster", {
                       "spec": {"instances": 2}}, type="merge")

        self.wait_pod("newcluster-1", "Running")

        self.wait_ic("newcluster", "ONLINE", 2)

        # TODO: see comment at line 334 where unlogged_db should be created
        # check that the new instance was provisioned through clone and not incremental
        # with mutil.MySQLPodSession(self.ns, "newcluster-1", "root", "sakila") as s:
        #     self.assertEqual(
        #         str(s.query_sql("select * from unlogged_db.tbl").fetch_all()), str([[42]]))

    def test_1_2_destroy(self):
        kutil.delete_ic(self.ns, "newcluster")

        self.wait_pod_gone("newcluster-0")
        self.wait_ic_gone("newcluster")

        kutil.delete_pvc(self.ns, None)

    def test_2_create_from_dump_options(self):
        """
        Create cluster using a shell dump with additional options passed to the
        load command.
        """

        bucket = g_ts_cfg.oci_bucket_name

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: newcluster
spec:
  instances: 1
  router:
    instances: 1
  secretName: newpwds
  baseServerId: 3000
  tlsUseSelfSigned: true
  initDB:
    dump:
      name: {self.dump_name}
      options:
        includeSchemas:
        - sakila
      storage:
        ociObjectStorage:
          prefix: {self.__class__.oci_storage_output}
          bucketName: {bucket}
          credentials: restore-apikey
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("newcluster-0", "Running")

        self.wait_ic("newcluster", "ONLINE", 1, timeout=600)

        with mutil.MySQLPodSession(self.ns, "newcluster-0", "root", "sakila") as s:
            tables = [r[0]
                      for r in s.query_sql("show tables in sakila").fetch_all()]

            self.assertEqual(set(self.__class__.orig_tables), set(tables))

        check_routing.check_pods(self, self.ns, "newcluster", 1)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_ic(self.ns, "newcluster")

        self.wait_pod_gone("newcluster-0")
        self.wait_ic_gone("newcluster")

        kutil.delete_secret(self.ns, "restore-apikey")
        kutil.delete_secret(self.ns, "backup-apikey")

        kutil.delete_pvc(self.ns, None)

        if self.__class__.oci_storage_output:
            ociutil.bulk_delete("DELETE", g_ts_cfg.oci_bucket_name, self.__class__.oci_storage_output)


@unittest.skipIf(g_ts_cfg.azure_skip or not g_ts_cfg.azure_config_file or not g_ts_cfg.azure_container_name,
  "Azure config file and/or container name not set")
class ClusterFromDumpAzure(tutil.OperatorTest):
    """
    Create cluster and initialize from a shell dump stored in an Azure container.
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    dump_name = "cluster-from-dump-test-azure1"
    azure_storage_prefix = f"/e2etest/{g_ts_cfg.get_worker_label()}"
    azure_storage_output = None

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_prepare(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        container = g_ts_cfg.azure_container_name
        config_file = g_ts_cfg.azure_config_file

        # create a secret with the api key to access the container
        kutil.create_secret_from_files(self.ns, "azure-backup", [["config", config_file]])

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
  backupProfiles:
  - name: fulldump-azure
    dumpInstance:
      storage:
        azure:
          prefix: {self.azure_storage_prefix}
          containerName: {container}
          config: azure-backup
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_ic("mycluster", "ONLINE", 1)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, "mycluster-0", script)

        self.__class__.orig_tables = []
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            self.__class__.orig_tables = [r[0]
                                for r in s.query_sql("show tables in sakila").fetch_all()]

        # create a dump in a Azure BLOB container
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: mycluster
  backupProfileName: fulldump-azure
"""
        kutil.apply(self.ns, yaml)

        # wait for backup to be done
        def check_mbk(l):
            for item in l:
                if item["NAME"] == self.dump_name and item["STATUS"] == "Completed":
                    # can't keep it in self.oci_storage_output because unittest run each function
                    # with a fresh instance
                    # after dump it shall be sth like 'cluster-from-dump-test-oci1-20211027-113626'
                    self.__class__.azure_storage_output = os.path.join(self.azure_storage_prefix, item["OUTPUT"])
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=300)

        # destroy the test cluster
        kutil.delete_ic(self.ns, "mycluster")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        # delete the pv and pvc for mycluster-0
        kutil.delete_pvc(self.ns, None)
        # TODO ensure the pv was deleted

        kutil.delete_secret(self.ns, "mypwds")

    def test_1_0_create_from_dump(self):
        """
        Create cluster using a shell dump stored in an Azure BLOB container.
        """
        kutil.create_user_secrets(
            self.ns, "newpwds", root_user="root", root_host="%", root_pass="sakila")

        container = g_ts_cfg.azure_container_name

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: newcluster
spec:
  instances: 1
  router:
    instances: 1
  secretName: newpwds
  tlsUseSelfSigned: true
  baseServerId: 2000
  initDB:
    dump:
      name: {self.dump_name}
      storage:
        azure:
          prefix: {self.__class__.azure_storage_output}
          containerName: {container}
          config: azure-backup
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("newcluster-0", "Running")

        self.wait_ic("newcluster", "ONLINE", 1, timeout=600)

        with mutil.MySQLPodSession(self.ns, "newcluster-0", "root", "sakila") as s:
            tables = [r[0]
                      for r in s.query_sql("show tables in sakila").fetch_all()]

            self.assertEqual(set(self.__class__.orig_tables), set(tables))

            # TODO: fails with the following error:
            # _mysql_connector.MySQLInterfaceError: Cannot modify @@session.sql_log_bin inside a transaction
            # add some data with binlog disabled to allow testing that new
            # members added to this cluster use clone for provisioning
            # s.exec_sql("set session sql_log_bin=0")
            # s.exec_sql("create schema unlogged_db")
            # s.exec_sql("create table unlogged_db.tbl (a int primary key)")
            # s.exec_sql("insert into unlogged_db.tbl values (42)")
            # s.exec_sql("set session sql_log_bin=1")

        check_routing.check_pods(self, self.ns, "newcluster", 1)

        # TODO also make sure the source field in the ic says clone and not blank

    def test_1_1_grow(self):
        """
        Ensures that a cluster created from a dump can be scaled up properly
        """
        kutil.patch_ic(self.ns, "newcluster", {
                       "spec": {"instances": 2}}, type="merge")

        self.wait_pod("newcluster-1", "Running")

        self.wait_ic("newcluster", "ONLINE", 2)

        # TODO: see comment at line 334 where unlogged_db should be created
        # check that the new instance was provisioned through clone and not incremental
        # with mutil.MySQLPodSession(self.ns, "newcluster-1", "root", "sakila") as s:
        #     self.assertEqual(
        #         str(s.query_sql("select * from unlogged_db.tbl").fetch_all()), str([[42]]))

    def test_1_2_destroy(self):
        kutil.delete_ic(self.ns, "newcluster")

        self.wait_pod_gone("newcluster-0")
        self.wait_ic_gone("newcluster")

        kutil.delete_pvc(self.ns, None)

    def test_2_create_from_dump_options(self):
        """
        Create cluster using a shell dump with additional options passed to the
        load command.
        """

        container = g_ts_cfg.azure_container_name

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: newcluster
spec:
  instances: 1
  router:
    instances: 1
  secretName: newpwds
  baseServerId: 3000
  tlsUseSelfSigned: true
  initDB:
    dump:
      name: {self.dump_name}
      options:
        includeSchemas:
        - sakila
      storage:
        azure:
          prefix: {self.__class__.azure_storage_output}
          containerName: {container}
          config: azure-backup
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("newcluster-0", "Running")

        self.wait_ic("newcluster", "ONLINE", 1, timeout=600)

        with mutil.MySQLPodSession(self.ns, "newcluster-0", "root", "sakila") as s:
            tables = [r[0]
                      for r in s.query_sql("show tables in sakila").fetch_all()]

            self.assertEqual(set(self.__class__.orig_tables), set(tables))

        check_routing.check_pods(self, self.ns, "newcluster", 1)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_ic(self.ns, "newcluster")

        self.wait_pod_gone("newcluster-0")
        self.wait_ic_gone("newcluster")

        kutil.delete_secret(self.ns, "azure-backup")

        kutil.delete_pvc(self.ns, None)


# class ClusterFromDumpLocal(tutil.OperatorTest):
#    pass


class ClusterFromDumpErrors(tutil.OperatorTest):
    pass
