# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from setup.config import g_ts_cfg
from utils import mutil, ociutil
from utils import kutil
from e2e.mysqloperator.keyring.keyring_base import KeyRingBase


# test the key ring with OCI vault
@unittest.skipIf(g_ts_cfg.enterprise_skip or not g_ts_cfg.vault_cfg_path, "Enterprise test cases are skipped or vault config path is not set")
class KeyRingWithOciVault(KeyRingBase):
    oci_key = "oci-key"
    vault_cfg = dict()

    def read_vault_cfg(self, vault_cfg_path):
        import configparser
        vault_cfg = configparser.ConfigParser()
        vault_cfg.read(vault_cfg_path)
        profile_name = "OCI"
        if not profile_name in vault_cfg:
            raise Exception(f"{profile_name} profile not found in {vault_cfg_path}")
        return vault_cfg[profile_name]

    def keyring_secret_remove(self, keyring_name):
        if keyring_name:
            compartment_id = self.__class__.vault_cfg['compartment']
            vault_id = self.__class__.vault_cfg['virtual_vault']
            ociutil.delete_vault_secret_by_name("VAULT", compartment_id, vault_id, keyring_name)


    def check_oci_variables(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            vault_cfg = self.__class__.vault_cfg
            self.check_variable(s, 'keyring_oci_ca_certificate', '')
            self.check_variable(s, 'keyring_oci_conf_file','')
            self.check_variable(s, 'keyring_oci_compartment', vault_cfg['compartment'])
            self.check_variable(s, 'keyring_oci_encryption_endpoint', vault_cfg['encryption_endpoint'])
            self.check_variable(s, 'keyring_oci_key_fingerprint', vault_cfg['key_fingerprint'])
            self.check_variable(s, 'keyring_oci_management_endpoint', vault_cfg['management_endpoint'])
            self.check_variable(s, 'keyring_oci_master_key', vault_cfg['master_key'])
            self.check_variable(s, 'keyring_oci_secrets_endpoint', vault_cfg['secrets_endpoint'])
            self.check_variable(s, 'keyring_oci_tenancy', vault_cfg['tenancy'])
            self.check_variable(s, 'keyring_oci_user', vault_cfg['user'])
            self.check_variable(s, 'keyring_oci_vaults_endpoint', vault_cfg['vaults_endpoint'])
            self.check_variable(s, 'keyring_oci_virtual_vault', vault_cfg['virtual_vault'])
            self.check_variable(s, 'keyring_oci_key_file', '/.oci/privatekey')

    def test_1_run(self):
        vault_cfg_path = g_ts_cfg.vault_cfg_path
        self.__class__.vault_cfg = self.read_vault_cfg(g_ts_cfg.vault_cfg_path)
        vault_cfg = self.__class__.vault_cfg

        keyring_oci_key_file = kutil.adjust_key_file_path(vault_cfg_path, vault_cfg['key_file'])
        kutil.create_generic_secret(self.ns, self.oci_key, 'privatekey', keyring_oci_key_file)

        keyring_spec = f"""
  keyring:
    oci:
      user: {vault_cfg['user']}
      keySecret: {self.oci_key}
      keyFingerprint: {vault_cfg['key_fingerprint']}
      tenancy: {vault_cfg['tenancy']}
      compartment: {vault_cfg['compartment']}
      virtualVault: {vault_cfg['virtual_vault']}
      masterKey: {vault_cfg['master_key']}
      endpoints:
        encryption: {vault_cfg['encryption_endpoint']}
        management: {vault_cfg['management_endpoint']}
        vaults: {vault_cfg['vaults_endpoint']}
        secrets: {vault_cfg['secrets_endpoint']}
"""
        self.create_cluster(keyring_spec)

        self.create_keyring()

        self.encrypt_tables()

        self.check_variables()
        self.check_oci_variables()

    def test_9_destroy(self):
        self.destroy_cluster()

        self.keyring_secret_remove(self.__class__.keyring_name)
