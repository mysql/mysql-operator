# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from e2e.mysqloperator.enterprise.audit_log_base import AuditLogBase
from setup.config import g_ts_cfg
from utils import auxutil
from utils import kutil
from utils import mutil

# test the audit log on the 3-instance cluster
# - set a filter, and verify it is set in the whole cluster
# - kill primary instance, wait for another primary to be set
# - set a new filter (via a new primary), and verify it is replicated to replicas
# - add some data, and verify they are logged
@unittest.skipIf(g_ts_cfg.enterprise_skip or g_ts_cfg.audit_log_skip, "Enterprise Audit Log test cases are skipped")
class AuditLogChangePrimaryAndFilter(AuditLogBase):
    add_data_timestamp = None
    primary_instance = "mycluster-0"
    secondary_instances = []

    def test_0_create(self):
        self.create_cluster()


    def test_1_init(self):
        self.install_plugin_on_primary(self.primary_instance)
        self.set_custom_filter(self.primary_instance)
        self.install_plugin_on_secondary("mycluster-1")
        self.install_plugin_on_secondary("mycluster-2")


    def test_2_change_primary(self):
        kutil.kill(self.ns, (self.primary_instance, "mysql"), 11, 1)

        # wait till instance is gone
        self.wait_ic("mycluster", ["ONLINE_PARTIAL", "ONLINE_UNCERTAIN"], self.cluster_size - 1)

        # wait till cluster is restored
        self.wait_ic("mycluster", "ONLINE", self.cluster_size)
        self.wait_routers("mycluster-router-*", self.routers_count)

        new_instance_primary = self.get_primary_instance("mycluster-1", self.user, self.password)
        self.assertIsNot(new_instance_primary, self.primary_instance)
        self.__class__.primary_instance = new_instance_primary


    def test_3_set_default_filter(self):
        self.assertTrue(self.has_custom_filter_set("mycluster-0"))
        self.assertTrue(self.has_custom_filter_set("mycluster-1"))
        self.assertTrue(self.has_custom_filter_set("mycluster-2"))

        self.remove_custom_filter(self.primary_instance)
        self.set_default_filter(self.primary_instance)


    def test_4_prepare_data(self):
        self.assertTrue(self.has_default_filter_set("mycluster-0"))
        self.assertTrue(self.has_default_filter_set("mycluster-1"))
        self.assertTrue(self.has_default_filter_set("mycluster-2"))

        self.__class__.add_data_timestamp = auxutil.utctime()

        with mutil.MySQLPodSession(self.ns, self.primary_instance, self.user, self.password) as s:
            s.exec_sql("CREATE DATABASE audit_foo")

        self.__class__.secondary_instances = self.get_secondary_instances("mycluster-1", self.user, self.password)
        secondary_instances = self.secondary_instances
        self.assertEqual(len(secondary_instances), 2)

        with mutil.MySQLPodSession(self.ns, secondary_instances[0], self.user, self.password) as s:
            res = s.query_sql("SHOW PLUGINS").fetch_all()
            self.assertIsNotNone(res)

        with mutil.MySQLPodSession(self.ns, secondary_instances[1], self.user, self.password) as s:
            res = s.query_sql("SHOW SCHEMAS").fetch_all()
            self.assertIsNotNone(res)
            s.exec_sql("FLUSH TABLES")

        self.rotate_log(self.primary_instance)


    def test_5_verify_logs(self):
        self.assertTrue(self.does_log_exist(self.primary_instance))
        self.assertTrue(self.does_log_exist(self.secondary_instances[0]))
        self.assertTrue(self.does_log_exist(self.secondary_instances[1]))

        # TODO: uncomment after audit log for clusters will have fixed
        # samples = [
        #     ("CREATE DATABASE audit_foo", True)
        #     ]
        # self.assertIsNone(self.verify_log_data(self.primary_instance, self.add_data_timestamp, samples))

        # samples = [
        #     ("SHOW PLUGINS", True)
        #     ]
        # self.assertIsNone(self.verify_log_data(self.secondary_instances[0], self.add_data_timestamp, samples))

        # samples = [
        #     ("SHOW SCHEMAS", True)
        #     ]
        # self.assertIsNone(self.verify_log_data(self.secondary_instances[1], self.add_data_timestamp, samples))


    def test_9_destroy(self):
        self.destroy_cluster()
