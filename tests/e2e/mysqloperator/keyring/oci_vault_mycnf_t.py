# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import unittest
from e2e.mysqloperator.cluster.cluster_t import check_all
from setup.config import g_ts_cfg
from utils import auxutil, mutil, ociutil
from utils import kutil
from utils import tutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log


# test the key ring with OCI vault
@unittest.skipIf(g_ts_cfg.enterprise_skip or not g_ts_cfg.vault_cfg_path, "Enterprise test cases are skipped or vault config path is not set")
class KeyRingWithOciVaultMycnf(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    oci_key = "oci-key"
    user = "root"
    password = "sakila"
    cluster_size = 3
    routers_count = 1
    vault_cfg = dict()
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

    def read_vault_cfg(self, vault_cfg_path):
        import configparser
        vault_cfg = configparser.ConfigParser()
        vault_cfg.read(vault_cfg_path)
        profile_name = "OCI"
        if not profile_name in vault_cfg:
            raise Exception(f"{profile_name} profile not found in {vault_cfg_path}")
        return vault_cfg[profile_name]

    def generate_keyring_name(self):
        random_suffix = auxutil.random_string(8)
        keyring_name = f"{g_ts_cfg.k8s_context}_keyring_{random_suffix}"
        print(f"keyring name: {keyring_name}")
        return keyring_name

    def verify_table_encrypted(self, session, schema_name, table_name, encryption_expected):
        schema_table_name = f"{schema_name}/{table_name}"
        query = f"SELECT name, encryption FROM information_Schema.innodb_tablespaces where name = '{schema_table_name}'"
        table_info = session.query_sql(query).fetch_one()
        self.assertEqual(table_info[0], schema_table_name)
        is_table_encrypted = table_info[1] == 'Y'
        self.assertEqual(is_table_encrypted, encryption_expected)
        print('fetch_one: ', session.query_sql(query).fetch_one())

    def keyring_secret_remove(self, keyring_name):
        if keyring_name:
            compartment_id = self.__class__.vault_cfg['compartment']
            vault_id = self.__class__.vault_cfg['virtual_vault']
            ociutil.delete_vault_secret_by_name("VAULT", compartment_id, vault_id, keyring_name)

    def verify_variable(self, session, var_name, expected_value):
        var_row = session.query_sql(f"SHOW VARIABLES like '{var_name}'").fetch_one()
        var_value = var_row[1]
        self.assertEqual(var_value, expected_value)


    def test_1_create_cluster(self):
        vault_cfg_path = g_ts_cfg.vault_cfg_path
        self.__class__.vault_cfg = self.read_vault_cfg(g_ts_cfg.vault_cfg_path)
        vault_cfg = self.__class__.vault_cfg

        keyring_oci_key_file = kutil.adjust_key_file_path(vault_cfg_path, vault_cfg['key_file'])
        kutil.create_generic_secret(self.ns, self.oci_key, 'oci_api_key.pem', keyring_oci_key_file)

        yaml = f"""apiVersion: v1
kind: ConfigMap
metadata:
  name: initsql
data:
  foo.sh: |
    #!/bin/sh
    sleep 30
    mysql --defaults-extra-file="$PASSFILE" --protocol=socket -uroot -hlocalhost --socket="$SOCKET" -e "select @@log_bin, @@super_read_only;"
      "${{mysql[@]}}" <<EOT
        INSTALL PLUGIN keyring_udf SONAME 'keyring_udf.so';
        CREATE FUNCTION IF NOT EXISTS keyring_key_generate RETURNS INTEGER SONAME 'keyring_udf.so';
        CREATE FUNCTION IF NOT EXISTS keyring_key_fetch RETURNS STRING SONAME 'keyring_udf.so';
        CREATE FUNCTION IF NOT EXISTS keyring_key_length_fetch RETURNS INTEGER SONAME 'keyring_udf.so';
        CREATE FUNCTION IF NOT EXISTS keyring_key_type_fetch RETURNS STRING SONAME 'keyring_udf.so';
        CREATE FUNCTION IF NOT EXISTS keyring_key_store RETURNS INTEGER SONAME 'keyring_udf.so';
        CREATE FUNCTION IF NOT EXISTS keyring_key_remove RETURNS INTEGER SONAME 'keyring_udf.so';
    EOT
---
apiVersion: v1
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
  podSpec:
    initContainers:
      - name: initmysql
        volumeMounts:
        - name: custominitsql
          mountPath: /docker-entrypoint-initdb.d/09-foo.sh
          subPath: 09-foo.sh
        - name: ocikey
          mountPath: /.oci
    containers:
      - name: mysql
        volumeMounts:
        - name: ocikey
          mountPath: /.oci
    volumes:
      - name: custominitsql
        configMap:
          name: initsql
          deefaultMode: 0755
          items:
            - key: foo.sh
              path:  09-foo.sh
      - name: ocikey
        secret:
          secretName: oci-key
  mycnf: |
    [mysqld]
    early-plugin-load=keyring_oci.so
    keyring_oci_user={vault_cfg['user']}
    keyring_oci_tenancy={vault_cfg['tenancy']}
    keyring_oci_compartment={vault_cfg['compartment']}
    keyring_oci_virtual_vault={vault_cfg['virtual_vault']}
    keyring_oci_master_key={vault_cfg['master_key']}
    keyring_oci_encryption_endpoint={vault_cfg['encryption_endpoint']}
    keyring_oci_management_endpoint={vault_cfg['management_endpoint']}
    keyring_oci_vaults_endpoint={vault_cfg['vaults_endpoint']}
    keyring_oci_secrets_endpoint={vault_cfg['secrets_endpoint']}
    keyring_oci_key_file=/.oci/oci_api_key.pem
    keyring_oci_key_fingerprint={vault_cfg['key_fingerprint']}
"""
        kutil.apply(self.ns, yaml)
        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", num_online=self.cluster_size)

        self.wait_routers("mycluster-router-*", self.routers_count)

        check_all(self, self.ns, "mycluster",
            instances=self.cluster_size, routers=self.routers_count, primary=0)

    def test_2_create_keyring(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            self.__class__.keyring_name = self.generate_keyring_name()
            keyring_name = self.__class__.keyring_name
            self.keyring_secret_remove(keyring_name)

            print(s.query_sql(f"SELECT keyring_key_store('{keyring_name}', 'AES', 'Secret string')").fetch_one())
            print(s.query_sql("SELECT space, name, space_Type, encryption FROM information_Schema.innodb_tablespaces").fetch_all())
            print(s.query_sql(f"SELECT keyring_key_fetch('{keyring_name}')").fetch_one())

    def test_3_run_checks(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            schema_name = 'keyring_oci_vault_test'
            s.exec_sql(f"CREATE SCHEMA {schema_name}")
            s.exec_sql(f"USE {schema_name}")

            s.exec_sql(f"CREATE TABLE t1 (c1 INT) ENCRYPTION = 'Y'")
            self.verify_table_encrypted(s, schema_name, 't1', True)

            s.exec_sql(f"CREATE TABLE t2 (c1 INT)")
            self.verify_table_encrypted(s, schema_name, 't2', False)

            s.exec_sql(f"ALTER TABLE t2 ENCRYPTION='Y'")
            self.verify_table_encrypted(s, schema_name, 't2', True)

            vault_cfg = self.__class__.vault_cfg
            self.verify_variable(s, 'keyring_oci_ca_certificate', '')
            self.verify_variable(s, 'keyring_oci_conf_file','')
            self.verify_variable(s, 'keyring_oci_compartment', vault_cfg['compartment'])
            self.verify_variable(s, 'keyring_oci_encryption_endpoint', vault_cfg['encryption_endpoint'])
            self.verify_variable(s, 'keyring_oci_key_fingerprint', vault_cfg['key_fingerprint'])
            self.verify_variable(s, 'keyring_oci_management_endpoint', vault_cfg['management_endpoint'])
            self.verify_variable(s, 'keyring_oci_master_key', vault_cfg['master_key'])
            self.verify_variable(s, 'keyring_oci_secrets_endpoint', vault_cfg['secrets_endpoint'])
            self.verify_variable(s, 'keyring_oci_tenancy', vault_cfg['tenancy'])
            self.verify_variable(s, 'keyring_oci_user', vault_cfg['user'])
            self.verify_variable(s, 'keyring_oci_vaults_endpoint', vault_cfg['vaults_endpoint'])
            self.verify_variable(s, 'keyring_oci_virtual_vault', vault_cfg['virtual_vault'])
            self.verify_variable(s, 'keyring_oci_key_file', '/.oci/oci_api_key.pem')
            self.verify_variable(s, 'keyring_operations', 'ON')

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")

        self.keyring_secret_remove(self.__class__.keyring_name)
