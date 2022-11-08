# Copyright (c) 2022, Oracle and/or its affiliates.
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

    def generate_keyring_name(self):
        random_suffix = auxutil.random_string(8)
        keyring_name = f"{g_ts_cfg.k8s_context}_keyring_{random_suffix}"
        self.logger.debug(f"keyring name: {keyring_name}")
        return keyring_name

    def verify_table_encrypted(self, session, schema_name, table_name, encryption_expected):
        schema_table_name = f"{schema_name}/{table_name}"
        query = f"SELECT name, encryption FROM information_Schema.innodb_tablespaces where name = '{schema_table_name}'"
        table_info = session.query_sql(query).fetch_one()
        self.assertEqual(table_info[0], schema_table_name)
        is_table_encrypted = table_info[1] == 'Y'
        self.assertEqual(is_table_encrypted, encryption_expected)

    def check_variable(self, session, var_name, expected_value):
        var_row = session.query_sql(f"SHOW VARIABLES like '{var_name}'").fetch_one()
        var_value = var_row[1]
        self.assertEqual(var_value, expected_value)


    def create_cluster(self, keyring_spec):
        yaml = f"""apiVersion: v1
kind: Secret
metadata:
  name: mypwds
stringData:
  rootUser: {self.user}
  rootHost: localhost
  rootPassword: {self.password}
---
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  tlsUseSelfSigned: true
  edition: enterprise
{keyring_spec}
"""
        kutil.apply(self.ns, yaml)
        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", num_online=self.cluster_size)

        self.wait_routers("mycluster-router-*", self.routers_count)

        check_all(self, self.ns, "mycluster",
            instances=self.cluster_size, routers=self.routers_count, primary=0)

    def create_volume(self, volume_name):
        yaml = f"""
apiVersion: v1
kind: PersistentVolume
metadata:
  name: {volume_name}
  labels:
    type: local
spec:
  storageClassName: manual
  capacity:
    storage: 2Gi
  accessModes:
    - ReadWriteOnce
  hostPath:
    path: "/"
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: {volume_name}
spec:
  storageClassName: manual
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
"""
        kutil.apply(self.ns, yaml)

    def create_keyring(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            self.__class__.keyring_name = self.generate_keyring_name()
            keyring_name = self.__class__.keyring_name

            self.logger.debug(s.query_sql(f"SELECT keyring_key_store('{keyring_name}', 'AES', 'Secret string')").fetch_one())
            self.logger.debug(s.query_sql("SELECT space, name, space_Type, encryption FROM information_Schema.innodb_tablespaces").fetch_all())
            self.logger.debug(s.query_sql(f"SELECT keyring_key_fetch('{keyring_name}')").fetch_one())

    def check_variables(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            self.check_variable(s, 'keyring_operations', 'ON')

    def encrypt_tables(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
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
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
