# Copyright (c) 2021, 2024 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from time import time, sleep
from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS
from .cluster_t import check_all
import os
import configparser

# force the same namespace which is hardcoded in certificates generated with tests/data/ssl/make_certs.sh
CLUSTER_SSL_NAMESPACE = 'cluster-ssl'

def check_verify_ca(self, ns, pod, port, ca, expected_host):
    try:
        # TODO:
        # use_pure is a work-around for combination of bug#35195287 in router
        # and bug#35233031 in c/Python. Once either is fixed this can be removed
        with mutil.MySQLPodSession(ns, pod, "root", "sakila", port=port, ssl_ca=ca, ssl_verify_cert=True, use_pure=True) as s:
            host = s.query_sql("select @@global.hostname").fetch_one()[0]

            self.assertEqual(expected_host, host, f"connect VERIFY_CA {pod}:{port}")
    except Exception as e:
        import traceback
        traceback.print_exc()
        self.assertFalse(e, f"connect VERIFY_CA {pod}:{port}")


def check_connect_via_operator_pod(self, address, ca, ssl_mode):
    # get name of the operator pod
    operator_pod = kutil.ls_po("mysql-operator")[0]["NAME"]

    # create a tmpfile with the CA in the operator pod
    if ca:
        kutil.cat_in("mysql-operator", [operator_pod, "mysql-operator"], "/tmp/testca.pem", open(ca).read())

    cmd = ['env', 'MYSQLSH_PROMPT_THEME=', 'mysqlsh', '--sql', '--tabbed',
            f'root:sakila@{address}', '--mysql',
            f'--ssl-mode={ssl_mode}']
    if ca:
        cmd += ['--ssl-ca=/tmp/testca.pem']
    cmd += ["-e select 'CONNECT_OK'"]

    r = kutil.exec("mysql-operator", operator_pod, cmd)
    self.assertIn("CONNECT_OK", r.stdout.decode("utf-8"), address)


def check_ssl(self, ns, pod, ca=None, crl=None, ssl_cert_days=None, check_gr_accounts: bool = True):
    self_signed = not ca or "/" not in ca

    with mutil.MySQLPodSession(ns, pod, "root", "sakila") as s:
        row = s.query_sql("select @@global.ssl_ca, @@global.ssl_capath, @@global.ssl_cert, @@global.ssl_crl, @@global.ssl_crlpath, @@global.ssl_key").fetch_one()

        if not self_signed:
            self.assertEqual("/etc/mysql-ssl/ca.pem", row[0], f"{pod}: ssl_ca")
            self.assertFalse(row[1], f"{pod}: ssl_capath")
            self.assertEqual("/etc/mysql-ssl/tls.crt", row[2], f"{pod}: ssl_cert")
            self.assertEqual("/etc/mysql-ssl/crl.pem" if crl else None, row[3], f"{pod}: ssl_crl")
            self.assertFalse(row[4], f"{pod}: ssl_crlpath")
            self.assertEqual("/etc/mysql-ssl/tls.key", row[5], f"{pod}: ssl_key")
        else:
            self.assertEqual("ca.pem", row[0], f"{pod}: ssl_ca")
            self.assertFalse(row[1], f"{pod}: ssl_capath")
            self.assertEqual("server-cert.pem", row[2], f"{pod}: ssl_cert")
            self.assertFalse(row[3], f"{pod}: ssl_crl")
            self.assertFalse(row[4], f"{pod}: ssl_crlpath")
            self.assertEqual("server-key.pem", row[5], f"{pod}: ssl_key")

        row = s.query_sql("""select @@global.group_replication_ssl_mode,
                            @@global.group_replication_recovery_use_ssl,
                            @@global.group_replication_recovery_ssl_verify_server_cert,
                            @@global.group_replication_recovery_ssl_ca,
                            @@global.group_replication_recovery_ssl_capath,
                            @@global.group_replication_recovery_ssl_cert,
                            @@global.group_replication_recovery_ssl_crl,
                            @@global.group_replication_recovery_ssl_crlpath,
                            @@global.group_replication_recovery_ssl_key""").fetch_one()

        self.assertEqual("REQUIRED" if self_signed else "VERIFY_IDENTITY", row[0], f"{pod}: group_replication_ssl_mode")
        self.assertEqual(1, row[1], f"{pod}: group_replication_recovery_use_ssl")
        self.assertEqual(0 if self_signed else 1, row[2], f"{pod}: group_replication_recovery_ssl_verify_server_cert")
        self.assertEqual("" if self_signed else "/etc/mysql-ssl/ca.pem", row[3], f"{pod}: group_replication_recovery_ssl_ca")
        self.assertFalse(row[4], f"{pod}: group_replication_recovery_ssl_capath")
        self.assertEqual("" if self_signed else "/etc/mysql-ssl/tls.crt", row[5], f"{pod}: group_replication_recovery_ssl_cert")
        self.assertEqual("" if self_signed else "/etc/mysql-ssl/tls.key", row[8], f"{pod}: group_replication_recovery_ssl_key")

        self.assertFalse(row[6], f"{pod}: group_replication_recovery_ssl_crl")
        self.assertFalse(row[7], f"{pod}: group_replication_recovery_ssl_crlpath")

        if ssl_cert_days:
            # test SSL certificates are created with different durations until expiration, so we use that to check if the right cert is loaded
            days = s.query_sql("""select datediff(
                        str_to_date((select variable_value from performance_schema.global_status where variable_name='Ssl_server_not_after'), "%b %d %T %Y GMT"),
                        str_to_date((select variable_value from performance_schema.global_status where variable_name='Ssl_server_not_before'), "%b %d %T %Y GMT")) as days;""").fetch_one()[0]
            self.assertEqual(ssl_cert_days, int(days), "certificate duration")

    if not self_signed:
        capath = os.path.join(tutil.g_test_data_dir, ca)
        # check connecting to server with VERIFY_CA (via proxy/portfw)
        check_verify_ca(self, ns, pod, 3306, capath, expected_host=pod)

        # check connecting to server with VERIFY_IDENTITY and CA directly from operator pod
        check_connect_via_operator_pod(self, f"{pod}.mycluster-instances.{ns}.svc.cluster.local:3306", capath, ssl_mode="VERIFY_CA")
        check_connect_via_operator_pod(self, f"{pod}.mycluster-instances.{ns}.svc.cluster.local:3306", capath, ssl_mode="VERIFY_IDENTITY")

        if check_gr_accounts:
            cluster_info = kutil.get_ic(self.ns, "mycluster")["metadata"]["annotations"]["mysql.oracle.com/cluster-info"]
            print(cluster_info)
            with mutil.MySQLPodSession(ns, pod, "root", "sakila") as s:
                print(s.query_sql("""SELECT User, ssl_type, x509_issuer, x509_subject FROM mysql.user
                                     WHERE User like "mysql_innodb_cluster_%" """).fetch_all())
                row = s.query_sql("""SELECT COUNT(*) as tls_gr_user_count FROM mysql.user
                                    WHERE ssl_type="SPECIFIED"
                                    AND x509_issuer != "0x"
                                    AND x509_subject != "0x"
                                    AND User like "mysql_innodb_cluster_%" """).fetch_one()
                self.assertEqual(self.instances, row[0])
    else:
        check_connect_via_operator_pod(self, f"{pod}.mycluster-instances.{ns}.svc.cluster.local:3306", None, ssl_mode="REQUIRED")


def check_router_ssl(self, ns, pod, ca=None, has_cert=False, crl=None):
    # a temporary patch due to timing issues in router - it may report status 'Running' before
    # some stuff checked in this routine is ready to verify
    sleep(3)

    # check router config file
    router_conf = kutil.cat(ns, pod, "/tmp/mysqlrouter/mysqlrouter.conf")

    conf = configparser.ConfigParser()
    conf.read_string(router_conf.decode("utf-8"))

    if has_cert:
        self.assertEqual("/router-ssl/key/tls.crt", conf["DEFAULT"]["client_ssl_cert"])
        self.assertEqual("/router-ssl/key/tls.key", conf["DEFAULT"]["client_ssl_key"])
        self.assertEqual("PREFERRED", conf["DEFAULT"]["client_ssl_mode"])

    self.assertEqual("/router-ssl/ca/ca.pem", conf["DEFAULT"]["server_ssl_ca"])
    self.assertEqual("PREFERRED", conf["DEFAULT"]["server_ssl_mode"])
    self.assertEqual("VERIFY_IDENTITY", conf["DEFAULT"]["server_ssl_verify"])

    # This won't be set with router 8.0.29, it knows about CA anyways
    # re-evaluate when router fixed bug #33996132
    #self.assertEqual("/router-ssl/ca.pem", conf["metadata_cache:mycluster"]["ssl_ca"])

    if ca:
        capath = os.path.join(tutil.g_test_data_dir, ca)

        # check connecting to router with VERIFY_CA (via proxy/portfw)
        # VERIFY_IDENTITY won't work in this case, since the proxy acts as a mitm
        check_verify_ca(self, ns, pod, 6446, capath, expected_host="mycluster-0")

        # check connecting to router with VERIFY_CA directly from operator pod to the service
        check_connect_via_operator_pod(self, f"mycluster.{ns}.svc.cluster.local:6446", capath, ssl_mode="VERIFY_CA")
        # VERIFY_IDENTITY doesn't work because we're connecting to the service
        #check_connect_via_operator_pod(self, f"mycluster.{ns}.svc.cluster.local:6446", capath, ssl_mode="VERIFY_IDENTITY")

class ClusterSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    instances = 2 # adapt test_2_modify_ssl_certs() and test_3_modify_ssl_certs_and_ca() when instances is different than 2
    routers = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    def test_0_create_secrets(self):
        kutil.create_ssl_ca_secret(self.ns, "mycluster-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
        kutil.create_ssl_cert_secret(self.ns, "mycluster-tls",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

    def test_1_create_cluster_with_ssl(self):
        """
        Create cluster with certificates for server only using default secret
        names.
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {self.instances}
  router:
    instances: {self.routers}
  secretName: mypwds
  edition: community
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Pending")

        # The deployment starts with one RS and zero routers, which are updated once the IC is up and running
        router_rs_pre = kutil.ls_rs(self.ns, pattern="mycluster-router-.*")
        self.assertEqual(len(router_rs_pre), self.routers)
        self.assertEqual(router_rs_pre[0]['DESIRED'], '0')
        self.assertEqual(router_rs_pre[0]['CURRENT'], '0')
        self.assertEqual(router_rs_pre[0]['READY'], '0')

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE", self.instances)

        self.wait_routers("mycluster-router-.*", self.routers)

        router_rs_post = kutil.ls_rs(self.ns, pattern="mycluster-router-.*")
        self.assertEqual(len(router_rs_post), self.routers)
        self.assertEqual(router_rs_post[0]['NAME'], router_rs_pre[0]['NAME'])
        self.assertEqual(router_rs_post[0]['DESIRED'], str(self.routers))
        self.assertEqual(router_rs_post[0]['CURRENT'], str(self.routers))
        self.assertEqual(router_rs_post[0]['READY'], str(self.routers))

        for instance in range(0, self.instances):
            with mutil.MySQLPodSession(self.ns, f"mycluster-{instance}", "root", "sakila") as s:
                s.exec_sql("set global max_connect_errors=10000")

        check_all(self, self.ns, "mycluster", instances=self.instances, routers=self.routers, primary=0)

        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/ca.pem", ssl_cert_days=3650)

    def wait_tls_changed(self, s, before):
        for _ in range(120):
            after = s.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]
            if after != before:
                break
            sleep(5)
        else:
            self.assertFalse(1, "timeout waiting for tls reload")

    def test_2_modify_ssl_certs(self):
        """
        Change server certificate pair
        CA is the same, so this is straightforward
        """
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0, mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s1:
            before = s0.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]

            kutil.delete_secret(self.ns, "mycluster-tls")
            kutil.create_ssl_cert_secret(self.ns, "mycluster-tls",
                os.path.join(tutil.g_test_data_dir, "ssl/out/server2-cert.pem"),
                os.path.join(tutil.g_test_data_dir, "ssl/out/server2-key.pem"))

            self.wait_tls_changed(s0, before)
            self.wait_tls_changed(s1, before)

        check_all(self, self.ns, "mycluster", instances=self.instances, routers=self.routers, primary=0)

        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/ca.pem", ssl_cert_days=7300)

    def test_3_modify_ssl_certs_and_ca(self):
        """
        Change server certificate and CA
        CA changes, so if no downtime is wanted, both CAs need to be made
        available at the same time.
        """
        old_routers = kutil.ls_pod(self.ns, "mycluster-router-.*")
        self.assertEqual(len(old_routers), self.routers)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s0, mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s1:
            before = s0.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]

            kutil.delete_secret(self.ns, "mycluster-ca")
            kutil.create_ssl_ca_secret(self.ns, "mycluster-ca",
                os.path.join(tutil.g_test_data_dir, "ssl/out/cab.pem"))

            kutil.delete_secret(self.ns, "mycluster-tls")
            kutil.create_ssl_cert_secret(self.ns, "mycluster-tls",
                os.path.join(tutil.g_test_data_dir, "ssl/out/serverb-cert.pem"),
                os.path.join(tutil.g_test_data_dir, "ssl/out/serverb-key.pem"))

            self.wait_tls_changed(s0, before)
            self.wait_tls_changed(s1, before)

        # before verifying the new router, ensure the old one is gone
        self.wait_pod_gone(old_routers[0]["NAME"])
        routers = self.wait_routers("mycluster-router-.*", self.routers)

        check_all(self, self.ns, "mycluster", instances=self.instances, routers=self.routers, primary=0)

        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/cab.pem", ssl_cert_days=10920)

        # routers are setup without certificates, so connect without VERIFY_
        for r in routers:
            check_router_ssl(self, self.ns, r)

        # kutil.delete_ic(self.ns, "mycluster")
        # self.wait_pod_gone("mycluster-1")
        # self.wait_pod_gone("mycluster-0")
        # self.wait_ic_gone("mycluster")


    def test_4_add_crl(self):
        old_routers = kutil.ls_pod(self.ns, "mycluster-router-.*")
        self.assertEqual(len(old_routers), self.routers)

        kutil.delete_secret(self.ns, "mycluster-ca")
        kutil.create_ssl_ca_secret(self.ns, "mycluster-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/cab.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/crl.pem"))

        for instance in range(0, self.instances):
            with mutil.MySQLPodSession(self.ns, f"mycluster-{instance}", "root", "sakila") as s:
                def check_tls_loaded():
                    return s.query_sql("select @@global.ssl_crl").fetch_one()[0]

                self.wait(check_tls_loaded, delay=5, timeout=5*60)

        # before verifying the new router, ensure the old one is gone
        self.wait_pod_gone(old_routers[0]["NAME"])
        routers = self.wait_routers("mycluster-router-.*", self.routers)

        check_all(self, self.ns, "mycluster", instances=self.instances, routers=self.routers, primary=0)

        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/cab.pem", crl="ssl/out/crl.pem", ssl_cert_days=10920)

        # routers are setup without certificates, so connect without VERIFY_
        for r in routers:
            check_router_ssl(self, self.ns, r)

        kutil.delete_ic(self.ns, "mycluster")

        for instance in reversed(range(0, self.instances)):
            self.wait_pod_gone(f"mycluster-{instance}")
        self.wait_ic_gone("mycluster")


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        for instance in reversed(range(0, self.instances)):
            self.wait_pod_gone(f"mycluster-{instance}")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


class ClusterNoSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    instances = 1
    routers = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()


    def test_0_create_secrets(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")


    def test_1_create_cluster_missing_ssl(self):
        """
        Create cluster without certificates and without tlsUseSelfSigned
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {self.instances}
  router:
    instances: {self.routers}
  secretName: mypwds
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "PENDING", 0)

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Pending")

        SECRET_TLS_NOT_FOUND='secret "mycluster-tls" not found'
        SECRET_CA_NOT_FOUND='secret "mycluster-ca" not found'
        def check_error():
            out = kutil.describe_po(self.ns, "mycluster-0")
            matched_lines=''
            for line in out.split('\n'):
                if SECRET_TLS_NOT_FOUND in line or SECRET_TLS_NOT_FOUND in line:
                    matched_lines += f"{line}'\n"
            return matched_lines

        # cluster will be stuck at PENDING because of the missing secret
        matched_lines = self.wait(check_error)
        self.assertIn(SECRET_TLS_NOT_FOUND, matched_lines)
        self.assertIn(SECRET_CA_NOT_FOUND, matched_lines)


    def test_1_create_cluster_missing_ssl_recover(self):
        """
        Recover from no-certificates by creating the missing certs
        """
        kutil.create_ssl_ca_secret(self.ns, "mycluster-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
        kutil.create_ssl_cert_secret(self.ns, "mycluster-tls",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")
        self.wait_ic("mycluster", "ONLINE", self.instances)
        self.wait_routers("mycluster-router-.*", self.routers)

        check_all(self, self.ns, "mycluster", instances=self.instances, routers=self.routers, primary=0)

        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/ca.pem", ssl_cert_days=3650)

        kutil.delete_ic(self.ns, "mycluster")
        for instance in reversed(range(0, self.instances)):
            self.wait_pod_gone(f"mycluster-{instance}")
        self.wait_ic_gone("mycluster")


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        for instance in reversed(range(0, self.instances)):
            self.wait_pod_gone(f"mycluster-{instance}")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


class ClusterAddSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    instances = 1 # adapt test_2_add_tls() ()
    routers = 1

    def wait_tls_changed(self, s, before):
        for _ in range(60):
            after = s.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]
            if after != before:
                break
            sleep(3)
        else:
            self.assertFalse(1, "timeout waiting for tls reload")

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    def test_0_create_secrets(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

    def test_1_create_cluster_without_ssl(self):
        """
        Create cluster with default certificates.
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {self.instances}
  router:
    instances: {self.routers}
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE", self.instances)

        self.wait_routers("mycluster-router-.*", self.routers)

        # check for defaults
        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ca.pem", crl="", ssl_cert_days=3650)


    def test_2_add_tls(self):
        for instance in range(0, self.instances):
            with mutil.MySQLPodSession(self.ns, f"mycluster-{instance}", "root", "sakila") as s:
                before = s.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]

        kutil.create_ssl_ca_secret(self.ns, "mycluster-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
        kutil.create_ssl_cert_secret(self.ns, "mycluster-tls",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

        kutil.patch_ic(self.ns, "mycluster", {
            "spec": {
                "tlsUseSelfSigned": False
            }
        }, type="merge")

        # we need to manually restart the cluster for changes of this kind to get applied
        kutil.restart_sts(self.ns, "mycluster")

        self.wait_pod("mycluster-0", "Pending")
        self.wait_ic("mycluster", "OFFLINE", 0)

        self.wait_pod("mycluster-0", "Running")
        self.wait_ic("mycluster", "ONLINE", 1)

        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            self.wait_tls_changed(s, before)

        check_all(self, self.ns, "mycluster", instances=1, routers=1, primary=0)

        for instance in range(0, self.instances):
            # Because the cluster was created with self signed then no x509 was used
            # for the GR accounts. After moving to non-self signed the cluster option
            # cannot be changed, Shell doesn't provide means for that, so the accounts
            # will stay PASSWORD authenticated for the time being of the cluster.
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/ca.pem", crl=None, ssl_cert_days=3650, check_gr_accounts=False)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


class ClusterRouterSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    instances = 1
    routers = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    def test_0_create_secrets(self):
        kutil.create_ssl_ca_secret(self.ns, "ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
        kutil.create_ssl_cert_secret(self.ns, "server-ssl",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))
        kutil.create_ssl_cert_secret(self.ns, "router-ssl",
            os.path.join(tutil.g_test_data_dir, "ssl/out/router-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/router-key.pem"))

        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

    def test_1_create_cluster_with_router_ssl(self):
        """
        Create cluster with certificates for server and router
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {self.instances}
  router:
    instances: {self.routers}
    tlsSecretName: router-ssl
  secretName: mypwds
  tlsCASecretName: ca
  tlsSecretName: server-ssl
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_ic("mycluster", "ONLINE", self.instances)

        routers = self.wait_routers("mycluster-router-.*", self.routers)

        check_all(self, self.ns, "mycluster", instances=self.instances, routers=self.routers, primary=0)

        for instance in range(0, self.instances):
            check_ssl(self, self.ns, f"mycluster-{instance}", ca="ssl/out/ca.pem")

        self.assertEqual(self.routers, len(routers))

        for rname in routers:
            check_router_ssl(self, self.ns, rname, ca="ssl/out/ca.pem", has_cert=True)

        kutil.delete_ic(self.ns, "mycluster")
        for instance in reversed(range(0, self.instances)):
            self.wait_pod_gone(f"mycluster-{instance}")
        self.wait_ic_gone("mycluster")



    def test_2_modify_ssl_certs(self):
        """
        Change server and router certs
        """
        pass


    def test_3_recover_from_bad_certs(self):
        pass


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        for instance in reversed(range(0, self.instances)):
            self.wait_pod_gone(f"mycluster-{instance}")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")

