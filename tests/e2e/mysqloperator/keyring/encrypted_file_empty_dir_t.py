# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from setup.config import g_ts_cfg
from e2e.mysqloperator.keyring.keyring_base import KeyRingBase


# test the key ring encrypted file with EmptyDir storage
@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class KeyRingEncryptedFileEmptyDir(KeyRingBase):

    def test_1_run(self):
        encrypted_file_secret_name = self.create_secret_for_encrypted_file()

        keyring_spec = f"""
  keyring:
    encryptedFile:
      fileName: "component_keyring_encrypted_file"
      readOnly: false
      password: {encrypted_file_secret_name}
      storage:
        emptyDir: {{}}
"""

        self.create_cluster(keyring_spec)
        # with emptyDir we can only check on a single pod as keyring is not shared
        self.create_keyring(False)
        self.encrypt_tables()
        self.check_variables()

    def test_9_destroy(self):
        self.destroy_cluster()
