# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import os
import time
import unittest

from setup.config import g_ts_cfg
from utils import kutil
from utils import mutil
from utils import ociutil
from utils import tutil
from utils.helmutil import HelmClusterInstallOptions
from utils.helmutil import install_cluster_with_helm
from utils.helmutil import uninstall_with_helm
from utils.optesting import COMMON_OPERATOR_ERRORS
from utils.tutil import g_full_log


skip_no_meb_oci = unittest.skipIf(
    g_ts_cfg.enterprise_skip or g_ts_cfg.oci_skip or
    not g_ts_cfg.oci_config_path or not g_ts_cfg.oci_bucket_name,
    "Enterprise or OCI backup config path and/or bucket name not set")
class MEBBase:
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    backup_apikey = "backup-apikey"
    restore_par_secret = "restore-par-url"
    profile_name = "meb-oci"
    root_user = "root"
    root_password = "sakila"
    oci_storage_prefix = f"/e2etest/{g_ts_cfg.get_worker_label()}"
    meb_storage_prefix = f"{oci_storage_prefix}meb"
    _cluster_size = 3
    _routers_count = 1
    restore_base_server_id = 2000
    use_self_signed_tls = True
    tls_fixture_variant = None

    base_expected = {
        "accounts": [
            (1, "Ada", 100),
            (2, "Grace", 200),
            (3, "Linus", 300),
        ],
        "events": [
            (1, 1, "created"),
            (2, 2, "created"),
        ],
        "settings": [
            ("mode", "base"),
            ("retention", "7d"),
        ],
    }
    incremental_expected = {
        "accounts": [
            (1, "Ada", 150),
            (2, "Grace", 200),
            (3, "Linus", 300),
            (4, "Marta", 400),
        ],
        "events": [
            (1, 1, "created"),
            (2, 2, "created"),
            (3, 1, "incremental-one"),
            (4, 4, "incremental-two"),
        ],
        "settings": [
            ("feature", "incremental-one"),
            ("mode", "incremental-two"),
            ("retention", "7d"),
        ],
    }
    pitr_expected = {
        "accounts": [
            (1, "Ada", 100),
            (2, "Grace", 200),
            (3, "Linus", 300),
            (5, "Nina", 500),
        ],
        "events": [
            (1, 1, "created"),
            (2, 2, "created"),
            (5, 5, "pitr-target"),
        ],
        "settings": [
            ("mode", "pitr-target"),
            ("retention", "7d"),
        ],
    }

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.configure_cluster_names()
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)
        cls.restore_cluster_name = f"{cls.cluster_name}-restore"
        cls.restore_cluster_secret_name = f"{cls.restore_cluster_name}-mypwds"
        cls.validate_cluster_names()
        cls.oci_namespace = None
        cls.backup_outputs = {}
        cls.backup_object_paths = []
        cls.backup_names = []
        cls.restore_par = None
        cls.pitr_target_gtids = None
        cls.source_cluster_deleted = False
        cls.restore_cluster_deleted = False
        cls.helm_release_keys = {}
        cls.watched_clusters = set()
        cls.watch_cluster_pods(cls.cluster_name)

    @classmethod
    def tearDownClass(cls):
        try:
            for cluster_name in list(cls.watched_clusters):
                cls.stop_watch_cluster_pods(cluster_name)
            cls.cleanup_leftovers()
            super().tearDownClass()
        finally:
            kutil.delete_ns(cls.ns, timeout=600)

    @classmethod
    def cleanup_leftovers(cls):
        for release_key in list(cls.helm_release_keys.values()):
            try:
                uninstall_with_helm(
                    release_key=release_key, delete_namespace=False)
            except Exception as exc:
                print(
                    f"Failed to uninstall MEB test Helm release "
                    f"{release_key}: {exc}")
        cls.helm_release_keys = {}

        for backup_name in list(cls.backup_names):
            try:
                kutil.delete_mbk(cls.ns, backup_name)
            except Exception as exc:
                print(
                    f"Failed to delete MySQLBackup "
                    f"{cls.ns}/{backup_name}: {exc}")
        cls.backup_names = []

        for cluster_name in (cls.restore_cluster_name, cls.cluster_name):
            try:
                kutil.delete_ic(cls.ns, cluster_name)
            except Exception as exc:
                print(
                    f"Failed to delete InnoDBCluster "
                    f"{cls.ns}/{cluster_name}: {exc}")
            cls.delete_tls_secrets_for_cluster(cluster_name)

        for secret_name in (
                cls.backup_apikey, cls.restore_par_secret,
                cls.cluster_secret_name, cls.restore_cluster_secret_name,
                f"{cls.cluster_name}-cluster-secret",
                f"{cls.restore_cluster_name}-cluster-secret"):
            try:
                kutil.delete_secret(cls.ns, secret_name)
            except Exception as exc:
                print(f"Failed to delete Secret {cls.ns}/{secret_name}: {exc}")

        if cls.restore_par:
            try:
                ociutil.delete_preauthenticated_request(
                    "RESTORE", g_ts_cfg.oci_bucket_name,
                    cls.restore_par["id"])
            except Exception as exc:
                print(f"Failed to delete restore PAR: {exc}")
            cls.restore_par = None

        for object_path in list(cls.backup_object_paths):
            try:
                ociutil.bulk_delete(
                    "DELETE", g_ts_cfg.oci_bucket_name, object_path)
            except Exception as exc:
                print(
                    f"Failed to delete OCI backup objects under "
                    f"{object_path}: {exc}")
        cls.backup_object_paths = []

    @classmethod
    def configure_cluster_names(cls):
        pass

    @classmethod
    def validate_cluster_names(cls):
        for attr in ("cluster_name", "restore_cluster_name"):
            cluster_name = getattr(cls, attr)
            if len(cluster_name) >= 28:
                raise ValueError(
                    f"{attr} {cluster_name!r} is too long for "
                    "InnoDBCluster.spec.clusterName; must be < 28")

    @classmethod
    def tls_ca_secret_name(cls, cluster_name):
        return f"{cluster_name}-ca"

    @classmethod
    def tls_server_secret_name(cls, cluster_name):
        return f"{cluster_name}-tls"

    @classmethod
    def tls_router_secret_name(cls, cluster_name):
        return f"{cluster_name}-router-tls"

    @classmethod
    def meb_tls_secret_name(cls, cluster_name):
        return f"{cluster_name}-meb-tls"

    @classmethod
    def tls_secret_names(cls, cluster_name):
        return (
            cls.tls_ca_secret_name(cluster_name),
            cls.tls_server_secret_name(cluster_name),
            cls.tls_router_secret_name(cluster_name),
            cls.meb_tls_secret_name(cluster_name),
        )

    @classmethod
    def delete_tls_secrets_for_cluster(cls, cluster_name):
        if cls.use_self_signed_tls:
            return

        for secret_name in cls.tls_secret_names(cluster_name):
            try:
                kutil.delete_secret(cls.ns, secret_name)
            except Exception as exc:
                print(
                    f"Failed to delete TLS Secret "
                    f"{cls.ns}/{secret_name}: {exc}")

    @classmethod
    def yaml_bool(cls, value):
        return "true" if value else "false"

    def tls_values(self, cluster_name):
        tls = {
            "useSelfSigned": self.use_self_signed_tls,
        }
        if not self.use_self_signed_tls:
            tls.update({
                "caSecretName": self.tls_ca_secret_name(cluster_name),
                "serverCertAndPKsecretName": self.tls_server_secret_name(
                    cluster_name),
                "routerCertAndPKsecretName": self.tls_router_secret_name(
                    cluster_name),
            })
        return tls

    def raw_router_tls_yaml(self, cluster_name):
        if self.use_self_signed_tls:
            return ""
        return f"    tlsSecretName: {self.tls_router_secret_name(cluster_name)}\n"

    def raw_cluster_tls_yaml(self, cluster_name):
        if self.use_self_signed_tls:
            return ""
        return f"""  tlsCASecretName: {self.tls_ca_secret_name(cluster_name)}
  tlsSecretName: {self.tls_server_secret_name(cluster_name)}
"""

    def raw_meb_tls_yaml(self, cluster_name):
        if self.use_self_signed_tls:
            return ""
        return f"""  meb:
    tlsSecretName: {self.meb_tls_secret_name(cluster_name)}
"""

    def check_tls_spec(self, spec, cluster_name):
        self.assertEqual(spec["tlsUseSelfSigned"], self.use_self_signed_tls)
        if not self.use_self_signed_tls:
            self.assertEqual(
                spec["tlsCASecretName"], self.tls_ca_secret_name(cluster_name))
            self.assertEqual(
                spec["tlsSecretName"], self.tls_server_secret_name(cluster_name))
            self.assertEqual(
                spec["router"]["tlsSecretName"],
                self.tls_router_secret_name(cluster_name))

    def cluster_name_for_secret(self, secret_name):
        if secret_name == self.cluster_secret_name:
            return self.cluster_name
        if secret_name == self.restore_cluster_secret_name:
            return self.restore_cluster_name
        return None

    def tls_fixture_dir(self, cluster_name, variant=None):
        fixture_dir = os.path.join(
            tutil.g_test_data_dir, "ssl/out/meb", self.ns, cluster_name)
        if variant:
            fixture_dir = os.path.join(fixture_dir, variant)
        return fixture_dir

    def create_tls_secrets_for_cluster(self, cluster_name, variant=None):
        if self.use_self_signed_tls:
            return

        if variant is None:
            variant = self.tls_fixture_variant

        fixture_dir = self.tls_fixture_dir(cluster_name, variant)
        ca_cert = os.path.join(fixture_dir, "ca.pem")
        server_cert = os.path.join(fixture_dir, "server/tls.crt")
        server_key = os.path.join(fixture_dir, "server/tls.key")
        router_cert = os.path.join(fixture_dir, "router/tls.crt")
        router_key = os.path.join(fixture_dir, "router/tls.key")
        meb_cert = os.path.join(fixture_dir, "meb/tls.crt")
        meb_key = os.path.join(fixture_dir, "meb/tls.key")

        missing = [
            path for path in (
                ca_cert, server_cert, server_key, router_cert, router_key,
                meb_cert, meb_key)
            if not os.path.exists(path)]
        if missing:
            self.fail(
                f"Missing MEB ExternalTLS fixtures for {cluster_name}: "
                f"{missing}")

        self.__class__.delete_tls_secrets_for_cluster(cluster_name)
        kutil.create_ssl_ca_secret(
            self.ns, self.tls_ca_secret_name(cluster_name), ca_cert)
        kutil.create_ssl_cert_secret(
            self.ns, self.tls_server_secret_name(cluster_name),
            server_cert, server_key)
        kutil.create_ssl_cert_secret(
            self.ns, self.tls_router_secret_name(cluster_name),
            router_cert, router_key)
        kutil.create_secret_from_files(
            self.ns, self.meb_tls_secret_name(cluster_name), [
                ("tls.crt", meb_cert),
                ("tls.key", meb_key),
                ("client.pem", meb_cert),
                ("client.key", meb_key),
                ("ca.pem", ca_cert),
            ])

    def get_meb_restart_counts(self, cluster_name):
        counts = {}
        for instance in range(0, self.cluster_size):
            pod_name = f"{cluster_name}-{instance}"
            pod = kutil.get_po(self.ns, pod_name)
            container = tutil.get_pod_container(pod, "meb")
            if not container:
                self.fail(f"MEB container not found in {self.ns}/{pod_name}")
            counts[pod_name] = container.get("restartCount", 0)
        return counts

    def wait_meb_containers_restarted(self, cluster_name, previous_counts):
        def check():
            counts = self.get_meb_restart_counts(cluster_name)
            for pod_name, old_count in previous_counts.items():
                if counts[pod_name] <= old_count:
                    return None
            return counts

        return self.wait(check, timeout=420, delay=5)

    def rotate_tls_and_wait_meb(self, cluster_name, variant):
        counts = self.get_meb_restart_counts(cluster_name)
        self.create_tls_secrets_for_cluster(cluster_name, variant=variant)
        counts = self.wait_meb_containers_restarted(cluster_name, counts)
        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{cluster_name}-{instance}", "Running", ready=True)
        return counts

    @classmethod
    def watch_cluster_pods(cls, cluster_name):
        cls.watched_clusters.add(cluster_name)
        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cluster_name}-{instance}")

    @classmethod
    def stop_watch_cluster_pods(cls, cluster_name):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cluster_name}-{instance}")
        cls.watched_clusters.discard(cluster_name)

    def meb_profile_spec(self):
        return {
            "name": self.profile_name,
            "meb": {
                "storage": {
                    "oci": {
                        "prefix": self.meb_storage_prefix,
                        "bucketName": g_ts_cfg.oci_bucket_name,
                        "credentials": self.backup_apikey,
                        "namespace": self.oci_namespace,
                    },
                },
            },
        }

    def create_cluster_values(self, cluster_name, *, initdb_meb=None,
                              base_server_id=None,
                              with_backup_profile=False):
        values = {
            "serverInstances": self.cluster_size,
            "router": {
                "instances": self.routers_count,
            },
            "credentials": {
                "root": {
                    "user": self.root_user,
                    "password": self.root_password,
                    "host": "%",
                },
            },
            "tls": self.tls_values(cluster_name),
            "edition": "enterprise",
        }
        if base_server_id is not None:
            values["baseServerId"] = base_server_id
        if with_backup_profile:
            values["backupProfiles"] = [self.meb_profile_spec()]
            if not self.use_self_signed_tls:
                values["meb"] = {
                    "tlsSecretName": self.meb_tls_secret_name(cluster_name),
                }
        if initdb_meb:
            values["initDB"] = {
                "meb": initdb_meb,
            }
        return values

    def wait_cluster_online(self, cluster_name):
        self.wait_ic(cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(
                f"{cluster_name}-router-*", self.routers_count,
                timeout=self.cluster_size*120)

        self.wait_ic(cluster_name, "ONLINE", num_online=self.cluster_size,
                     timeout=self.cluster_size*200)

    def create_initial_data(self):
        sql = """
DROP SCHEMA IF EXISTS meb_e2e;
CREATE SCHEMA meb_e2e;
CREATE TABLE meb_e2e.accounts (
  id INT PRIMARY KEY,
  name VARCHAR(64) NOT NULL,
  balance INT NOT NULL
) ENGINE=InnoDB;
CREATE TABLE meb_e2e.events (
  id INT PRIMARY KEY,
  account_id INT NOT NULL,
  note VARCHAR(64) NOT NULL
) ENGINE=InnoDB;
CREATE TABLE meb_e2e.settings (
  k VARCHAR(64) PRIMARY KEY,
  v VARCHAR(64) NOT NULL
) ENGINE=InnoDB;
INSERT INTO meb_e2e.accounts VALUES
  (1, 'Ada', 100),
  (2, 'Grace', 200),
  (3, 'Linus', 300);
INSERT INTO meb_e2e.events VALUES
  (1, 1, 'created'),
  (2, 2, 'created');
INSERT INTO meb_e2e.settings VALUES
  ('mode', 'base'),
  ('retention', '7d');
"""
        mutil.load_script(
            self.ns, (self.get_source_primary(), "mysql"), sql,
            self.root_user, self.root_password)

    def apply_incremental_mutation_one(self):
        sql = """
UPDATE meb_e2e.accounts SET balance = 150 WHERE id = 1;
INSERT INTO meb_e2e.events VALUES (3, 1, 'incremental-one');
INSERT INTO meb_e2e.settings VALUES ('feature', 'incremental-one');
"""
        mutil.load_script(
            self.ns, (self.get_source_primary(), "mysql"), sql,
            self.root_user, self.root_password)

    def apply_incremental_mutation_two(self):
        sql = """
INSERT INTO meb_e2e.accounts VALUES (4, 'Marta', 400);
INSERT INTO meb_e2e.events VALUES (4, 4, 'incremental-two');
UPDATE meb_e2e.settings SET v = 'incremental-two' WHERE k = 'mode';
"""
        mutil.load_script(
            self.ns, (self.get_source_primary(), "mysql"), sql,
            self.root_user, self.root_password)

    def apply_pitr_target_mutation(self):
        sql = """
INSERT INTO meb_e2e.accounts VALUES (5, 'Nina', 500);
INSERT INTO meb_e2e.events VALUES (5, 5, 'pitr-target');
UPDATE meb_e2e.settings SET v = 'pitr-target' WHERE k = 'mode';
"""
        mutil.load_script(
            self.ns, (self.get_source_primary(), "mysql"), sql,
            self.root_user, self.root_password)

    def apply_pitr_after_mutation(self):
        sql = """
INSERT INTO meb_e2e.accounts VALUES (6, 'Oscar', 600);
INSERT INTO meb_e2e.events VALUES (6, 6, 'pitr-after');
UPDATE meb_e2e.settings SET v = 'pitr-after' WHERE k = 'mode';
"""
        mutil.load_script(
            self.ns, (self.get_source_primary(), "mysql"), sql,
            self.root_user, self.root_password)

    def get_source_primary(self):
        return self.get_primary_instance(
            f"{self.cluster_name}-0", self.root_user, self.root_password)

    def get_source_gtid_executed(self):
        with mutil.MySQLPodSession(
                self.ns, self.get_source_primary(),
                self.root_user, self.root_password) as session:
            return self.normalize_gtid_set(
                session.query_sql("SELECT @@GLOBAL.gtid_executed").fetch_one()[0])

    def normalize_gtid_set(self, gtid_set):
        return ",".join(
            part.strip() for part in (gtid_set or "").split(",")
            if part.strip())

    def subtract_source_gtids(self, gtid_set, subtract_gtid_set):
        with mutil.MySQLPodSession(
                self.ns, self.get_source_primary(),
                self.root_user, self.root_password) as session:
            return self.normalize_gtid_set(session.query_sql(
                "SELECT GTID_SUBTRACT(%s, %s)",
                (gtid_set, subtract_gtid_set)).fetch_one()[0])

    def get_source_binlog_base_name(self):
        with mutil.MySQLPodSession(
                self.ns, self.get_source_primary(),
                self.root_user, self.root_password) as session:
            binlog_base = session.query_sql(
                "SELECT @@GLOBAL.log_bin_basename").fetch_one()[0]
        return os.path.basename(binlog_base)

    def read_data(self, cluster_name, instance=0):
        with mutil.MySQLPodSession(
                self.ns, f"{cluster_name}-{instance}",
                self.root_user, self.root_password) as session:
            return {
                "accounts": [tuple(row) for row in session.query_sql(
                    "SELECT id, name, balance FROM meb_e2e.accounts ORDER BY id").fetch_all()],
                "events": [tuple(row) for row in session.query_sql(
                    "SELECT id, account_id, note FROM meb_e2e.events ORDER BY id").fetch_all()],
                "settings": [tuple(row) for row in session.query_sql(
                    "SELECT k, v FROM meb_e2e.settings ORDER BY k").fetch_all()],
            }

    def assert_cluster_data(self, cluster_name, expected):
        for instance in range(0, self.cluster_size):
            self.assertEqual(expected, self.read_data(cluster_name, instance))

    def wait_source_data(self, expected):
        def check():
            try:
                for instance in range(0, self.cluster_size):
                    if self.read_data(self.cluster_name, instance) != expected:
                        return False
                return True
            except Exception:
                return False

        self.wait(check, timeout=180)

    def check_ic(self):
        ic = kutil.get_ic(self.ns, self.cluster_name)
        spec = ic["spec"]

        self.check_tls_spec(spec, self.cluster_name)
        self.assertEqual(spec["edition"], "enterprise")
        self.assertEqual(spec["instances"], self.cluster_size)
        self.assertEqual(spec["router"]["instances"], self.routers_count)

        backup_profile = spec["backupProfiles"][0]
        self.assertEqual(backup_profile["name"], self.profile_name)

        meb = backup_profile["meb"]
        self.assertNotIn("extraOptions", meb)

        oci = meb["storage"]["oci"]
        self.assertEqual(oci["prefix"], self.meb_storage_prefix)
        self.assertEqual(oci["bucketName"], g_ts_cfg.oci_bucket_name)
        self.assertEqual(oci["credentials"], self.backup_apikey)
        self.assertEqual(oci["namespace"], self.oci_namespace)
        if not self.use_self_signed_tls:
            self.assertEqual(
                spec["meb"]["tlsSecretName"],
                self.meb_tls_secret_name(self.cluster_name))

    def check_restore_ic(self, initdb_meb):
        ic = kutil.get_ic(self.ns, self.restore_cluster_name)
        spec = ic["spec"]

        self.check_tls_spec(spec, self.restore_cluster_name)
        self.assertEqual(spec["edition"], "enterprise")
        self.assertEqual(spec["instances"], self.cluster_size)
        self.assertEqual(spec["router"]["instances"], self.routers_count)
        self.assertEqual(spec["baseServerId"], self.restore_base_server_id)
        self.assertEqual(spec["initDB"]["meb"], initdb_meb)

    def check_mbk(self, backup_name, output, *, incremental=False,
                  incremental_base="last_backup"):
        mbk = kutil.get_mbk(self.ns, backup_name)

        spec = mbk["spec"]
        self.assertEqual(spec["backupProfileName"], self.profile_name)
        self.assertEqual(spec["clusterName"], self.cluster_name)
        self.assertFalse(spec.get("deleteBackupData", False))
        self.assertEqual(spec.get("incremental", False), incremental)
        if incremental:
            self.assertEqual(spec.get("incrementalBase", "last_backup"),
                             incremental_base)

        status = mbk["status"]
        self.assertIsNotNone(status["startTime"])
        self.assertIsNotNone(status["completionTime"])
        self.assertGreaterEqual(status["completionTime"], status["startTime"])
        self.assertIsNotNone(status["elapsedTime"])
        self.assertEqual(status["status"], "Completed")
        self.assertEqual(status["method"], "meb")
        self.assertTrue(status["source"])
        self.assertEqual(status["output"], output)
        self.assertTrue(status["output"].startswith(f"{backup_name}-"))

    def get_terminal_backup_pod_name(self, output):
        def check_pod():
            pods = kutil.ls_po(self.ns, pattern=f"{output}.*")
            failed_pods = [
                pod for pod in pods if pod["STATUS"] in ("Error", "Failed")]
            if failed_pods:
                return ("failed", pods)

            completed_pods = [
                pod for pod in pods if pod["STATUS"] == "Completed"]
            if len(completed_pods) == 1:
                return ("completed", completed_pods[0]["NAME"])
            if len(completed_pods) > 1:
                return ("duplicate", pods)

            return None

        try:
            status, value = self.wait(check_pod, timeout=180, delay=2)
        except Exception:
            self.dump_backup_pod_log(output)
            raise

        if status != "completed":
            self.dump_backup_pod_log(output)
            self.fail(f"backup pod for {output} ended with {status}: {value}")

        return value

    def dump_backup_pod_log(self, output, backup_name=None):
        pod_prefix = output or backup_name or self.profile_name
        try:
            pods = kutil.ls_po(self.ns, pattern=f"{pod_prefix}.*")
            if not pods:
                print(f"No backup pods found for {self.ns}/{pod_prefix}")
            else:
                for pod in pods:
                    pod_name = pod["NAME"]
                    print(
                        f"===== Backup pod {self.ns}/{pod_name} "
                        f"log ({pod['STATUS']}) =====")
                    try:
                        print(kutil.logs(
                            self.ns, (pod_name, "operator-backup-job")))
                    except Exception as exc:
                        print(
                            f"Failed to read backup pod log for "
                            f"{self.ns}/{pod_name}: {exc}")
        except Exception as exc:
            print(f"Failed to list backup pods for {self.ns}/{output}: {exc}")
        self.dump_source_meb_container_logs()

    def dump_source_meb_container_logs(self):
        try:
            pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}.*")
        except Exception as exc:
            print(
                f"Failed to list source pods for "
                f"{self.ns}/{self.cluster_name}: {exc}")
            return

        if not pods:
            print(f"No source pods found for {self.ns}/{self.cluster_name}")
            return

        for pod in pods:
            pod_name = pod["NAME"]
            print(
                f"===== Source pod {self.ns}/{pod_name} "
                f"describe ({pod['STATUS']}) =====")
            try:
                print(kutil.describe_po(self.ns, pod_name))
            except Exception as exc:
                print(
                    f"Failed to describe source pod "
                    f"{self.ns}/{pod_name}: {exc}")

            print(
                f"===== Source pod {self.ns}/{pod_name} "
                f"container meb log =====")
            try:
                print(kutil.logs(self.ns, (pod_name, "meb")))
            except Exception as exc:
                print(
                    f"Failed to read source pod log for "
                    f"{self.ns}/{pod_name}/meb: {exc}")

    def check_backup_pod_log(self, output):
        pod_name = self.get_terminal_backup_pod_name(output)
        log = kutil.logs(self.ns, (pod_name, "operator-backup-job"))
        if ("mysqlbackup completed OK!" not in log or
                not log.rstrip().endswith(
                    "Command execute-backup finished with code True")):
            print(f"===== Backup pod {self.ns}/{pod_name} log failed validation =====")
            print(log)
            self.fail(
                f"MEB backup pod {self.ns}/{pod_name} did not report success")

    def dump_restore_cluster_logs(self, cluster_name):
        try:
            pods = kutil.ls_po(self.ns, pattern=f"{cluster_name}.*")
        except Exception as exc:
            print(
                f"Failed to list restore pods for "
                f"{self.ns}/{cluster_name}: {exc}")
            return

        if not pods:
            print(f"No restore pods found for {self.ns}/{cluster_name}")
            return

        for pod in pods:
            pod_name = pod["NAME"]
            print(
                f"===== Restore pod {self.ns}/{pod_name} "
                f"describe ({pod['STATUS']}) =====")
            try:
                print(kutil.describe_po(self.ns, pod_name))
            except Exception as exc:
                print(
                    f"Failed to describe restore pod "
                    f"{self.ns}/{pod_name}: {exc}")

            for container in ("restore", "mysql", "sidecar", "meb", "router"):
                try:
                    print(
                        f"===== Restore pod {self.ns}/{pod_name} "
                        f"container {container} log =====")
                    print(kutil.logs(self.ns, (pod_name, container)))
                except Exception as exc:
                    print(
                        f"Failed to read restore pod log for "
                        f"{self.ns}/{pod_name}/{container}: {exc}")

    def backup_output(self, name):
        if name not in self.__class__.backup_outputs:
            self.fail(f"backup output {name!r} was not created")
        return self.__class__.backup_outputs[name]

    def create_backup(self, backup_name, *, incremental=False,
                      incremental_base="last_backup"):
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: MySQLBackup
metadata:
  name: {backup_name}
spec:
  clusterName: {self.cluster_name}
  backupProfileName: {self.profile_name}
"""
        if incremental:
            yaml += f"""  incremental: true
  incrementalBase: {incremental_base}
"""
        kutil.apply(self.ns, yaml)
        self.__class__.backup_names.append(backup_name)

        def check_mbk(l):
            for item in l:
                if item["NAME"] == backup_name and item["STATUS"] in ("Completed", "Error"):
                    return item
            return None

        r = self.wait(kutil.ls_mbk, args=(self.ns,),
                      check=check_mbk, timeout=900)
        output = r["OUTPUT"]
        object_path = f"{self.meb_storage_prefix}{output}" if output else None
        if object_path:
            self.__class__.backup_object_paths.append(object_path)
        self.assertEqual(r["CLUSTER"], self.cluster_name)
        if r["STATUS"] == "Error":
            self.dump_backup_pod_log(output, backup_name)
            mbk = kutil.get_mbk(self.ns, backup_name)
            self.fail(
                f"MySQLBackup {self.ns}/{backup_name} failed: "
                f"{mbk.get('status', {}).get('message', '')}")
        self.assertEqual(r["STATUS"], "Completed")
        self.assertTrue(output.startswith(f"{backup_name}-"))

        try:
            self.check_mbk(
                backup_name, output, incremental=incremental,
                incremental_base=incremental_base)
            self.check_backup_pod_log(output)
            objects = ociutil.list_objects(
                "RESTORE", g_ts_cfg.oci_bucket_name, object_path)
            self.assertGreaterEqual(len(objects), 1)
            return output
        except Exception:
            self.dump_backup_pod_log(output, backup_name)
            raise

    def make_restore_initdb_meb(self):
        raise NotImplementedError

    def create_restore_par_secret(self):
        par = ociutil.create_prefix_par_base_url(
            "RESTORE", g_ts_cfg.oci_bucket_name,
            f"{self.restore_cluster_name}-meb-restore",
            self.meb_storage_prefix, expires_in_hours=4)
        self.__class__.restore_par = par
        kutil.create_secrets(
            self.ns, self.restore_par_secret,
            f"parBaseUrl: {kutil.b64encode(par['parBaseUrl'])}")

    def delete_restore_par(self):
        if self.__class__.restore_par:
            try:
                ociutil.delete_preauthenticated_request(
                    "RESTORE", g_ts_cfg.oci_bucket_name,
                    self.__class__.restore_par["id"])
            except Exception as exc:
                print(f"Failed to delete restore PAR: {exc}")
            self.__class__.restore_par = None

    def delete_all_backups_from_bucket(self):
        for object_path in self.__class__.backup_object_paths:
            ociutil.bulk_delete("DELETE", g_ts_cfg.oci_bucket_name, object_path)
        self.__class__.backup_object_paths = []

    def delete_backup_objects(self):
        for backup_name in self.__class__.backup_names:
            kutil.delete_mbk(self.ns, backup_name)
        self.__class__.backup_names = []

    def delete_restore_secret(self):
        kutil.delete_secret(self.ns, self.restore_par_secret)

    def restore_source_for_output(self, output):
        return f"{self.meb_storage_prefix}{output}"


class MEBRawMixin:
    def create_source_cluster(self):
        kutil.create_user_secrets(
            self.ns, self.cluster_secret_name, root_user=self.root_user,
            root_host="%", root_pass=self.root_password)
        self.create_tls_secrets_for_cluster(self.cluster_name)

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
  router:
    instances: {self.routers_count}
{self.raw_router_tls_yaml(self.cluster_name)}  tlsUseSelfSigned: {self.yaml_bool(self.use_self_signed_tls)}
{self.raw_cluster_tls_yaml(self.cluster_name)}  edition: enterprise
{self.raw_meb_tls_yaml(self.cluster_name)}  backupProfiles:
  - name: {self.profile_name}
    meb:
      storage:
        oci:
          prefix: {self.meb_storage_prefix}
          bucketName: {g_ts_cfg.oci_bucket_name}
          credentials: {self.backup_apikey}
          namespace: {self.oci_namespace}
"""
        kutil.apply(self.ns, yaml)

    def create_restore_cluster(self, initdb_meb):
        kutil.create_user_secrets(
            self.ns, self.restore_cluster_secret_name,
            root_user=self.root_user, root_host="%",
            root_pass=self.root_password)
        self.create_tls_secrets_for_cluster(self.restore_cluster_name)

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.restore_cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.restore_cluster_secret_name}
  router:
    instances: {self.routers_count}
{self.raw_router_tls_yaml(self.restore_cluster_name)}  tlsUseSelfSigned: {self.yaml_bool(self.use_self_signed_tls)}
{self.raw_cluster_tls_yaml(self.restore_cluster_name)}  edition: enterprise
  baseServerId: {self.restore_base_server_id}
  initDB:
    meb:
      storage:
        ociObjectStorage:
          credentials: {self.restore_par_secret}
      fullBackup: {initdb_meb["fullBackup"]}
"""
        if initdb_meb.get("incrementalBackups"):
            yaml += "      incrementalBackups:\n"
            for backup in initdb_meb["incrementalBackups"]:
                yaml += f"      - {backup}\n"
        if initdb_meb.get("pitr"):
            pitr = initdb_meb["pitr"]
            end = pitr["end"]
            yaml += f"""      pitr:
        backupFile: {pitr["backupFile"]}
        binlogName: {pitr["binlogName"]}
        end:
"""
            if "afterGtids" in end:
                yaml += f"""          afterGtids: "{end["afterGtids"]}"
"""
            if "beforeGtids" in end:
                yaml += f"""          beforeGtids: "{end["beforeGtids"]}"
"""
        kutil.apply(self.ns, yaml)

    def delete_cluster_resource(self, cluster_name):
        kutil.delete_ic(self.ns, cluster_name)
        self.wait_pods_gone(f"{cluster_name}-*")
        self.wait_routers_gone(f"{cluster_name}-router-*")
        self.wait_ic_gone(cluster_name)

    def delete_cluster_secret_resource(self, secret_name):
        kutil.delete_secret(self.ns, secret_name)
        cluster_name = self.cluster_name_for_secret(secret_name)
        if cluster_name:
            self.delete_tls_secrets_for_cluster(cluster_name)


class MEBHelmMixin:
    def install_cluster(self, cluster_name, values):
        options = HelmClusterInstallOptions(
            namespace=self.ns,
            release_name=cluster_name,
            kube_context=g_ts_cfg.k8s_context,
            cluster_values=values,
        )
        key = (
            options.namespace,
            options.release_name,
            options.kube_context or "",
        )
        self.__class__.helm_release_keys[cluster_name] = key
        install_cluster_with_helm(self.ns, resolved_options=options)

    def create_source_cluster(self):
        self.create_tls_secrets_for_cluster(self.cluster_name)
        self.install_cluster(
            self.cluster_name,
            self.create_cluster_values(
                self.cluster_name, with_backup_profile=True))

    def create_restore_cluster(self, initdb_meb):
        self.create_tls_secrets_for_cluster(self.restore_cluster_name)
        self.install_cluster(
            self.restore_cluster_name,
            self.create_cluster_values(
                self.restore_cluster_name,
                initdb_meb=initdb_meb,
                base_server_id=self.restore_base_server_id))

    def delete_cluster_resource(self, cluster_name):
        release_key = self.__class__.helm_release_keys.get(cluster_name)
        if release_key:
            uninstall_with_helm(
                release_key=release_key, delete_namespace=False)
            del self.__class__.helm_release_keys[cluster_name]
        else:
            kutil.delete_ic(self.ns, cluster_name)
        self.wait_pods_gone(f"{cluster_name}-*")
        self.wait_routers_gone(f"{cluster_name}-router-*")
        self.wait_ic_gone(cluster_name)

    def delete_cluster_secret_resource(self, secret_name):
        kutil.delete_secret(self.ns, secret_name)
        if secret_name.endswith("-mypwds"):
            cluster_name = secret_name[:-len("-mypwds")]
            kutil.delete_secret(self.ns, f"{cluster_name}-cluster-secret")
        cluster_name = self.cluster_name_for_secret(secret_name)
        if cluster_name:
            self.delete_tls_secrets_for_cluster(cluster_name)


class MEBFullBackupScenario(MEBBase):
    profile_name = "meb-full-oci"
    backup_name = "meb-full-oci"

    def test_0_create(self):
        self.__class__.oci_namespace = ociutil.get_object_storage_namespace("BACKUP")

        kutil.create_apikey_secret(
            self.ns, self.backup_apikey, g_ts_cfg.oci_config_path, "BACKUP")
        self.create_source_cluster()
        self.wait_cluster_online(self.cluster_name)
        self.create_initial_data()
        self.wait_source_data(self.base_expected)

    def test_1_backup_to_oci_bucket(self):
        self.check_ic()
        output = self.create_backup(self.backup_name)
        self.__class__.backup_outputs["full"] = output

    def test_9_destroy(self):
        self.delete_backup_objects()
        self.delete_cluster_resource(self.cluster_name)
        self.__class__.source_cluster_deleted = True

        kutil.delete_secret(self.ns, self.backup_apikey)
        self.delete_cluster_secret_resource(self.cluster_secret_name)
        self.delete_all_backups_from_bucket()


class MEBRestoreScenario(MEBBase):
    expected_after_restore = None

    def test_0_create_source(self):
        self.__class__.oci_namespace = ociutil.get_object_storage_namespace("BACKUP")

        kutil.create_apikey_secret(
            self.ns, self.backup_apikey, g_ts_cfg.oci_config_path, "BACKUP")
        self.create_source_cluster()
        self.wait_cluster_online(self.cluster_name)
        self.create_initial_data()
        self.wait_source_data(self.base_expected)
        self.check_ic()

    def test_1_create_backups(self):
        self.create_scenario_backups()

    def test_2_restore(self):
        initdb_meb = self.make_restore_initdb_meb()
        try:
            self.create_restore_par_secret()
            self.delete_cluster_resource(self.cluster_name)
            self.__class__.source_cluster_deleted = True
            self.__class__.watch_cluster_pods(self.restore_cluster_name)
            self.create_restore_cluster(initdb_meb)
            self.wait_cluster_online(self.restore_cluster_name)
            self.check_restore_ic(initdb_meb)
        except Exception:
            self.dump_restore_cluster_logs(self.restore_cluster_name)
            raise

    def test_3_check_data(self):
        try:
            self.assert_cluster_data(
                self.restore_cluster_name, self.expected_after_restore)
        except Exception:
            self.dump_restore_cluster_logs(self.restore_cluster_name)
            raise

    def test_9_destroy(self):
        self.delete_backup_objects()
        if not self.__class__.restore_cluster_deleted:
            self.delete_cluster_resource(self.restore_cluster_name)
            self.__class__.restore_cluster_deleted = True
        if not self.__class__.source_cluster_deleted:
            self.delete_cluster_resource(self.cluster_name)
            self.__class__.source_cluster_deleted = True

        kutil.delete_secret(self.ns, self.backup_apikey)
        self.delete_restore_secret()
        self.delete_cluster_secret_resource(self.cluster_secret_name)
        self.delete_cluster_secret_resource(self.restore_cluster_secret_name)
        self.delete_restore_par()
        self.delete_all_backups_from_bucket()


class MEBFullRestoreScenario(MEBRestoreScenario):
    expected_after_restore = MEBBase.base_expected
    backup_name = "meb-full-restore"

    def create_scenario_backups(self):
        output = self.create_backup(self.backup_name)
        self.__class__.backup_outputs["full"] = output

    def make_restore_initdb_meb(self):
        return {
            "storage": {
                "ociObjectStorage": {
                    "credentials": self.restore_par_secret,
                },
            },
            "fullBackup": self.restore_source_for_output(
                self.backup_output("full")),
        }


class MEBIncrementalRestoreScenario(MEBRestoreScenario):
    expected_after_restore = MEBBase.incremental_expected
    full_backup_name = "meb-incremental-full"
    inc1_backup_name = "meb-incremental-one"
    inc2_backup_name = "meb-incremental-two"

    def create_scenario_backups(self):
        output = self.create_backup(self.full_backup_name)
        self.__class__.backup_outputs["full"] = output

        self.apply_incremental_mutation_one()
        inc1_expected = {
            "accounts": [
                (1, "Ada", 150),
                (2, "Grace", 200),
                (3, "Linus", 300),
            ],
            "events": [
                (1, 1, "created"),
                (2, 2, "created"),
                (3, 1, "incremental-one"),
            ],
            "settings": [
                ("feature", "incremental-one"),
                ("mode", "base"),
                ("retention", "7d"),
            ],
        }
        self.wait_source_data(inc1_expected)
        output = self.create_backup(
            self.inc1_backup_name, incremental=True,
            incremental_base="last_backup")
        self.__class__.backup_outputs["inc1"] = output

        self.apply_incremental_mutation_two()
        self.wait_source_data(self.incremental_expected)
        output = self.create_backup(
            self.inc2_backup_name, incremental=True,
            incremental_base="last_backup")
        self.__class__.backup_outputs["inc2"] = output

    def make_restore_initdb_meb(self):
        return {
            "storage": {
                "ociObjectStorage": {
                    "credentials": self.restore_par_secret,
                },
            },
            "fullBackup": self.restore_source_for_output(
                self.backup_output("full")),
            "incrementalBackups": [
                self.restore_source_for_output(
                    self.backup_output("inc1")),
                self.restore_source_for_output(
                    self.backup_output("inc2")),
            ],
        }


class MEBPITRRestoreScenario(MEBRestoreScenario):
    expected_after_restore = MEBBase.pitr_expected
    full_backup_name = "meb-pitr-full"
    pitr_backup_name = "meb-pitr-binlogs"

    def create_scenario_backups(self):
        output = self.create_backup(self.full_backup_name)
        self.__class__.backup_outputs["full"] = output

        self.apply_pitr_target_mutation()
        self.wait_source_data(self.pitr_expected)
        target_gtid_set = self.get_source_gtid_executed()
        self.assertTrue(target_gtid_set)
        self.__class__.pitr_target_gtids = target_gtid_set
        self.__class__.pitr_binlog_name = self.get_source_binlog_base_name()

        self.apply_pitr_after_mutation()
        pitr_after_expected = {
            "accounts": [
                (1, "Ada", 100),
                (2, "Grace", 200),
                (3, "Linus", 300),
                (5, "Nina", 500),
                (6, "Oscar", 600),
            ],
            "events": [
                (1, 1, "created"),
                (2, 2, "created"),
                (5, 5, "pitr-target"),
                (6, 6, "pitr-after"),
            ],
            "settings": [
                ("mode", "pitr-after"),
                ("retention", "7d"),
            ],
        }
        self.wait_source_data(pitr_after_expected)
        after_gtid_set = self.get_source_gtid_executed()
        self.assertTrue(
            self.subtract_source_gtids(after_gtid_set, target_gtid_set))
        output = self.create_backup(self.pitr_backup_name)
        self.__class__.backup_outputs["pitr"] = output

    def make_restore_initdb_meb(self):
        return {
            "storage": {
                "ociObjectStorage": {
                    "credentials": self.restore_par_secret,
                },
            },
            "fullBackup": self.restore_source_for_output(
                self.backup_output("full")),
            "pitr": {
                "backupFile": self.restore_source_for_output(
                    self.backup_output("pitr")),
                "binlogName": self.__class__.pitr_binlog_name,
                "end": {
                    "afterGtids": self.__class__.pitr_target_gtids,
                },
            },
        }


@skip_no_meb_oci
class MEBFullBackupSelfTLSRaw(MEBRawMixin, MEBFullBackupScenario,
                              tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBFullBackupSelfTLSHelm(MEBHelmMixin, MEBFullBackupScenario,
                               tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBFullRestoreSelfTLSRaw(MEBRawMixin, MEBFullRestoreScenario,
                               tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBFullRestoreSelfTLSHelm(MEBHelmMixin, MEBFullRestoreScenario,
                                tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBIncrementalRestoreSelfTLSRaw(MEBRawMixin,
                                      MEBIncrementalRestoreScenario,
                                      tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBIncrementalRestoreSelfTLSHelm(MEBHelmMixin,
                                       MEBIncrementalRestoreScenario,
                                       tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBPITRRestoreSelfTLSRaw(MEBRawMixin, MEBPITRRestoreScenario,
                               tutil.OperatorTest):
    pass


@skip_no_meb_oci
class MEBPITRRestoreSelfTLSHelm(MEBHelmMixin, MEBPITRRestoreScenario,
                                tutil.OperatorTest):
    pass


class MEBExternalTLSMixin:
    use_self_signed_tls = False
    external_tls_cluster_name = None

    @classmethod
    def configure_cluster_names(cls):
        if not cls.external_tls_cluster_name:
            raise ValueError(
                f"{cls.__name__} must define external_tls_cluster_name")
        cls.cluster_name = cls.external_tls_cluster_name
        cls.cluster_secret_name = f"{cls.cluster_name}-mypwds"


@skip_no_meb_oci
class MEBFullBackupExternalTLSRaw(MEBExternalTLSMixin, MEBRawMixin,
                                  MEBFullBackupScenario, tutil.OperatorTest):
    external_tls_cluster_name = "meb-fb-ext-raw"


@skip_no_meb_oci
class MEBTLSRotationExternalTLSRaw(MEBExternalTLSMixin, MEBRawMixin,
                                   MEBFullBackupScenario,
                                   tutil.OperatorTest):
    external_tls_cluster_name = "meb-tls-rot-raw"
    tls_fixture_variant = "initial"
    first_backup_name = "meb-tls-rotate-one"
    second_backup_name = "meb-tls-rotate-two"

    def test_1_backup_to_oci_bucket(self):
        self.check_ic()

        time.sleep(65)
        self.rotate_tls_and_wait_meb(self.cluster_name, "rotate1")
        output = self.create_backup(self.first_backup_name)
        self.__class__.backup_outputs["rotate1"] = output

        self.rotate_tls_and_wait_meb(self.cluster_name, "rotate2")
        output = self.create_backup(self.second_backup_name)
        self.__class__.backup_outputs["rotate2"] = output


@skip_no_meb_oci
class MEBFullBackupExternalTLSHelm(MEBExternalTLSMixin, MEBHelmMixin,
                                   MEBFullBackupScenario, tutil.OperatorTest):
    external_tls_cluster_name = "meb-fb-ext-helm"


@skip_no_meb_oci
class MEBFullRestoreExternalTLSRaw(MEBExternalTLSMixin, MEBRawMixin,
                                   MEBFullRestoreScenario, tutil.OperatorTest):
    external_tls_cluster_name = "meb-fr-ext-raw"


@skip_no_meb_oci
class MEBFullRestoreExternalTLSHelm(MEBExternalTLSMixin, MEBHelmMixin,
                                    MEBFullRestoreScenario,
                                    tutil.OperatorTest):
    external_tls_cluster_name = "meb-fr-ext-helm"


@skip_no_meb_oci
class MEBIncrementalRestoreExternalTLSRaw(MEBExternalTLSMixin, MEBRawMixin,
                                          MEBIncrementalRestoreScenario,
                                          tutil.OperatorTest):
    external_tls_cluster_name = "meb-inc-ext-raw"


@skip_no_meb_oci
class MEBIncrementalRestoreExternalTLSHelm(MEBExternalTLSMixin, MEBHelmMixin,
                                           MEBIncrementalRestoreScenario,
                                           tutil.OperatorTest):
    external_tls_cluster_name = "meb-inc-ext-helm"


@skip_no_meb_oci
class MEBPITRRestoreExternalTLSRaw(MEBExternalTLSMixin, MEBRawMixin,
                                   MEBPITRRestoreScenario, tutil.OperatorTest):
    external_tls_cluster_name = "meb-pitr-ext-raw"


@skip_no_meb_oci
class MEBPITRRestoreExternalTLSHelm(MEBExternalTLSMixin, MEBHelmMixin,
                                    MEBPITRRestoreScenario,
                                    tutil.OperatorTest):
    external_tls_cluster_name = "meb-pitr-ext-helm"
