# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from setup.config import g_ts_cfg
from e2e.mysqloperator.keyring.keyring_base import KeyRingBase


# test the key encrypted ring file with PVC storage
@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class KeyRingEncryptedFilePvc(KeyRingBase):
    volume_name = "keyring-encrypted-file-volume"

    def test_1_run(self):
        encrypted_file_secret_name = self.create_secret_for_encrypted_file()
        self.create_volume(self.volume_name)

        keyring_spec = f"""
  keyring:
    encryptedFile:
      fileName: "component_keyring_encrypted_file"
      readOnly: false
      password: {encrypted_file_secret_name}
      storage:
        persistentVolumeClaim:
          claimName: {self.volume_name}
"""

        self.create_cluster(keyring_spec)
        self.create_keyring()
        self.encrypt_tables()
        self.check_variables()

    def test_9_destroy(self):
        self.destroy_cluster()
