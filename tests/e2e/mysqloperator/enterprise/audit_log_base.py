# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import os
from e2e.mysqloperator.cluster.cluster_t import check_all
from utils import kutil
from utils import mutil
from utils import tutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log

class AuditLogBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    user = 'root'
    password = 'sakila'
    cluster_size = 3
    audit_log_filename = 'audit.json'

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()


    def create_cluster(self):
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
        # plugin-load-add=audit_log=audit_log.so

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", num_online=3)

        self.wait_routers("mycluster-router-*", 2)

        check_all(self, self.ns, "mycluster",
                    instances=3, routers=2, primary=0)


    def install_plugin_on_primary(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            # https://dev.mysql.com/doc/refman/8.0/en/audit-log-installation.html
            # enable logging and assign it to the default account:
            install_script_dir = s.query_sql("SHOW VARIABLES LIKE 'lc_messages_dir'").fetch_one()[1]
            install_script_path = os.path.join(install_script_dir, 'audit_log_filter_linux_install.sql')
            print(install_script_path)

            cmd = ['mysql', '-u', self.user, f"--password={self.password}", '-e', f"source {install_script_path}"]
            kutil.exec(self.ns, (instance, "mysql"), cmd)


    def install_plugin_on_secondary(self, instance):
        kutil.exec(self.ns, (instance, "mysql"), "SET GLOBAL super_read_only=off")
        kutil.exec(self.ns, (instance, "mysql"), "INSTALL PLUGIN audit_log SONAME 'audit_log.so")
        kutil.exec(self.ns, (instance, "mysql"), "SET GLOBAL super_read_only=on")


    def set_filter(self, instance, user, filter, log):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql("SELECT audit_log_filter_set_filter('log_all', '{ \"filter\": { \"log\": true } }')").fetch_one()
            self.assertEqual(res, ('OK',))
            res = s.query_sql("SELECT audit_log_filter_set_user('%', 'log_all')").fetch_one()
            self.assertEqual(res, ('OK',))


    def set_filter_on_all(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql("SELECT audit_log_filter_set_filter('log_all', '{ \"filter\": { \"log\": true } }')").fetch_one()
            self.assertEqual(res, ('OK',))
            res = s.query_sql("SELECT audit_log_filter_set_user('%', 'log_all')").fetch_one()
            self.assertEqual(res, ('OK',))


    def get_log_path(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            datadir = s.query_sql("SHOW VARIABLES LIKE 'datadir'").fetch_one()[1]
            audit_log_fname = s.query_sql("SHOW VARIABLES LIKE 'audit_log_file'").fetch_one()[1]
            return os.path.join(datadir, audit_log_fname)


    def does_log_exist(self, instance):
        audit_log_path = self.get_log_path(instance)
        cmd = ['ls', '-l', audit_log_path]
        ls_res = kutil.execp(self.ns, (instance, "mysql"), cmd)
        return self.audit_log_filename in str(ls_res)


    def get_log_data(self, instance, timestamp):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            query = ('SELECT JSON_PRETTY(CONVERT(audit_log_read(\'{ "start": { "timestamp": \"'
                + timestamp + "\"} }') USING UTF8MB4))")
            data = s.query_sql(query)
            rows = data.fetch_all()
            self.assertEqual(len(rows), 1)
            return str(rows[0])
