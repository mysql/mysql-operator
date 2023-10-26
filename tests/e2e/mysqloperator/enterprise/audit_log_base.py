# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import os
import time
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
    rotated_audit_log_path = None

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


    def create_cluster(self, audit_log_strategy = 'SYNCHRONOUS'):
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
        loose_audit_log_strategy={audit_log_strategy}
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
            kutil.exec(self.ns, [instance, "mysql"], cmd)


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
            s.exec_sql("FLUSH TABLES")

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
            s.exec_sql("FLUSH TABLES")

    def remove_default_filter(self, instance):
        self.remove_filter(instance, self.default_filter_user, self.default_filter_label)

    def remove_custom_filter(self, instance):
        self.remove_filter(instance, self.custom_filter_user, self.custom_filter_label)


    def has_filter(self, instance, user, host, filter_name, filter):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql("SELECT * FROM audit_log_filter").fetch_all()
            self.logger.debug(res)
            if not res:
                return False
            if res != [(filter_name, filter)]:
                return False

            res = s.query_sql("SELECT * FROM audit_log_user").fetch_all()
            self.logger.debug(res)
            if not res:
                return False
            if res != [(user, host, filter_name)]:
                return False

            return True

    def has_default_filter_set(self, instance):
        return self.has_filter(instance, self.default_filter_user, self.default_filter_host, self.default_filter_label, self.default_filter)

    def has_custom_filter_set(self, instance):
        return self.has_filter(instance, self.custom_filter_user, self.custom_filter_host, self.custom_filter_label, self.custom_filter)


    def rotate_log(self, instance):
        with mutil.MySQLPodSession(self.ns, instance, self.user, self.password) as s:
            res = s.query_sql("SELECT audit_log_rotate()").fetch_one()
            self.logger.debug(res)
            rotated_log_filename = res[0]
            datadir = s.query_sql("SHOW VARIABLES LIKE 'datadir'").fetch_one()[1]
            self.__class__.rotated_audit_log_path = os.path.join(datadir, rotated_log_filename)
            self.logger.debug(self.__class__.rotated_audit_log_path)

    def get_rotated_log_data(self, instance):
        cmd = ['cat', self.__class__.rotated_audit_log_path]
        return str(kutil.execp(self.ns, [instance, "mysql"], cmd))


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
        ls_res = kutil.execp(self.ns, [instance, "mysql"], cmd)
        self.logger.info(str(ls_res))

        # cmd = ['cat', audit_log_path]
        # cat_res = kutil.execp(self.ns, [instance, "mysql"], cmd)
        # self.logger.debug(str(cat_res))

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

    def filter_log_data(self, log_data, samples):
        issues = []
        for sample in samples:
            sequence = sample[0]
            is_expected = sample[1]
            if is_expected:
                if sequence not in log_data:
                    issues.append(f"expected sequence '{sequence}' not found")
            else:
                if sequence in log_data:
                    issues.append(f"unexpected sequence '{sequence}' found")

        if issues:
            return f"Following issues: '[{'; '.join(issues)}] met in log {log_data}"

        return None

    def verify_rotated_log_data(self, instance, samples):
        log_data = self.get_rotated_log_data(instance)
        issues = self.filter_log_data(log_data, samples)
        if issues:
            return f"Rotated log {self.__class__.rotated_audit_log_path}: {issues}"
        return None

    def verify_current_log_data(self, instance, timestamp, samples):
        MAX_TRIALS = 10
        issues = None
        for i in range(MAX_TRIALS):
            log_data = self.get_log_data(instance, timestamp)
            issues = self.filter_log_data(log_data, samples)
            if not issues:
                break
            time.sleep(3)

        return issues

    def verify_log_data(self, instance, timestamp, samples):
        rotated_log_issues = self.verify_rotated_log_data(instance, samples)
        if not rotated_log_issues:
            return None

        current_log_issues = self.verify_current_log_data(instance, timestamp, samples)
        if not current_log_issues:
            return None

        return current_log_issues + rotated_log_issues


    def destroy_cluster(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
