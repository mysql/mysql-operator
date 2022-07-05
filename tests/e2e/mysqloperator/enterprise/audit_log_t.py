# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import os
import unittest
from e2e.mysqloperator.cluster.cluster_t import check_all
from setup.config import g_ts_cfg
from utils import auxutil
from utils import kutil
from utils import mutil
from utils import tutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log

@unittest.skipIf(g_ts_cfg.enterprise_skip, "Enterprise test cases are skipped")
class AuditLog(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    user = 'root'
    password = 'sakila'
    cluster_size = 3
    audit_log_filename = 'audit.json'
    add_data_timestamp = None

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()


    def test_0_create(self):
        kutil.create_default_user_secrets(self.ns)

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
    name: mycluster
spec:
    instances: {self.cluster_size}
    router:
        instances: 2
    secretName: mypwds
    edition: enterprise
    tlsUseSelfSigned: true
    datadirVolumeClaimTemplate:
        accessModes: [ "ReadWriteOnce" ]
        resources:
            requests:
                storage: 2Gi
    mycnf: |
        [mysqld]
        loose_audit_log_file={self.audit_log_filename}
        loose_audit_log_format=JSON
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", num_online=3)

        self.wait_routers("mycluster-router-*", 2)

        check_all(self, self.ns, "mycluster",
                    instances=3, routers=2, primary=0)


    def test_1_init(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            # https://dev.mysql.com/doc/refman/8.0/en/audit-log-installation.html
            # enable logging and assign it to the default account:
            install_script_dir = s.query_sql("SHOW VARIABLES LIKE 'lc_messages_dir'").fetch_one()[1]
            install_script_path = os.path.join(install_script_dir, 'audit_log_filter_linux_install.sql')
            cmd = ['mysql', '-u', self.user, f"--password={self.password}", '-e', f"source {install_script_path}"]
            kutil.exec(self.ns, (f"mycluster-0", "mysql"), cmd)

            res = s.query_sql("SELECT audit_log_filter_set_filter('log_all', '{ \"filter\": { \"log\": true } }')").fetch_one()
            self.assertEqual(res, ('OK',))
            res = s.query_sql("SELECT audit_log_filter_set_user('%', 'log_all')").fetch_one()
            self.assertEqual(res, ('OK',))


    def test_2_add_data(self):
        self.__class__.add_data_timestamp = auxutil.utctime()
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            s.exec_sql("CREATE SCHEMA audit_foo")
            s.exec_sql("CREATE TABLE audit_foo.t (id INT NOT NULL, name VARCHAR(20), PRIMARY KEY(id))")
            s.exec_sql('INSERT INTO audit_foo.t VALUES (123456, "first_audit")')
            s.exec_sql('INSERT INTO audit_foo.t VALUES (654321, "second_audit")')


    def test_3_verify_log(self):
        with mutil.MySQLPodSession(self.ns, "mycluster-0", self.user, self.password) as s:
            datadir = s.query_sql("SHOW VARIABLES LIKE 'datadir'").fetch_one()[1]
            audit_log_fname = s.query_sql("SHOW VARIABLES LIKE 'audit_log_file'").fetch_one()[1]
            query = ('SELECT JSON_PRETTY(CONVERT(audit_log_read(\'{ "start": { "timestamp": \"'
                + self.__class__.add_data_timestamp + "\"} }') USING UTF8MB4))")
            records = s.query_sql(query)

            rows = records.fetch_all()
            self.assertEqual(len(rows), 1)
            recordStr = str(rows[0])
            self.assertIn("CREATE SCHEMA audit_foo", recordStr)
            self.assertIn("CREATE TABLE audit_foo.t (id INT NOT NULL, name VARCHAR(20), PRIMARY KEY(id))", recordStr)
            self.assertIn('INSERT INTO audit_foo.t VALUES (123456, \\\\"first_audit\\\\")', recordStr)
            self.assertIn('INSERT INTO audit_foo.t VALUES (654321, \\\\"second_audit\\\\")', recordStr)

        audit_log_path = os.path.join(datadir, audit_log_fname)
        cmd = ['ls', '-l', audit_log_path]
        ls_res = kutil.execp(self.ns, (f"mycluster-0", "mysql"), cmd)
        self.assertIn(audit_log_fname, str(ls_res))


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
