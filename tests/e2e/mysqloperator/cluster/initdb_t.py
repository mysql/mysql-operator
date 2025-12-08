# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
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
    copycluster_startinstances = 1
    copycluster_wantedinstances = 2

    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.copycluster_name = f"copy-{cls.cluster_name}"
        cls.cloned_cluster_ns = f"clone-{cls.random_suffix}"

        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.copycluster_wantedinstances):
            g_full_log.watch_mysql_pod(cls.cloned_cluster_ns, f"{cls.copycluster_name}-{instance}")

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        for instance in reversed(range(0, cls.copycluster_wantedinstances)):
            g_full_log.stop_watch(cls.cloned_cluster_ns, f"{cls.copycluster_name}-{instance}")

        super().tearDownClass()

    def test_0_create(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
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
 """

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, [f"{self.cluster_name}-0", "mysql"], script)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            s.exec_sql("create user clone@'%' identified by 'clonepass'")
            s.exec_sql("grant backup_admin on *.* to clone@'%'")

    def test_1_create_clone(self):
        # TODO add support for using different root password between clusters
        kutil.create_ns(self.cloned_cluster_ns, g_ts_cfg.get_custom_test_ns_labels())
        kutil.create_user_secrets(self.cloned_cluster_ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")
        kutil.create_user_secrets(self.cloned_cluster_ns, "donorpwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.copycluster_name}
spec:
  instances: {self.copycluster_startinstances}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  baseServerId: 2000
  initDB:
    clone:
      donorUrl: root@{self.cluster_name}-0.{self.cluster_name}-instances.{self.ns}.svc.cluster.local:3306
      secretKeyRef:
        name: donorpwds
"""

        kutil.apply(self.cloned_cluster_ns, yaml)

        for instance in range(0, self.copycluster_startinstances):
            self.wait_pod(f"{self.copycluster_name}-{instance}", "Running", ns=self.cloned_cluster_ns)

        if self.routers_count:
            self.wait_routers(f"{self.copycluster_name}-router-*", self.routers_count, ns=self.cloned_cluster_ns, timeout=self.cluster_size*120)
        self.wait_ic(self.copycluster_name, "ONLINE", num_online=self.copycluster_startinstances, ns=self.cloned_cluster_ns, timeout=300)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            orig_tables = [r[0] for r in s.query_sql(
                "show tables in sakila").fetch_all()]

        with mutil.MySQLPodSession(self.cloned_cluster_ns, f"{self.copycluster_name}-0", "root", "sakila") as s:
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

        check_routing.check_pods(self, self.cloned_cluster_ns, self.copycluster_name, num_pods=self.routers_count)

        # TODO also make sure the source field in the ic says clone and not blank

    def test_2_grow(self):
        kutil.patch_ic(self.cloned_cluster_ns, self.copycluster_name, {
                       "spec": {"instances": self.copycluster_wantedinstances}}, type="merge")

        for instance in range(1, self.copycluster_wantedinstances):
            self.wait_pod(f"{self.copycluster_name}-{instance}", "Running", ns=self.cloned_cluster_ns)

        self.wait_ic(self.copycluster_name, "ONLINE", self.copycluster_wantedinstances, ns=self.cloned_cluster_ns)

        # check that the new instance was cloned
        with mutil.MySQLPodSession(self.cloned_cluster_ns, f"{self.copycluster_name}-1", "root", "sakila") as s:
            self.assertEqual(
                str(s.query_sql("select * from unlogged_db.tbl").fetch_all()), str([(42,)]))

    def test_3_routing(self):
        pass  # TODO

    def test_9_destroy(self):
        kutil.delete_ic(self.cloned_cluster_ns, self.copycluster_name)

        self.wait_pods_gone(f"{self.copycluster_name}-*", ns=self.cloned_cluster_ns)
        self.wait_routers_gone(f"{self.copycluster_name}-router-*", ns=self.cloned_cluster_ns)
        self.wait_ic_gone(self.copycluster_name, ns=self.cloned_cluster_ns)
        kutil.delete_ns(self.cloned_cluster_ns)
        kutil.delete_secret(self.cloned_cluster_ns, self.cluster_secret_name)
        kutil.delete_secret(self.cloned_cluster_ns, "donorpwds")

        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


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

    _cluster_size = 1
    _routers_count = 0
    newcluster_wantedinstances = 2

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.newcluster_name = f"new{cls.cluster_name}"

        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_prepare(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

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
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  router:
    instances: {self.routers_count}
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
        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, f"{self.cluster_name}-0", script)

        self.__class__.orig_tables = []
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            self.__class__.orig_tables = [r[0]
                                for r in s.query_sql("show tables in sakila").fetch_all()]

        # create a dump in a bucket
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: {self.cluster_name}
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
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

    def test_1_0_create_from_dump(self):
        """
        Create cluster using a shell dump stored in an OCI bucket.
        """
        kutil.create_user_secrets(self.ns, f"{self.newcluster_name}-newpwds", root_user="root", root_host="%", root_pass="sakila")

        bucket = g_ts_cfg.oci_bucket_name
        newcluster_start_instances = 1
        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.newcluster_name}
spec:
  instances: {newcluster_start_instances}
  router:
    instances: {self.routers_count}
  secretName: {self.newcluster_name}-newpwds
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

        self.wait_ic(self.newcluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, newcluster_start_instances):
            self.wait_pod(f"{self.newcluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.newcluster_name}-router-*", self.routers_count, timeout=self.routers_count*120)

        self.wait_ic(self.newcluster_name, "ONLINE", num_online=newcluster_start_instances, timeout=newcluster_start_instances*200)

        pods = kutil.ls_po(self.ns, pattern=(self.newcluster_name + "-.*"))
        print(pods)
        self.wait_routers(f"{self.newcluster_name}-router-*", self.routers_count)

        with mutil.MySQLPodSession(self.ns, f"{self.newcluster_name}-0", "root", "sakila") as s:
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

        pods = kutil.ls_po(self.ns, pattern=(self.newcluster_name + ".*"))
        print(pods)
        check_routing.check_pods(self, self.ns, self.newcluster_name, self.routers_count)

        # TODO also make sure the source field in the ic says clone and not blank

    def test_1_1_grow(self):
        """
        Ensures that a cluster created from a dump can be scaled up properly
        """
        kutil.patch_ic(self.ns, self.newcluster_name, {
                       "spec": {"instances": self.newcluster_wantedinstances }}, type="merge")

        pods = kutil.ls_po(self.ns, pattern=(self.newcluster_name + ".*"))
        print(pods)
        for instance in range(1, self.newcluster_wantedinstances):
            print(f"Waiting for pod {self.newcluster_name}-{instance}")
            self.wait_pod(f"{self.newcluster_name}-{instance}", "Running")

        self.wait_ic(self.newcluster_name, "ONLINE", num_online=self.newcluster_wantedinstances)

        # TODO: see comment at line 334 where unlogged_db should be created
        # check that the new instance was provisioned through clone and not incremental
        # with mutil.MySQLPodSession(self.ns, "newcluster-1", "root", "sakila") as s:
        #     self.assertEqual(
        #         str(s.query_sql("select * from unlogged_db.tbl").fetch_all()), str([[42]]))

    def test_1_2_destroy(self):
        kutil.delete_ic(self.ns, self.newcluster_name)

        self.wait_pods_gone(f"{self.newcluster_name}-*")
        self.wait_routers_gone(f"{self.newcluster_name}-router-*")
        self.wait_ic_gone(self.newcluster_name)

        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, f"{self.newcluster_name}-newpwds")

    def test_2_create_from_dump_options(self):
        """
        Create cluster using a shell dump with additional options passed to the
        load command.
        """
        kutil.create_user_secrets(self.ns, f"{self.newcluster_name}-newpwds", root_user="root", root_host="%", root_pass="sakila")

        bucket = g_ts_cfg.oci_bucket_name
        newcluster_start_instances = 1
        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.newcluster_name}
spec:
  instances: {newcluster_start_instances}
  router:
    instances: {self.routers_count}
  secretName: {self.newcluster_name}-newpwds
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

        self.wait_ic(self.newcluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, newcluster_start_instances):
            self.wait_pod(f"{self.newcluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.newcluster_name}-router-*", self.routers_count, timeout=self.routers_count*120)

        self.wait_ic(self.newcluster_name, "ONLINE", num_online=newcluster_start_instances, timeout=newcluster_start_instances*200)

        with mutil.MySQLPodSession(self.ns, f"{self.newcluster_name}-0", "root", "sakila") as s:
            tables = [r[0]
                      for r in s.query_sql("show tables in sakila").fetch_all()]

            self.assertEqual(set(self.__class__.orig_tables), set(tables))

        check_routing.check_pods(self, self.ns, self.newcluster_name, self.routers_count)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.newcluster_name)

        self.wait_pods_gone(f"{self.newcluster_name}-*")
        self.wait_routers_gone(f"{self.newcluster_name}-router-*")
        self.wait_ic_gone(self.newcluster_name)
        kutil.delete_secret(self.ns, f"{self.newcluster_name}-newpwds")

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

    _cluster_size = 1
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.newcluster_name = f"{cls.cluster_name}-newcluster"
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_prepare(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        container = g_ts_cfg.azure_container_name
        config_file = g_ts_cfg.azure_config_file

        # create a secret with the api key to access the container
        kutil.create_secret_from_files(self.ns, "azure-backup", [["config", config_file]])

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
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

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        script = open(tutil.g_test_data_dir+"/sql/sakila-schema.sql").read()
        script += open(tutil.g_test_data_dir+"/sql/sakila-data.sql").read()

        mutil.load_script(self.ns, f"{self.cluster_name}-0", script)

        self.__class__.orig_tables = []
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            self.__class__.orig_tables = [r[0]
                                for r in s.query_sql("show tables in sakila").fetch_all()]

        # create a dump in a Azure BLOB container
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {self.dump_name}
spec:
  clusterName: {self.cluster_name}
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

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def test_1_0_create_from_dump(self):
        """
        Create cluster using a shell dump stored in an Azure BLOB container.
        """
        kutil.create_user_secrets(self.ns, f"{self.newcluster_name}-newpwds", root_user="root", root_host="%", root_pass="sakila")
        new_cluster_size = 1
        container = g_ts_cfg.azure_container_name
        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.new_cluster_name}
spec:
  instances: {new_cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.newcluster_name}-newpwds
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

        self.wait_ic(self.newcluster_name, ["PENDING", "INITIALIZING", "ONLINE"])
        for instance in range(0, new_cluster_size):
            self.wait_pod(f"{self.newcluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.newcluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.newcluster_name, "ONLINE", num_online=new_cluster_size)

        with mutil.MySQLPodSession(self.ns, f"{self.newcluster_name}-0", "root", "sakila") as s:
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

        check_routing.check_pods(self, self.ns, self.newcluster_name, self.routers_count)

        # TODO also make sure the source field in the ic says clone and not blank

    def test_1_1_grow(self):
        """
        Ensures that a cluster created from a dump can be scaled up properly
        """
        kutil.patch_ic(self.ns, self.newcluster_name, {
                       "spec": {"instances": 2}}, type="merge")

        self.wait_pod(f"{self.newcluster_name}-1", "Running")

        self.wait_ic(self.newcluster_name, "ONLINE", num_online=2)

        # TODO: see comment at line 334 where unlogged_db should be created
        # check that the new instance was provisioned through clone and not incremental
        # with mutil.MySQLPodSession(self.ns, "newcluster-1", "root", "sakila") as s:
        #     self.assertEqual(
        #         str(s.query_sql("select * from unlogged_db.tbl").fetch_all()), str([[42]]))

    def test_1_2_destroy(self):
        kutil.delete_ic(self.ns, self.new_cluster_name)

        self.wait_pods_gone(f"{self.new_cluster_name}-*")
        self.wait_ic_gone(self.new_cluster_name)
        kutil.delete_secret(self.ns, f"{self.newcluster_name}-newpwds")

    def test_2_create_from_dump_options(self):
        """
        Create cluster using a shell dump with additional options passed to the
        load command.
        """
        kutil.create_user_secrets(self.ns, f"{self.newcluster_name}-newpwds", root_user="root", root_host="%", root_pass="sakila")

        container = g_ts_cfg.azure_container_name
        new_cluster_size = 1
        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.new_cluster_name}
spec:
  instances: {new_cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.newcluster_name}-newpwds
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

        self.wait_ic(self.newcluster_name, ["PENDING", "INITIALIZING", "ONLINE"])
        for instance in range(0, new_cluster_size):
            self.wait_pod(f"{self.newcluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.newcluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.newcluster_name, "ONLINE", num_online=new_cluster_size)

        with mutil.MySQLPodSession(self.ns, f"{self.newcluster_name}-0", "root", "sakila") as s:
            tables = [r[0]
                      for r in s.query_sql("show tables in sakila").fetch_all()]

            self.assertEqual(set(self.__class__.orig_tables), set(tables))

        check_routing.check_pods(self, self.ns, self.newcluster_name, self.routers_count)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

        kutil.delete_ic(self.ns, self.newcluster_name)

        self.wait_pods_gone(f"{self.newcluster_name}-*")
        self.wait_ic_gone(self.newcluster_name)
        kutil.delete_secret(self.ns, f"{self.newcluster_name}-newpwds")

        kutil.delete_secret(self.ns, "azure-backup")

        kutil.delete_pvc(self.ns, None)


# class ClusterFromDumpLocal(tutil.OperatorTest):
#    pass


class ClusterFromDumpErrors(tutil.OperatorTest):
    pass
