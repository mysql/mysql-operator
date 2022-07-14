# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from e2e.mysqloperator.enterprise.audit_log_base import AuditLogBase
from setup.config import g_ts_cfg

# test the audit log on the 3-instance cluster, set a filter, verify it is replicated to replicas
# then remove the filter, and assert it changed on replicas too
@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class AuditLogRemoveFilter(AuditLogBase):
    instance_primary = "mycluster-0"

    def test_0_create(self):
        self.create_cluster()


    def test_1_init(self):
        self.install_plugin_on_secondary("mycluster-2")
        self.install_plugin_on_secondary("mycluster-1")
        self.install_plugin_on_primary(self.instance_primary)
        self.set_default_filter(self.instance_primary)


    def test_2_verify_default_filter_is_set(self):
        self.assertTrue(self.has_default_filter_set("mycluster-0"))
        self.assertTrue(self.has_default_filter_set("mycluster-1"))
        self.assertTrue(self.has_default_filter_set("mycluster-2"))


    def test_3_remove_filter(self):
        self.remove_default_filter(self.instance_primary)


    def test_4_verify_filter_is_removed(self):
        self.assertFalse(self.has_default_filter_set("mycluster-0"))
        self.assertFalse(self.has_default_filter_set("mycluster-1"))
        self.assertFalse(self.has_default_filter_set("mycluster-2"))


    def test_9_destroy(self):
        self.destroy_cluster()
