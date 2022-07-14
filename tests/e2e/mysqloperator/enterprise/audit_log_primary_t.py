# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from e2e.mysqloperator.enterprise import audit_log_base
from setup.config import g_ts_cfg
from utils import auxutil
from utils import mutil

# test the audit log on the primary of a cluster
@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class AuditLogPrimary(audit_log_base.AuditLogBase):
    add_data_timestamp = None
    test_table = "mycluster0"
    instance_primary = "mycluster-0"

    def test_0_create(self):
        self.create_cluster()


    def test_1_init(self):
        self.install_plugin_on_primary(self.instance_primary)
        self.set_default_filter(self.instance_primary)


    def test_2_add_data(self):
        self.__class__.add_data_timestamp = auxutil.utctime()
        with mutil.MySQLPodSession(self.ns, self.instance_primary, self.user, self.password) as s:
            s.exec_sql("CREATE SCHEMA audit_foo")
            s.exec_sql(f"CREATE TABLE audit_foo.{self.test_table} (id INT NOT NULL, name VARCHAR(20), PRIMARY KEY(id))")
            s.exec_sql(f'INSERT INTO audit_foo.{self.test_table} VALUES (123456, "first_audit")')
            s.exec_sql(f'INSERT INTO audit_foo.{self.test_table} VALUES (654321, "second_audit")')
            s.exec_sql(f'FLUSH TABLES audit_foo.{self.test_table}')


    def test_3_verify_log(self):
        self.assertTrue(self.does_log_exist(self.instance_primary))

        self.assertTrue(self.has_default_filter_set(self.instance_primary))

        log_data = self.get_log_data(self.instance_primary, self.add_data_timestamp)
        self.assertIn("CREATE SCHEMA audit_foo", log_data)
        self.assertIn(f"CREATE TABLE audit_foo.{self.test_table} (id INT NOT NULL, name VARCHAR(20), PRIMARY KEY(id))", log_data)
        self.assertIn(f'INSERT INTO audit_foo.{self.test_table} VALUES (123456, \\\\"first_audit\\\\")', log_data)
        self.assertIn(f'INSERT INTO audit_foo.{self.test_table} VALUES (654321, \\\\"second_audit\\\\")', log_data)


    def test_9_destroy(self):
        self.destroy_cluster()
