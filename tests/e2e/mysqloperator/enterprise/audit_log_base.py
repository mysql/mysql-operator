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
    routers_count = 2

    audit_log_filename = 'audit.json'

    default_filter_user = '%'
    default_filter_host = ''
    default_filter_label = 'log_all'
    default_filter = '{"filter": {"log": true}}'

    custom_filter_user = user
    custom_filter_host = '%'
    custom_filter_label = 'custom_log'
    custom_filter = '{"filter": {"class": [{"name": "table_access"}]}}'


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
        instances: {self.routers_count}
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

        self.wait_ic("mycluster", "ONLINE", num_online=self.cluster_size)

        self.wait_routers("mycluster-router-*", self.routers_count)

        check_all(self, self.ns, "mycluster",
            instances=self.cluster_size, routers=self.routers_count, primary=0)


    def install_plugin_on_primary(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            # https://dev.mysql.com/doc/refman/8.0/en/audit-log-installation.html
            # enable logging and assign it to the default account:
            install_script_dir = s.query_sql("SHOW VARIABLES LIKE 'lc_messages_dir'").fetch_one()[1]
            install_script_path = os.path.join(install_script_dir, 'audit_log_filter_linux_install.sql')
            cmd = ['mysql', '-u', self.user, f"--password={self.password}", '-e', f"source {install_script_path}"]
            kutil.exec(self.ns, (instance, "mysql"), cmd)


    def install_plugin_on_secondary(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            s.exec_sql("SET GLOBAL super_read_only=off")
            s.exec_sql("INSTALL PLUGIN audit_log SONAME 'audit_log.so'")
            s.exec_sql("SET GLOBAL super_read_only=on")


    def set_filter(self, instance, user, filter_name, filter):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql(f"SELECT audit_log_filter_set_filter('{filter_name}', '{filter}')").fetch_one()
            self.assertEqual(res, ('OK',))
            res = s.query_sql(f"SELECT audit_log_filter_set_user('{user}', '{filter_name}')").fetch_one()
            self.assertEqual(res, ('OK',))

    def set_default_filter(self, instance):
        self.set_filter(instance, self.default_filter_user, self.default_filter_label, self.default_filter)

    def set_custom_filter(self, instance):
        self.set_filter(instance, self.custom_filter_user, self.custom_filter_label, self.custom_filter)


    def remove_filter(self, instance, user, filter_name):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql(f"SELECT audit_log_filter_remove_filter('{filter_name}')").fetch_one()
            self.assertEqual(res, ('OK',))
            res = s.query_sql(f"SELECT audit_log_filter_remove_user('{user}')").fetch_one()
            self.assertEqual(res, ('OK',))

    def remove_default_filter(self, instance):
        self.remove_filter(instance, self.default_filter_user, self.default_filter_label)

    def remove_custom_filter(self, instance):
        self.remove_filter(instance, self.custom_filter_user, self.custom_filter_label)


    def has_filter(self, instance, user, host, filter_name, filter):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql("SELECT * FROM audit_log_filter").fetch_all()
            print(res)
            if not res:
                return False
            if res != [(filter_name, filter)]:
                return False

            res = s.query_sql("SELECT * FROM audit_log_user").fetch_all()
            print(res)
            if not res:
                return False
            if res != [(user, host, filter_name)]:
                return False

            return True

    def has_default_filter_set(self, instance):
        return self.has_filter(instance, self.default_filter_user, self.default_filter_host, self.default_filter_label, self.default_filter)

    def has_custom_filter_set(self, instance):
        return self.has_filter(instance, self.custom_filter_user, self.custom_filter_host, self.custom_filter_label, self.custom_filter)


    def get_log_path(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            datadir = s.query_sql("SHOW VARIABLES LIKE 'datadir'").fetch_one()[1]
            res = s.query_sql("SHOW VARIABLES LIKE 'audit_log_file'").fetch_one()
            if not res:
                return None
            audit_log_fname = res[1]
            return os.path.join(datadir, audit_log_fname)


    def does_log_exist(self, instance):
        audit_log_path = self.get_log_path(instance)
        if not audit_log_path:
            return False
        cmd = ['ls', '-l', audit_log_path]
        ls_res = kutil.execp(self.ns, (instance, "mysql"), cmd)
        print(str(ls_res))

        # cmd = ['cat', audit_log_path]
        # cat_res = kutil.execp(self.ns, (instance, "mysql"), cmd)
        # print(str(cat_res))

        return self.audit_log_filename in str(ls_res)


    def get_log_data(self, instance, timestamp):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            query = ('SELECT JSON_PRETTY(CONVERT(audit_log_read(\'{ "start": { "timestamp": \"'
                + timestamp + "\"} }') USING UTF8MB4))")
            data = s.query_sql(query)
            rows = data.fetch_all()
            if not rows:
                return None
            self.assertEqual(len(rows), 1)
            return str(rows[0])


    def destroy_cluster(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
