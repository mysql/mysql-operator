# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import configparser
import json
import unittest
from time import time
from setup.config import g_ts_cfg
from utils import auxutil, mutil
from utils import kutil, tutil
from e2e.mysqloperator.keyring.keyring_base import KeyRingBase

def parse_key_value_file(filename):
    result = {}
    with open(filename, 'r') as file:
        content = file.read()
    for line_number, line in enumerate(content.split('\n'), 1):
        if not (line:= line.strip()):
            continue
        key, value = line.split('=', 1)
        if not (key := key.strip()):
            continue
        value = value.strip()
        result[key] = value
    return result


def check_kmip_variables(testobj: KeyRingBase, pods_to_check):
    for podname in pods_to_check:
        with testobj.subTest(podname=podname):
            kmip_cfg = testobj.__class__.kmip_cfg
            with mutil.MySQLPodSession(testobj.ns, podname, testobj.user, testobj.password) as s:
                comp_status = {}
                for row in s.query_sql("""
                        SELECT STATUS_KEY, STATUS_VALUE
                        FROM performance_schema.keyring_component_status
                        """).fetch_all():
                    comp_status[row[0]] = row[1]
                print(comp_status)
                testobj.assertEqual(comp_status["Component_name"], "component_keyring_kmip")
                testobj.assertEqual(comp_status["Author"], "Oracle Corporation")
                testobj.assertEqual(comp_status["License"], "PROPRIETARY")
                testobj.assertEqual(comp_status["Component_status"], "Active")
                testobj.assertEqual(comp_status["Server IP"],  kmip_cfg['SERVER'])
                testobj.assertEqual(comp_status["Standby IP [0]"],  kmip_cfg['SERVER'])
                testobj.assertEqual(comp_status["Standby IP [1]"],  kmip_cfg['SERVER'])
                testobj.assertEqual(comp_status["Caching"],  kmip_cfg["cache_keys"])


@unittest.skipIf(g_ts_cfg.enterprise_skip or not g_ts_cfg.vault_cfg_path, "Enterprise test cases are skipped or vault config path is not set")
class KeyRingWithKMIP(KeyRingBase):
    kmip_cfg = dict()

    def setUp(self):
        super().setUp()
        self.tls_secret_name = f"{self.ns}-{auxutil.random_string(8)}"

    def keyring_secret_remove(self, keyring_name):
        pass

    def test_1_run(self):
        kmip_config_path = g_ts_cfg.kmip_okvclient_ora_path
        self.__class__.kmip_cfg = parse_key_value_file(kmip_config_path)
        kmip_cfg = self.__class__.kmip_cfg
        kmip_cfg["cache_keys"] = "true"

        kutil.create_secret_from_paths(self.ns, self.tls_secret_name, [g_ts_cfg.kmip_tls_path])

        keyring_spec = f"""
  keyring:
    kmip:
        configuration: {self.tls_secret_name}
        cacheKeys: {kmip_cfg["cache_keys"]}
        server: "{kmip_cfg["SERVER"]}"
        standbyServer: ["{kmip_cfg["SERVER"]}", "{kmip_cfg["SERVER"]}"]
"""

        print("Creating cluster")
        self.create_cluster(keyring_spec)

        # coomponent_kmip seems to have a problem with keyring_key_store. Has no probs with keyring_key_generate
        # keyring_key_store works with component_oci
        #
        #    SQL > SELECT keyring_key_generate('MyKey222', 'AES', 16);
        #    +---------------------------------------------+
        #    | keyring_key_generate('MyKey222', 'AES', 16) |
        #    +---------------------------------------------+
        #    |                                           1 |
        #    +---------------------------------------------+
        #    1 row in set (1.4195 sec)
        #
        #    SQL >  select * from performance_schema.keyring_keys;
        #    +----------+----------------+--------------------------------------+
        #    | KEY_ID   | KEY_OWNER      | BACKEND_KEY_ID                       |
        #    +----------+----------------+--------------------------------------+
        #    | MyKey222 | root@localhost | C2781821-F7DA-44EB-B635-3674D1C3F3BC |
        #    +----------+----------------+--------------------------------------+
        #    1 row in set (0.0008 sec)
        #
        #    SQL > SELECT keyring_key_store('Somekeyring', 'AES', 'Secret String here');
        #    +---------------------------------------------------------------+
        #    | keyring_key_store('Somekeyring', 'AES', 'Secret String here') |
        #    +---------------------------------------------------------------+
        #    |                                                          NULL |
        #    +---------------------------------------------------------------+
        #    1 row in set (0.0012 sec)
        #
        #print("Creating keyring")
        #self.create_keyring()

        print("Encrypting tables")
        self.encrypt_tables()

        print("Checking variables")
        self.check_variables()

        print("Checking KMIP variables")
        check_kmip_variables(self, self.pods_to_check)

    def test_9_destroy(self):
        self.destroy_cluster()

        kutil.delete_secret(self.ns, self.tls_secret_name)


@unittest.skipIf(g_ts_cfg.enterprise_skip or not g_ts_cfg.vault_cfg_path, "Enterprise test cases are skipped or vault config path is not set")
class KeyRingWithKMIP2(KeyRingBase):
    kmip_cfg = dict()

    def setUp(self):
        super().setUp()
        self.tls_secret_name = f"{self.ns}-{auxutil.random_string(8)}"
        self.new_tls_secret_name = f"{self.tls_secret_name}-new"

    def keyring_secret_remove(self, keyring_name):
        pass

    def test_1_run(self):
        kmip_config_path = g_ts_cfg.kmip_okvclient_ora_path
        self.__class__.kmip_cfg = parse_key_value_file(kmip_config_path)
        kmip_cfg = self.__class__.kmip_cfg
        kmip_cfg["cache_keys"] = "true"

        kutil.create_secret_from_paths(self.ns, self.tls_secret_name, [g_ts_cfg.kmip_tls_path])

        keyring_spec = f"""
  keyring:
    kmip:
        configuration: {self.tls_secret_name}
        cacheKeys: {kmip_cfg["cache_keys"]}
        server: "{kmip_cfg["SERVER"]}"
        standbyServer: ["{kmip_cfg["SERVER"]}", "{kmip_cfg["SERVER"]}"]
"""

        print("Creating cluster")
        self.create_cluster(keyring_spec)

        print("Encrypting tables")
        self.encrypt_tables()

        kutil.create_secret_from_paths(self.ns, self.new_tls_secret_name, [g_ts_cfg.kmip_tls_path])

        patch = {"spec": { "keyring" : { "kmip" : { "configuration": self.new_tls_secret_name }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", self.cluster_size)

        print(f"Cluster {self.cluster_name} again ONLINE after %.2f seconds " % (time() - start_time))

        print("Checking variables")
        self.check_variables()

        print("Checking KMIP variables")
        check_kmip_variables(self, self.pods_to_check)

    def test_9_destroy(self):
        self.destroy_cluster()

        kutil.delete_secret(self.ns, self.tls_secret_name)
        kutil.delete_secret(self.ns, self.new_tls_secret_name)
