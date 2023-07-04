# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from e2e.mysqloperator.enterprise.audit_log_base import AuditLogBase
from setup.config import g_ts_cfg
from utils import auxutil
from utils import mutil

# test the audit log on the 3-instance cluster, set a filter, verify it is replicated to replicas
# then set another filter, and assert it changed on replicas too
@unittest.skipIf(g_ts_cfg.enterprise_skip or g_ts_cfg.audit_log_skip, "Enterprise Audit Log test cases are skipped")
class AuditLogChangeFilter(AuditLogBase):
    add_data_timestamp = None
    instance_primary = "mycluster-0"

    def test_0_create(self):
        self.create_cluster()


    def test_1_init(self):
        self.install_plugin_on_primary(self.instance_primary)
        self.install_plugin_on_secondary("mycluster-1")
        self.set_custom_filter(self.instance_primary)
        self.install_plugin_on_secondary("mycluster-2")


    def test_2_verify_custom_filter_is_set(self):
        self.assertTrue(self.has_custom_filter_set("mycluster-0"))
        self.assertTrue(self.has_custom_filter_set("mycluster-1"))
        self.assertTrue(self.has_custom_filter_set("mycluster-2"))


    def test_3_set_default_filter(self):
        self.remove_custom_filter(self.instance_primary)
        self.set_default_filter(self.instance_primary)


    def test_4_verify_default_filter_is_set(self):
        self.assertTrue(self.has_default_filter_set("mycluster-0"))
        self.assertTrue(self.has_default_filter_set("mycluster-1"))
        self.assertTrue(self.has_default_filter_set("mycluster-2"))


    def test_5_generate_data(self):
        self.__class__.add_data_timestamp = auxutil.utctime()

        with mutil.MySQLPodSession(self.ns, self.instance_primary, self.user, self.password) as s:
            s.exec_sql("CREATE DATABASE audit_foo")

        with mutil.MySQLPodSession(self.ns, "mycluster-1", self.user, self.password) as s:
            res = s.query_sql("SHOW PROFILES").fetch_all()
            self.assertIsNotNone(res)

        with mutil.MySQLPodSession(self.ns, "mycluster-2", self.user, self.password) as s:
            res = s.query_sql("SHOW SCHEMAS").fetch_all()
            self.assertIsNotNone(res)
            s.exec_sql("FLUSH TABLES")

        self.rotate_log(self.instance_primary)


    def test_6_verify_logs(self):
        self.assertTrue(self.does_log_exist("mycluster-0"))
        self.assertTrue(self.does_log_exist("mycluster-1"))
        self.assertTrue(self.does_log_exist("mycluster-2"))

        # TODO: uncomment after audit log for clusters will have fixed
        # samples = [
        #     ("CREATE DATABASE audit_foo", True)
        #     ]
        # self.assertIsNone(self.verify_log_data("mycluster-0", self.add_data_timestamp, samples))

        # samples = [
        #     ("SHOW PROFILES", True)
        #     ]
        # self.assertIsNone(self.verify_log_data("mycluster-1", self.add_data_timestamp, samples))

        # samples = [
        #     ("SHOW SCHEMAS", True)
        #     ]
        # self.assertIsNone(self.verify_log_data("mycluster-2", self.add_data_timestamp, samples))


    def test_9_destroy(self):
        self.destroy_cluster()
