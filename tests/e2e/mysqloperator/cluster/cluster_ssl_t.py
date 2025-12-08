# Copyright (c) 2021, 2025 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from time import time, sleep
from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.auxutil import isotime
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


def check_connect_via_operator_pod(self, address, ca, ssl_mode, cluster_name):
    # get name of the operator pod
    operator_pod = kutil.ls_po("mysql-operator")[0]["NAME"]

    # create a tmpfile with the CA in the operator pod
    if ca:
        kutil.cat_in("mysql-operator", [operator_pod, "mysql-operator"], f"/tmp/testca-{cluster_name}.pem", open(ca).read())

    cmd = ['env', 'MYSQLSH_PROMPT_THEME=', 'mysqlsh', '--sql', '--tabbed',
            f'root:sakila@{address}', '--mysql',
            f'--ssl-mode={ssl_mode}']
    if ca:
        cmd += [f'--ssl-ca=/tmp/testca-{cluster_name}.pem']
    cmd += ["-e select 'CONNECT_OK'"]

    r = kutil.exec("mysql-operator", operator_pod, cmd)
    self.assertIn("CONNECT_OK", r.stdout.decode("utf-8"), address)


def check_ssl(self: tutil.OperatorTest, ns, pod, ca=None, crl=None, ssl_cert_days=None, check_gr_accounts: bool = True):
    self_signed = not ca or "/" not in ca

    with mutil.MySQLPodSession(ns, pod, "root", "sakila") as s:
        row = s.query_sql("select @@global.ssl_ca, @@global.ssl_capath, @@global.ssl_cert, @@global.ssl_crl, @@global.ssl_crlpath, @@global.ssl_key").fetch_one()

        if not self_signed:
            self.assertEqual("/etc/mysql-ssl/ca/ca.pem", row[0], f"{pod}: ssl_ca")
            self.assertFalse(row[1], f"{pod}: ssl_capath")
            self.assertEqual("/etc/mysql-ssl/key/tls.crt", row[2], f"{pod}: ssl_cert")
            self.assertEqual("/etc/mysql-ssl/ca/crl.pem" if crl else None, row[3], f"{pod}: ssl_crl")
            self.assertFalse(row[4], f"{pod}: ssl_crlpath")
            self.assertEqual("/etc/mysql-ssl/key/tls.key", row[5], f"{pod}: ssl_key")
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
        self.assertEqual("" if self_signed else "/etc/mysql-ssl/ca/ca.pem", row[3], f"{pod}: group_replication_recovery_ssl_ca")
        self.assertFalse(row[4], f"{pod}: group_replication_recovery_ssl_capath")
        self.assertEqual("" if self_signed else "/etc/mysql-ssl/key/tls.crt", row[5], f"{pod}: group_replication_recovery_ssl_cert")
        self.assertEqual("" if self_signed else "/etc/mysql-ssl/key/tls.key", row[8], f"{pod}: group_replication_recovery_ssl_key")

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
        check_connect_via_operator_pod(self, f"{pod}.{self.cluster_name}-instances.{ns}.svc.cluster.local:3306", capath, ssl_mode="VERIFY_CA", cluster_name=self.cluster_name)
        check_connect_via_operator_pod(self, f"{pod}.{self.cluster_name}-instances.{ns}.svc.cluster.local:3306", capath, ssl_mode="VERIFY_IDENTITY", cluster_name=self.cluster_name)

        if check_gr_accounts:
            cluster_info = kutil.get_ic(self.ns, self.cluster_name)["metadata"]["annotations"]["mysql.oracle.com/cluster-info"]
            print(cluster_info)
            with mutil.MySQLPodSession(ns, pod, "root", "sakila") as s:
                print(s.query_sql("""SELECT User, ssl_type, x509_issuer, x509_subject FROM mysql.user
                                     WHERE User like "mysql_innodb_cluster_%" """).fetch_all())
                row = s.query_sql("""SELECT COUNT(*) as tls_gr_user_count FROM mysql.user
                                    WHERE ssl_type="SPECIFIED"
                                    AND x509_issuer != "0x"
                                    AND x509_subject != "0x"
                                    AND User like "mysql_innodb_cluster_%" """).fetch_one()
                self.assertEqual(self.cluster_size, row[0])
    else:
        check_connect_via_operator_pod(self, f"{pod}.{self.cluster_name}-instances.{ns}.svc.cluster.local:3306", None, ssl_mode="REQUIRED", cluster_name=self.cluster_name)


def check_router_ssl(self: tutil.OperatorTest, ns, pod, ca=None, has_cert=False, crl=None):
    # a temporary patch due to timing issues in router - it may report status 'Running' before
    # some stuff checked in this routine is ready to verify
    sleep(10)

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
    #self.assertEqual("/router-ssl/ca.pem", conf[f"metadata_cache:{self.cluster_name}"]["ssl_ca"])

    if ca:
        capath = os.path.join(tutil.g_test_data_dir, ca)

        # check connecting to router with VERIFY_CA (via proxy/portfw)
        # VERIFY_IDENTITY won't work in this case, since the proxy acts as a mitm
        check_verify_ca(self, ns, pod, 6446, capath, expected_host=f"{self.cluster_name}-0")

        # check connecting to router with VERIFY_CA directly from operator pod to the service
        check_connect_via_operator_pod(self, f"{self.cluster_name}.{ns}.svc.cluster.local:6446", capath, ssl_mode="VERIFY_CA", cluster_name=self.cluster_name)
        # VERIFY_IDENTITY doesn't work because we're connecting to the service
        #check_connect_via_operator_pod(self, f"{self.cluster_name}.{ns}.svc.cluster.local:6446", capath, ssl_mode="VERIFY_IDENTITY")


class ClusterSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
#    instances = 2 # adapt test_2_modify_ssl_certs() and test_3_modify_ssl_certs_and_ca() when instances is different than 2
#    routers = 1
    _cluster_size = 2
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)
        cls.cluster_name = "mycluster" # due to precreated TLS certs
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_create_secrets(self):
        kutil.create_ssl_ca_secret(self.ns, f"{self.cluster_name}-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
        kutil.create_ssl_cert_secret(self.ns, f"{self.cluster_name}-tls",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

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
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(f"{self.cluster_name}-0", "Pending")

        # The deployment starts with one RS and zero routers, which are updated once the IC is up and running
        router_rs_pre = kutil.ls_rs(self.ns, pattern=f"{self.cluster_name}-router-.*")
        self.assertEqual(len(router_rs_pre), self.routers_count)
        self.assertEqual(router_rs_pre[0]['DESIRED'], '0')
        self.assertEqual(router_rs_pre[0]['CURRENT'], '0')
        self.assertEqual(router_rs_pre[0]['READY'], '0')

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.wait_routers(f"{self.cluster_name}-router-.*", num_online=self.routers_count)

        router_rs_post = kutil.ls_rs(self.ns, pattern=f"{self.cluster_name}-router-.*")
        self.assertEqual(len(router_rs_post), self.routers_count)
        self.assertEqual(router_rs_post[0]['NAME'], router_rs_pre[0]['NAME'])
        self.assertEqual(router_rs_post[0]['DESIRED'], str(self.routers_count))
        self.assertEqual(router_rs_post[0]['CURRENT'], str(self.routers_count))
        self.assertEqual(router_rs_post[0]['READY'], str(self.routers_count))

        for instance in range(0, self.cluster_size):
            with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-{instance}", "root", "sakila") as s:
                s.exec_sql("set global max_connect_errors=10000")

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/ca.pem", ssl_cert_days=3650)

    def wait_tls_changed(self, s, before):
        for _ in range(120):
            after = s.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]
            if after != before:
                break
            sleep(10)
        else:
            self.assertFalse(1, "timeout waiting for tls reload")

    def test_2_modify_ssl_certs(self):
        """
        Change server certificate pair
        CA is the same, so this is straightforward
        """
        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0, mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s1:
            before = s0.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]

            kutil.delete_secret(self.ns, f"{self.cluster_name}-tls")
            kutil.create_ssl_cert_secret(self.ns, f"{self.cluster_name}-tls",
                os.path.join(tutil.g_test_data_dir, "ssl/out/server2-cert.pem"),
                os.path.join(tutil.g_test_data_dir, "ssl/out/server2-key.pem"))

            self.wait_tls_changed(s0, before)
            self.wait_tls_changed(s1, before)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/ca.pem", ssl_cert_days=7300)

    def test_3_modify_ssl_certs_and_ca(self):
        """
        Change server certificate and CA
        CA changes, so if no downtime is wanted, both CAs need to be made
        available at the same time.
        """
        old_routers = kutil.ls_pod(self.ns, f"{self.cluster_name}-router-.*")
        self.assertEqual(len(old_routers), self.routers_count)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s0, mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-1", "root", "sakila") as s1:
            before = s0.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]

            kutil.delete_secret(self.ns, f"{self.cluster_name}-ca")
            kutil.create_ssl_ca_secret(self.ns, f"{self.cluster_name}-ca",
                os.path.join(tutil.g_test_data_dir, "ssl/out/cab.pem"))

            kutil.delete_secret(self.ns, f"{self.cluster_name}-tls")
            kutil.create_ssl_cert_secret(self.ns, f"{self.cluster_name}-tls",
                os.path.join(tutil.g_test_data_dir, "ssl/out/serverb-cert.pem"),
                os.path.join(tutil.g_test_data_dir, "ssl/out/serverb-key.pem"))

            self.wait_tls_changed(s0, before)
            self.wait_tls_changed(s1, before)

        # before verifying the new router, ensure the old one is gone
        self.wait_pod_gone(old_routers[0]["NAME"])
        routers = self.wait_routers(f"{self.cluster_name}-router-.*", self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/cab.pem", ssl_cert_days=10920)

        # routers are setup without certificates, so connect without VERIFY_
        for r in routers:
            check_router_ssl(self, self.ns, r)

        # kutil.delete_ic(self.ns, self.cluster_name)
        # self.wait_pod_gone(f"{self.cluster_name}-1")
        # self.wait_pod_gone(f"{self.cluster_name}-0")
        # self.wait_ic_gone(self.cluster_name)


    def test_4_add_crl(self):
        old_routers = kutil.ls_pod(self.ns, f"{self.cluster_name}-router-.*")
        self.assertEqual(len(old_routers), self.routers_count)

        kutil.delete_secret(self.ns, f"{self.cluster_name}-ca")
        kutil.create_ssl_ca_secret(self.ns, f"{self.cluster_name}-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/cab.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/crl.pem"))

        for instance in range(0, self.cluster_size):
            with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-{instance}", "root", "sakila") as s:
                def check_tls_loaded():
                    return s.query_sql("select @@global.ssl_crl").fetch_one()[0]

                self.wait(check_tls_loaded, delay=5, timeout=5*60)

        # before verifying the new router, ensure the old one is gone
        self.wait_pod_gone(old_routers[0]["NAME"])
        routers = self.wait_routers(f"{self.cluster_name}-router-.*", self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/cab.pem", crl="ssl/out/crl.pem", ssl_cert_days=10920)

        # routers are setup without certificates, so connect without VERIFY_
        for r in routers:
            check_router_ssl(self, self.ns, r)

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


class ClusterNoSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)
        cls.cluster_name = "mycluster" # due to precreated TLS certs
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_create_secrets(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

    def test_1_create_cluster_missing_ssl(self):
        """
        Create cluster without certificates and without tlsUseSelfSigned
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
"""
        start_time = isotime()
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, "INVALID", 0)

        SECRET_TLS_NOT_FOUND=f'Secret "{self.cluster_name}-tls" NOT found'
        SECRET_CA_NOT_FOUND=f'Secret "{self.cluster_name}-ca" NOT found'

        self.wait_got_cluster_event(self.cluster_name, after=start_time, timeout=120, delay=15,
                                    type="Error", reason="InvalidArgument", msg=SECRET_CA_NOT_FOUND)

        kutil.create_ssl_ca_secret(self.ns, f"{self.cluster_name}-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))

        update_time = isotime()

        self.wait_got_cluster_event(self.cluster_name, after=update_time, timeout=120, delay=15,
                                    type="Error", reason="InvalidArgument", msg=SECRET_TLS_NOT_FOUND)

        kutil.create_ssl_cert_secret(self.ns, f"{self.cluster_name}-tls",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

        self.wait_ic(self.cluster_name, "ONLINE", num_online=0)

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        self.wait_routers(f"{self.cluster_name}-router-.*", num_online=self.routers_count)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/ca.pem", ssl_cert_days=3650)

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


class ClusterAddSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    def wait_tls_changed(self, s, before):
        for _ in range(60):
            after = s.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]
            if after != before:
                break
            sleep(10)
        else:
            self.assertFalse(1, "timeout waiting for tls reload")

    _cluster_size = 1 # adapt test_2_add_tls() ()
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)
        cls.cluster_name = "mycluster" # due to precreated TLS certs
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_create_secrets(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

    def test_1_create_cluster_without_ssl(self):
        """
        Create cluster with default certificates.
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        self.wait_routers(f"{self.cluster_name}-router-.*", num_online=self.routers_count)

        # check for defaults
        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ca.pem", crl="", ssl_cert_days=3650)


    def test_2_add_tls(self):
        for instance in range(0, self.cluster_size):
            with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-{instance}", "root", "sakila") as s:
                before = s.query_sql("show status like 'Ssl_server_not_after'").fetch_one()[1]

        kutil.create_ssl_ca_secret(self.ns, f"{self.cluster_name}-ca",
            os.path.join(tutil.g_test_data_dir, "ssl/out/ca.pem"))
        kutil.create_ssl_cert_secret(self.ns, f"{self.cluster_name}-tls",
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-cert.pem"),
            os.path.join(tutil.g_test_data_dir, "ssl/out/server-key.pem"))

        kutil.patch_ic(self.ns, self.cluster_name, {
            "spec": {
                "tlsUseSelfSigned": False
            }
        }, type="merge")

        # we need to manually restart the cluster for changes of this kind to get applied
        kutil.restart_sts(self.ns, self.cluster_name)

        self.wait_pod(f"{self.cluster_name}-0", "Pending")
        self.wait_ic(self.cluster_name, "OFFLINE", 0)

        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=1)

        with mutil.MySQLPodSession(self.ns, f"{self.cluster_name}-0", "root", "sakila") as s:
            self.wait_tls_changed(s, before)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            # Because the cluster was created with self signed then no x509 was used
            # for the GR accounts. After moving to non-self signed the cluster option
            # cannot be changed, Shell doesn't provide means for that, so the accounts
            # will stay PASSWORD authenticated for the time being of the cluster.
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/ca.pem", crl=None, ssl_cert_days=3650, check_gr_accounts=False)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)


class ClusterRouterSSL(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 1 # adapt test_2_add_tls() ()
    _routers_count = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(CLUSTER_SSL_NAMESPACE)
        cls.cluster_name = "mycluster" # due to precreated TLS certs
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

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

        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

    def test_1_create_cluster_with_router_ssl(self):
        """
        Create cluster with certificates for server and router
        """

        # create cluster with server certificates
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
    tlsSecretName: router-ssl
  secretName: {self.cluster_secret_name}
  tlsCASecretName: ca
  tlsSecretName: server-ssl
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_ic(f"{self.cluster_name}", "ONLINE", num_online=self.cluster_size)

        routers = self.wait_routers(f"{self.cluster_name}-router-.*", self.routers_count)

        check_all(self, self.ns, f"{self.cluster_name}", instances=self.cluster_size, routers=self.routers_count, primary=0)

        for instance in range(0, self.cluster_size):
            check_ssl(self, self.ns, f"{self.cluster_name}-{instance}", ca="ssl/out/ca.pem")

        self.assertEqual(self.routers_count, len(routers))

        for rname in routers:
            check_router_ssl(self, self.ns, rname, ca="ssl/out/ca.pem", has_cert=True)

        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)


    def test_2_modify_ssl_certs(self):
        """
        Change server and router certs
        """
        pass


    def test_3_recover_from_bad_certs(self):
        pass


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)

        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)

