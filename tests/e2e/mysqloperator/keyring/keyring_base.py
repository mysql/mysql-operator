# Copyright (c) 2022, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
from e2e.mysqloperator.cluster.cluster_t import check_all
from setup.config import g_ts_cfg
from utils import auxutil, mutil
from utils import kutil
from utils import tutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log


class KeyRingBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    user = "root"
    password = "sakila"
    cluster_size = 3
    routers_count = 1
    keyring_name = None
    secret_string = "Secret string"

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.cluster_size):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")


    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.cluster_size)):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def generate_keyring_name(self):
        keyring_name = f"{g_ts_cfg.k8s_context}_keyring_{self.random_suffix}"
        self.logger.debug(f"keyring name: {keyring_name}")
        return keyring_name

    def verify_table_encrypted(self, session, schema_name, table_name, encryption_expected):
        schema_table_name = f"{schema_name}/{table_name}"
        query = f"SELECT name, encryption FROM INFORMATION_SCHEMA.innodb_tablespaces where name = '{schema_table_name}'"
        table_info = session.query_sql(query).fetch_one()
        self.assertEqual(table_info[0], schema_table_name)
        is_table_encrypted = table_info[1] == 'Y'
        self.assertEqual(is_table_encrypted, encryption_expected)

    def check_variable(self, session, var_name, expected_value):
        var_row = session.query_sql(f"SHOW VARIABLES like '{var_name}'").fetch_one()
        var_value = var_row[1]
        self.assertEqual(var_value, expected_value)

    def create_cluster(self, keyring_spec, no_check: bool = False):
        """
        Create an InnoDB Cluster with kering

        By default this will check everything through, but in OCI Keyring tests
        we test upgrades, there the full check won't see the pod definition
        it expects with newer operators

        :param keyring_spec: spec to be injected in shared spec
        :param no_check: whether to check or not
        """
        yaml = f"""apiVersion: v1
kind: Secret
metadata:
  name: {self.cluster_secret_name}
stringData:
  rootUser: {self.user}
  rootHost: localhost
  rootPassword: {self.password}
---
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  secretName: {self.cluster_secret_name}
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  tlsUseSelfSigned: true
  edition: enterprise
{keyring_spec}
"""
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if not no_check:
            check_all(self, self.ns, self.cluster_name,
                instances=self.cluster_size, routers=self.routers_count, primary=0)

    def create_volume(self, volume_name):
        yaml = f"""
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {volume_name}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: 2Gi
"""
        kutil.apply(self.ns, yaml)

    def create_secret(self, secret_name):
        yaml = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {secret_name}
stringData:
  component_keyring_encrypted_file: 1B9CD7A23C7CF1EB4DFEF748716A8271A13797FD300A3D5B187837B5E7097B0946E7C23F405B89410DF21DB0503A38B68E47F5B15AF3A7969DE53BE9F1EFBBC963287BE00CCE388B5E7931648E90E8F79C20042D1F6FD1BF6CC26E6657E056371D5C4C1B30F210846A9DF2A91633689466EDBA519659983F64A253D917E01DE1E84B372D050728C9F1A7706358BFF370A4D71735A076036582B663C4A00411D8973D40DD65781E3FDADB1353746765B8
  component_keyring_file: |
   {{"version":"1.0","elements":[{{"user":"root@localhost","data_id":"test-key-name","data_type":"AES","data":"53656372657420737472696E67","extension":[]}}]}}
"""
        kutil.apply(self.ns, yaml)

    def create_secret_for_encrypted_file(self):
        secret_name = "encrypted-file-secret"
        yaml = f"""
apiVersion: v1
kind: Secret
metadata:
  name: {secret_name}
stringData:
  keyring_password: {self.password}
"""
        kutil.apply(self.ns, yaml)
        return secret_name

    def create_config_map(self, cm_name):
        yaml = f"""
apiVersion: v1
kind: ConfigMap
metadata:
  name: {cm_name}
data:
  component_keyring_encrypted_file: 1B9CD7A23C7CF1EB4DFEF748716A8271A13797FD300A3D5B187837B5E7097B0946E7C23F405B89410DF21DB0503A38B68E47F5B15AF3A7969DE53BE9F1EFBBC963287BE00CCE388B5E7931648E90E8F79C20042D1F6FD1BF6CC26E6657E056371D5C4C1B30F210846A9DF2A91633689466EDBA519659983F64A253D917E01DE1E84B372D050728C9F1A7706358BFF370A4D71735A076036582B663C4A00411D8973D40DD65781E3FDADB1353746765B8
  component_keyring_file: |
   {{"version":"1.0","elements":[{{"user":"root@localhost","data_id":"test-key-name","data_type":"AES","data":"53656372657420737472696E67","extension":[]}}]}}
"""
        kutil.apply(self.ns, yaml)

    def create_keyring(self, check_all_pods=True):
        self.__class__.keyring_name = self.generate_keyring_name()
        keyring_name = self.__class__.keyring_name

        print(f"Storing '{self.secret_string}' with AES cipher into the keyring store on {self.cluster_name}-0")
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", self.user, self.password) as s:
            self.assertTupleEqual(
                s.query_sql(f"SELECT keyring_key_store('{keyring_name}', 'AES', '{self.secret_string}')").fetch_one(),
                (1,))

        pods_to_check = [f'{self.cluster_name}-0']
        if check_all_pods:
            pods_to_check = self.pods_to_check
            # On keyring_file/keyring_encrypted_file the values are cached, by
            # restarting we can read them from other nodes
            print(f"Shutting down {self.cluster_name}-1 and waiting to reach Terminating")
            with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", self.user, self.password) as s:
                s.exec_sql("SHUTDOWN")
                kutil.wait_pod(self.ns, f"{self.cluster_name}-1", "Terminating")

            print(f"Shutting down {self.cluster_name}-2 and waiting to reach Terminating")
            with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-2", self.user, self.password) as s:
                s.exec_sql("SHUTDOWN")
                kutil.wait_pod(self.ns, f"{self.cluster_name}-2", "Terminating")

            print(f"Checking that {self.cluster_name}-1 is back ready")
            kutil.wait_pod(self.ns, f"{self.cluster_name}-1", checkready=True)
            print(f"Checking that {self.cluster_name}-2 is back ready")
            kutil.wait_pod(self.ns, f"{self.cluster_name}-2", checkready=True)

        print(f"Reading a key from {keyring_name} on pods {pods_to_check}")
        self.read_key(keyring_name, pods_to_check)

    @property
    def pods_to_check(self):
        return tuple([f"{self.cluster_name}-{instance}" for instance in range(0, self.cluster_size)])

    def read_key(self, keyring_name, pods_to_check=None):
        if pods_to_check is None:
            pods_to_check = self.pods_to_check
        for pod_name in pods_to_check:
            with self.subTest(pod_name=pod_name):
                print(f"Checking key fetch on {pod_name}")
                with mutil.MySQLPodSession(self.ns, pod_name, self.user, self.password) as s:
                    self.assertTupleEqual(
                        s.query_sql(f"SELECT CAST(keyring_key_fetch('{keyring_name}') AS CHAR(255))").fetch_one(),
                        (self.secret_string, ))

    def check_variables(self):
        for podname in (f"{self.cluster_name}-0", f"{self.cluster_name}-1", f"{self.cluster_name}-2"):
            with self.subTest(podname=podname):
                print(f"Checking keyring_operations variable on {podname}")
                with mutil.MySQLPodSession(self.ns, podname, self.user, self.password) as s:
                    self.check_variable(s, 'keyring_operations', 'ON')

    def encrypt_tables(self):
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", self.user, self.password) as s:
            schema_name = 'keyring_test_schema'
            s.exec_sql(f"CREATE SCHEMA {schema_name}")
            s.exec_sql(f"USE {schema_name}")

            s.exec_sql(f"CREATE TABLE t1 (c1 INT) ENCRYPTION = 'Y'")
            self.verify_table_encrypted(s, schema_name, 't1', True)
            s.exec_sql(f"DROP TABLE t1")

            s.exec_sql(f"CREATE TABLE t2 (c1 INT)")
            self.verify_table_encrypted(s, schema_name, 't2', False)

            s.exec_sql(f"ALTER TABLE t2 ENCRYPTION='Y'")
            self.verify_table_encrypted(s, schema_name, 't2', True)

            s.exec_sql(f"DROP TABLE t2")

            s.exec_sql(f"DROP SCHEMA {schema_name}")

    def destroy_cluster(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_pvc(self.ns, None)
