# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import configparser
import json
import unittest
from setup.config import g_ts_cfg
from utils import mutil, ociutil
from utils import kutil
from e2e.mysqloperator.keyring.keyring_base import KeyRingBase

from ..operator.operator_upgrade_t import change_operator_version

def read_vault_cfg(vault_cfg_path: str) -> dict:
    vault_cfg = configparser.ConfigParser()
    vault_cfg.read(vault_cfg_path)
    profile_name = "OCI"
    if not profile_name in vault_cfg:
        raise Exception(f"{profile_name} profile not found in {vault_cfg_path}")
    return vault_cfg[profile_name]

def check_oci_variables(testobj: KeyRingBase):
    for podname in ("mycluster-0", "mycluster-1", "mycluster-2"):
        with testobj.subTest(podname=podname):
            vault_cfg = testobj.__class__.vault_cfg

            with mutil.MySQLPodSession(testobj.ns, podname, testobj.user, testobj.password) as s:
                comp_status = {}
                for row in s.query_sql("""
                        SELECT STATUS_KEY, STATUS_VALUE
                        FROM performance_schema.keyring_component_status
                        """).fetch_all():
                    comp_status[row[0]] = row[1]

                testobj.assertEqual(comp_status["Component_name"], "component_keyring_oci")
                testobj.assertEqual(comp_status["user"], vault_cfg['user'])
                testobj.assertEqual(comp_status["tenancy"],  vault_cfg['tenancy'])
                testobj.assertEqual(comp_status["compartment"],  vault_cfg['compartment'])
                testobj.assertEqual(comp_status["virtual_vault"],  vault_cfg['virtual_vault'])
                testobj.assertEqual(comp_status["master_key"],  vault_cfg['master_key'])
                testobj.assertEqual(comp_status["encryption_endpoint"],  vault_cfg['encryption_endpoint'])
                testobj.assertEqual(comp_status["management_endpoint"],  vault_cfg['management_endpoint'])
                testobj.assertEqual(comp_status["vaults_endpoint"],  vault_cfg['vaults_endpoint'])
                testobj.assertEqual(comp_status["secrets_endpoint"],  vault_cfg['secrets_endpoint'])
                testobj.assertEqual(comp_status["key_file"],  '/.oci/privatekey')
                testobj.assertEqual(comp_status["key_fingerprint"],  vault_cfg['key_fingerprint'])

# test the key ring with OCI vault
@unittest.skipIf(g_ts_cfg.enterprise_skip or not g_ts_cfg.vault_cfg_path, "Enterprise test cases are skipped or vault config path is not set")
class KeyRingWithOciVault(KeyRingBase):
    oci_key = "oci-key"
    vault_cfg = dict()


    def keyring_secret_remove(self, keyring_name):
        if keyring_name:
            compartment_id = self.__class__.vault_cfg['compartment']
            vault_id = self.__class__.vault_cfg['virtual_vault']
            ociutil.delete_vault_secret_by_name("VAULT", compartment_id, vault_id, keyring_name)



    def test_1_run(self):
        vault_cfg_path = g_ts_cfg.vault_cfg_path
        self.__class__.vault_cfg = read_vault_cfg(g_ts_cfg.vault_cfg_path)
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
        check_oci_variables(self)

    def test_9_destroy(self):
        self.destroy_cluster()

        kutil.delete_secret(self.ns, self.oci_key)


@unittest.skipIf(g_ts_cfg.enterprise_skip or not g_ts_cfg.vault_cfg_path, "Enterprise test cases are skipped or vault config path is not set")
class KeyRingWithOciVaultConvertPluginToComponent(KeyRingBase):
    oci_key = "oci-key"
    vault_cfg = dict()

    def test_1_run(self):
        vault_cfg_path = g_ts_cfg.vault_cfg_path
        self.__class__.vault_cfg = read_vault_cfg(g_ts_cfg.vault_cfg_path)
        vault_cfg = self.__class__.vault_cfg

        change_operator_version(g_ts_cfg.operator_old_version_tag, store_operator_log=lambda: self.take_log_operator_snapshot())

        keyring_oci_key_file = kutil.adjust_key_file_path(vault_cfg_path, vault_cfg['key_file'])
        kutil.create_generic_secret(self.ns, self.oci_key, 'privatekey', keyring_oci_key_file)

        keyring_spec = f"""
  version: {g_ts_cfg.get_old_version_tag()}
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
        self.create_cluster(keyring_spec, no_check=True)

        change_operator_version(store_operator_log=lambda: self.take_log_operator_snapshot())

        kutil.patch_ic(self.ns, "mycluster", {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        def check_done(pod):
            po = kutil.get_po(self.ns, pod)
            # self.logger.debug(json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")))
            return json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")).get("version", "")

        self.wait(check_done, args=("mycluster-2", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=150, delay=10)
        self.wait(check_done, args=("mycluster-1", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=150, delay=10)
        self.wait(check_done, args=("mycluster-0", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=150, delay=10)

        check_oci_variables(self)

    def test_9_destroy(self):
        change_operator_version(store_operator_log=lambda: self.take_log_operator_snapshot())

        self.destroy_cluster()

        kutil.delete_secret(self.ns, self.oci_key)
