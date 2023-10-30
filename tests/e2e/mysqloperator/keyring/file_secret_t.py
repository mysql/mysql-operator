# Copyright (c) 2022, 2023 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from setup.config import g_ts_cfg
from e2e.mysqloperator.keyring.keyring_base import KeyRingBase


# test the key ring file with secret storage
@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class KeyRingFileSecret(KeyRingBase):
    secret_name = "keyring-file-secret"

    def test_1_run(self):
        self.create_secret(self.secret_name)

        keyring_spec = f"""
  keyring:
    file:
      fileName: "component_keyring_file"
      readOnly: true
      storage:
        secret:
          secretName: {self.secret_name}
"""

        self.create_cluster(keyring_spec)
        self.read_key("test-key-name")
        self.check_variables()

    def test_9_destroy(self):
        self.destroy_cluster()
