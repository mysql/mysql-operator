# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from e2e.mysqloperator.enterprise.audit_log_base import AuditLogBase
from setup.config import g_ts_cfg
from utils import auxutil
from utils import mutil

# test the audit log on the 3-instance cluster, with plugin installed on primary, then
# with some delay on both secondaries
@unittest.skipIf(g_ts_cfg.enterprise_skip or g_ts_cfg.audit_log_skip, "Enterprise Audit Log test cases are skipped")
class AuditLogDelayedSecondary(AuditLogBase):
    add_data_timestamp = None
    instance_primary = "mycluster-0"

    def test_0_create(self):
        self.create_cluster()


    def test_1_init(self):
        self.install_plugin_on_primary("mycluster-0")
        self.set_default_filter(self.instance_primary)


    def test_2_prepare_data(self):
        self.__class__.add_data_timestamp = auxutil.utctime()
        with mutil.MySQLPodSession(self.ns, "mycluster-1", self.user, self.password) as s:
            res = s.query_sql("SHOW TABLES").fetch_all()
            self.assertIsNotNone(res)

        with mutil.MySQLPodSession(self.ns, "mycluster-2", self.user, self.password) as s:
            res = s.query_sql("SHOW DATABASES").fetch_all()
            self.assertIsNotNone(res)


    def test_3_verify_secondary_empty(self):
        self.assertFalse(self.does_log_exist("mycluster-1"))
        self.assertFalse(self.does_log_exist("mycluster-2"))

        # even without the audit plugin installed, the tables related to filtering should be replicated
        self.assertTrue(self.has_default_filter_set("mycluster-1"))
        self.assertTrue(self.has_default_filter_set("mycluster-2"))


    def test_4_install_on_secondary(self):
        self.install_plugin_on_secondary("mycluster-1")
        self.install_plugin_on_secondary("mycluster-2")


    def test_5_prepare_data(self):
        self.__class__.add_data_timestamp = auxutil.utctime()
        with mutil.MySQLPodSession(self.ns, "mycluster-1", self.user, self.password) as s:
            res = s.query_sql("SHOW PLUGINS").fetch_all()
            self.assertIsNotNone(res)

        with mutil.MySQLPodSession(self.ns, "mycluster-2", self.user, self.password) as s:
            res = s.query_sql("SHOW PROFILES").fetch_all()
            self.assertIsNotNone(res)

        self.rotate_log(self.instance_primary)


    def test_6_verify_secondary_empty(self):
        self.assertTrue(self.does_log_exist("mycluster-1"))
        self.assertTrue(self.does_log_exist("mycluster-2"))

        samples = [
            ("SHOW PLUGINS", True),
            ("SHOW PROFILES", False)
            ]
        self.assertIsNone(self.verify_log_data("mycluster-1", self.add_data_timestamp, samples))

        samples = [
            ("SHOW PLUGINS", False),
            ("SHOW PROFILES", True)
            ]
        self.assertIsNone(self.verify_log_data("mycluster-2", self.add_data_timestamp, samples))


    def test_9_destroy(self):
        self.destroy_cluster()
