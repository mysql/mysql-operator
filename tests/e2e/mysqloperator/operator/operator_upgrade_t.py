# Copyright (c) 2022, 2025 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import json
import pathlib
import time
import unittest
import re
from datetime import datetime, timezone
import yaml

from utils import tutil
from utils import kutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from setup.config import g_ts_cfg


DEFAULT_OPERATOR_CLUSTERROLE_NAMES = (
    "mysql-operator",
    "mysql-sidecar",
    "mysql-switchover",
)
DEFAULT_OPERATOR_DEPLOY_MANIFEST = (
    pathlib.Path(__file__).resolve().parents[4] / "deploy" / "deploy-operator.yaml"
)


def _load_default_operator_clusterrole_rules(
    manifest_path: pathlib.Path = DEFAULT_OPERATOR_DEPLOY_MANIFEST,
) -> dict[str, list[dict]]:
    rules_by_name = {}

    with manifest_path.open(encoding="utf-8") as handle:
        for doc in yaml.safe_load_all(handle):
            if not doc or doc.get("kind") != "ClusterRole":
                continue

            name = doc.get("metadata", {}).get("name")
            if name in DEFAULT_OPERATOR_CLUSTERROLE_NAMES:
                rules_by_name[name] = doc["rules"]

    missing = set(DEFAULT_OPERATOR_CLUSTERROLE_NAMES) - set(rules_by_name)
    if missing:
        raise AssertionError(
            f"Missing default operator ClusterRoles in {manifest_path}: {sorted(missing)}"
        )

    return rules_by_name


def _sync_default_operator_clusterroles_from_manifest() -> None:
    for name, rules in _load_default_operator_clusterrole_rules().items():
        kutil.patch(
            None,
            "clusterrole",
            name,
            {"rules": rules},
            type="merge",
            data_as_type="json",
            w_ns=False,
        )


def change_operator_version(version=None, store_operator_log=None, new_on_same_version=False):
    """Change to the given operator version"""

    # Get name of current operator pod, once this is gone we know the new one
    # took over as it only be deleted once new one is ready
    pods = kutil.ls_pod("mysql-operator", "mysql-operator.*")

    old_pod = kutil.get_po("mysql-operator", pods[0]["NAME"])

    target_image = g_ts_cfg.get_operator_image(version)

    # Keep ClusterRoles aligned with the checked-in operator manifest before
    # rolling the Pod image. Version-only upgrades in the test harness patch the
    # Deployment directly, so RBAC would otherwise stay stale and can block the
    # replacement pod from ever becoming ready.
    _sync_default_operator_clusterroles_from_manifest()

    if target_image == old_pod["spec"]["containers"][0]["image"] and new_on_same_version == False:
        # We are already running the expected version
        return

    # Patch version
    patch = {"spec": {
            "template": {
                "metadata": {
                    "annotations": {
                        "kubectl.kubernetes.io/restartedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
                    }
                },
                "spec": {
                    "containers": [{
                        "image": target_image,
                        "name": "mysql-operator"
                    }]
                }
            }
        }
    }

    # If we downgrade before 8.0.33 there is no readiness probe
    # this can be removed once our base test version is upgraded
    # This attempts to do the smallest change possible
    if version:
        patch["spec"]["template"]["spec"]["containers"][0]["readinessProbe"] = {
            "exec": {
                "command": ["cat", "/dev/null"]
            }
        }
    else:
        patch["spec"]["template"]["spec"]["containers"][0]["readinessProbe"] = {
            "exec": {
                "command": ["cat", "/tmp/mysql-operator-ready"]
            }
        }

    #print(patch)
    kutil.patch_dp("mysql-operator", "mysql-operator", patch)

    if store_operator_log:
        store_operator_log()

    # Wait till old operator is gone
    if pods:
        kutil.wait_pod_gone("mysql-operator", pods[0]["NAME"])

    # Wait for the new operator running
    pods = kutil.ls_pod("mysql-operator", "mysql-operator.*")
    kutil.wait_pod("mysql-operator", pods[0]["NAME"])


# This needs to stay, as it does a basic upgrade which also includes backup schedule while other tests don't
class OperatorUpgradeTest(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    cluster_size = 1
    routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        # Revert to current operator version under test, if tests passed this
        # should be a no-op as the test itself should do that already
        change_operator_version(store_operator_log=lambda: cls.take_log_operator_snapshot())

        super().tearDownClass()

    def test_1_sidecar_update(self):
        def compare_image(used_image, expected_image):
            def extract_image_tag(image):
                return image[image.rfind(':') + 1:]

            if used_image != expected_image and extract_image_tag(used_image) != extract_image_tag(expected_image):
                self.fail(f"{used_image} != {expected_image}")

        # input params as tuples [name, image], e.g. ['sidecar', 'registry.localhost:5000/mysql/community-operator:8.0.34-2.0.10']
        def compare_image_info(used, expected):
            used_name = used[0]
            expected_name = expected[0]
            self.assertEqual(used_name, expected_name)

            used_image = used[1]
            expected_image = expected[1]
            compare_image(used_image, expected_image)

        def assert_images_equal(used, expected):
            self.assertEqual(len(used), len(expected))
            for used, expected in zip(used, expected):
                compare_image_info(used, expected)

        def assert_sidecar_image(expected_image):
            spec = kutil.get_po(self.ns, f"{self.cluster_name}-0")["spec"]
            images_used = list(
                map(lambda c: [c["name"], c["image"]],
                    filter(lambda c: c["name"] in ["initconf", "fixdatadir", "sidecar"],
                        spec["initContainers"] + spec["containers"])
                )
            )
            # TODO: bring back after upgrade to 8.0.34
            # self.assertEqual(images_used, [['fixdatadir', expected_image], ['initconf', expected_image], ['sidecar', expected_image]])
            assert_images_equal(images_used, [['fixdatadir', expected_image], ['initconf', expected_image], ['sidecar', expected_image]])

        def assert_cj_image(cj_name, expected_image):
            cj = kutil.get_cj(self.ns, cj_name)
            cj_image = cj["spec"]["jobTemplate"]["spec"]["template"]["spec"]["containers"][0]["image"]
            compare_image(cj_image, expected_image)

        change_operator_version(g_ts_cfg.operator_current_lts_version_tag, store_operator_log=lambda: self.take_log_operator_snapshot())

        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        #
        # This will schedule backups for Jan 1st for a year where this is a Monday
        # it wil fail to create a backup at that time, which is okay, as we don't
        # want it to run anyways and jsut want to inspect the CronJob
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
  version: "{g_ts_cfg.get_current_lts_version()}"

  backupProfiles:
    - name: testprofile
      dumpInstance:
        storage:
          ociObjectStorage:
            bucketName: does-not-exist
            credentials: does-not-exist

  backupSchedules:
    - name: testschedule
      schedule: "1 1 1 1 1"
      deleteBackupData: false
      backupProfileName: testprofile
      enabled: true
    - name: testscheduleinactive
      schedule: "1 1 1 1 1"
      deleteBackupData: false
      backupProfileName: testprofile
      enabled: false

"""

        # 1 - Cluster is being deployed with current operator
        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        prev_operator_image = g_ts_cfg.get_operator_image(g_ts_cfg.operator_current_lts_version_tag)
        assert_sidecar_image(prev_operator_image)
        time.sleep(10)
        assert_cj_image(f"{self.cluster_name}-testschedule-cb", prev_operator_image)
        assert_cj_image(f"{self.cluster_name}-testscheduleinactive-cb", prev_operator_image)

        # 2 - Upgrading Operator doesn't change sidecar
        change_operator_version(store_operator_log=lambda: self.take_log_operator_snapshot())

        assert_sidecar_image(prev_operator_image)
        time.sleep(10)
        operator_image = g_ts_cfg.get_operator_image()
        assert_cj_image(f"{self.cluster_name}-testschedule-cb", operator_image)
        assert_cj_image(f"{self.cluster_name}-testscheduleinactive-cb", operator_image)

        # 3 - Upgrading the InnoDB Cluster updates sidecar
        kutil.patch_ic(self.ns, self.cluster_name, {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        def check_done(pod):
            po = kutil.get_po(self.ns, pod)
            # self.logger.debug(json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")))
            return json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")).get("version", "")

        self.wait(check_done, args=(f"{self.cluster_name}-0", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=150, delay=10)

        assert_sidecar_image(g_ts_cfg.get_operator_image())

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


class OperatorAndClusterMultiUpgradeBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls, ns = None) -> None:
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass(ns)

    @classmethod
    def tearDownClass(cls) -> None:
        # Revert to current operator version under test, if tests passed this
        # should be a no-op as the test itself should do that already
        change_operator_version(store_operator_log=lambda: cls.take_log_operator_snapshot(), new_on_same_version=True)

        super().tearDownClass()

    def create_cluster_secret(self) -> None:
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

    def delete_cluster_secret(self) -> None:
        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def create_tls_secrets(self) -> None:
        pass

    def delete_tls_secrets(self) -> None:
        pass

    def server_tls_defition(self) -> str:
        pass

    def router_tls_defition(self) -> str:
        pass

    def verify_existing_cluster_reconciled_after_operator_restart(self, num_online: int) -> None:
        before_restart = kutil.get_ic(self.ns, self.cluster_name)
        probe_time = before_restart["status"]["cluster"].get("lastProbeTime")
        self.assertIsNotNone(probe_time)

        change_operator_version(store_operator_log=lambda: self.take_log_operator_snapshot(), new_on_same_version=True)

        self.wait_ic(self.cluster_name, "ONLINE", num_online=num_online, probe_time=probe_time)

        after_restart = kutil.get_ic(self.ns, self.cluster_name)
        self.assertEqual(after_restart["status"]["cluster"]["status"], "ONLINE")
        self.assertGreater(after_restart["status"]["cluster"]["lastProbeTime"], probe_time)

    def _test_00_sidecar_update(self) -> None:
        def compare_image(used_image, expected_image):
            def extract_image_tag(image):
                return image[image.rfind(':') + 1:]

            if used_image != expected_image and extract_image_tag(used_image) != extract_image_tag(expected_image):
                self.fail(f"{used_image} != {expected_image}")

        # input params as tuples [name, image], e.g. ['sidecar', 'registry.localhost:5000/mysql/community-operator:8.0.34-2.0.10']
        def compare_image_info(used, expected) -> None:
            used_name = used[0]
            expected_name = expected[0]
            self.assertEqual(used_name, expected_name)

            used_image = used[1]
            expected_image = expected[1]
            compare_image(used_image, expected_image)

        def assert_images_equal(used, expected) -> None:
            self.assertEqual(len(used), len(expected))
            for used, expected in zip(used, expected):
                compare_image_info(used, expected)

        def assert_sidecar_image(server_instances, expected_image) -> None:
            self.logger.info(f"Checking fixdatadir, initconf and sidecar for {expected_image} of {server_instances} instances")
            for instance in range(0, server_instances):
                spec = kutil.get_po(self.ns, f"{self.cluster_name}-{instance}")["spec"]
                images_used = list(
                    map(lambda c: [c["name"], c["image"]],
                        filter(lambda c: c["name"] in ["initconf", "fixdatadir", "sidecar"],
                            spec["initContainers"] + spec["containers"])
                    )
                )
                self.logger.info(f"Images used by {self.cluster_name}-{instance}:{images_used}")
            assert_images_equal(images_used, [['fixdatadir', expected_image], ['initconf', expected_image], ['sidecar', expected_image]])

        self.create_cluster_secret()
        self.create_tls_secrets()

        for scenario in self.upgrade_scenarios:
            with self.subTest():
                self.logger.info("=================================================")
                self.logger.info(f"Server Instances: {scenario['server_instances']}")
                self.logger.info(f"Router Instances: {scenario['router_instances']}")
                self.logger.info(f"Initial operator version: {scenario['initial_old_operator_version']}")
                self.logger.info(f"TLS:{'external certificates' if self.router_tls_defition() else 'self signed'}")
                self.logger.info("=================================================")
                change_operator_version(scenario["initial_old_operator_version"], store_operator_log=lambda: self.take_log_operator_snapshot(), new_on_same_version=True)

                yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {scenario["server_instances"]}
  router:
    instances: {scenario["router_instances"]}
{self.router_tls_defition()}
  secretName: {self.cluster_secret_name}
  {self.server_tls_defition()}
  version: {scenario["initial_old_server_version"]}
  podSpec:
    terminationGracePeriodSeconds: 15
#  mycnf: |
#    [mysqld]
#    innodb_lock_wait_timeout=50
"""
                print(f"Applying {yaml}")
                kutil.apply(self.ns, yaml)
                self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"], timeout=60)

                for instance in range(0, scenario["server_instances"]):
                    self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

                self.wait_ic(self.cluster_name, "ONLINE", scenario["server_instances"], timeout=150)
                self.wait_routers(f"{self.cluster_name}-router-*", scenario["router_instances"])

                old_operator_image = g_ts_cfg.get_operator_image(scenario["initial_old_operator_version"])
                assert_sidecar_image(scenario["server_instances"], old_operator_image)
                time.sleep(5)
                old_cluster_version = scenario["initial_old_server_version"]
                for operator_version, upgrade_versions in scenario["operator_and_server_versions"]:
                    with self.subTest(operator_version=operator_version):
                        # Upgrading Operator doesn't change sidecar
                        self.logger.info("=================================================")
                        self.logger.info(f"UPGRADING OPERATOR to {operator_version}")
                        self.logger.info("=================================================")
                        change_operator_version(version=operator_version, store_operator_log=lambda: self.take_log_operator_snapshot(), new_on_same_version=True)

                        self.verify_existing_cluster_reconciled_after_operator_restart(scenario["server_instances"])

                        assert_sidecar_image(scenario["server_instances"], old_operator_image)
                        operator_image = g_ts_cfg.get_operator_image(version=operator_version)

                        time.sleep(15)

                        for upgrade_version in upgrade_versions:
                            if upgrade_version <= old_cluster_version:
                                self.logger.info(f"Skipping upgrade from {old_cluster_version} to {upgrade_version} ")
                                continue
                            with self.subTest(upgrade_version=upgrade_version):
                                self.logger.info("=================================================")
                                self.logger.info(f"UPGRADING CLUSTER to {upgrade_version}")
                                self.logger.info("=================================================")
                                kutil.patch_ic(self.ns, self.cluster_name, {"spec":{"version":upgrade_version}}, type="merge")

                                def check_done(pod):
                                    empty_value = ""
                                    attempt = 20
                                    po = None
                                    print(pod)
                                    while attempt > 0 and po is None:
                                        print(kutil.ls_pod(self.ns, f"{self.cluster_name}-\\d"))
                                        try:
                                            po = kutil.get_po(self.ns, pod)
                                        except Exception as exc:
                                            is_not_found = bool(re.search(r'Error from server \(NotFound\): pods ".+" not found', str(exc)))
                                            if not is_not_found:
                                                raise
                                            attempt = attempt - 1
                                            self.logger.info(f"Pod {self.ns}/{pod} not found! Will retry! {attempt} attempts left")
                                            time.sleep(2)

                                    if po is None:
                                        self.logger.info("Pod {self.ns}/{pod} not found! Giving up!")
                                        return empty_value

                                    try:
                                        print(kutil.logs(self.ns, [pod, "initconf"]))
                                        print(kutil.logs(self.ns, [pod, "initmysql"]))
                                        print(kutil.logs(self.ns, [pod, "mysql"]))
                                        print(kutil.logs(self.ns, [pod, "sidecar"]))
                                    except Exception as exc:
                                        print(exc)
                                    self.logger.info(f"po_status={po['status']}")
                                    self.logger.info(json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")))
                                    return json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")).get("version", empty_value)


                                for instance in reversed(range(0, scenario["server_instances"])):
                                    try:
                                        pod_name = f"{self.cluster_name}-{instance}"
                                        self.logger.info("============================================================")
                                        self.logger.info(f"CHECKING POD {pod_name} UPGRADING TO {upgrade_version}")
                                        self.logger.info("============================================================")
                                        self.wait(check_done,
                                                args=(pod_name, ),
                                                check=lambda s: s.startswith(upgrade_version),
                                                timeout=600,
                                                delay=1)
                                    except Exception as e:
                                        po = kutil.get_po(self.ns, f"{self.cluster_name}-{instance}")
                                        print(po)
                                        if str(e) == "Timeout waiting for condition":
                                            raise AssertionError(f"Timeout while waiting for upgrade on {self.cluster_name}-{instance}") from e
                                        raise

                                assert_sidecar_image(scenario["server_instances"], operator_image)
                                old_cluster_version = upgrade_version
                        old_operator_image = operator_image
                self.logger.info("Finished with current upgrade scenario")

                self.logger.info("Deleting IC")
                kutil.delete_ic(self.ns, self.cluster_name)

                self.logger.info("Waiting for all serverpods to vanish")
                self.wait_pods_gone(f"{self.cluster_name}-*")

                self.logger.info("Waiting for all router pods to vanish")
                self.wait_routers_gone(f"{self.cluster_name}-router-*")

                self.logger.info("Waiting for IC to be gone")
                self.wait_ic_gone(self.cluster_name)

                self.logger.info("Deleting PVCs or any new cluster will start with them and will crash starting from newer datadir version or because of passwords")
                kutil.delete_pvc(self.ns, None)

                self.logger.info("Will either continue with next scenario or if no more we are finished")

        print("Finished with all scenarios")

    def _test_99_destroy(self) -> None:
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        self.delete_tls_secrets()
        self.delete_cluster_secret()

    def runit(self) -> None:
        self._test_00_sidecar_update()
        self._test_99_destroy()


class OperatorAndClusterMultiUpgradeTestSelfSignedBase(OperatorAndClusterMultiUpgradeBase):
    def server_tls_defition(self) -> str:
        return "tlsUseSelfSigned: true"

    def router_tls_defition(self) -> str:
        return ""


class OperatorAndClusterMultiUpgradeTestSelfSignedScenario0(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 1,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.31",
            "initial_old_operator_version": g_ts_cfg.operator_old_version_tag,
            "operator_and_server_versions": [
                ("8.4.5-2.1.7",  ["8.4.5"]),
                (g_ts_cfg.operator_version_tag, ["9.0.0", "9.4.0", "9.5.0", g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()

class OperatorAndClusterMultiUpgradeTestSelfSignedScenario1(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 1,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.31",
            "initial_old_operator_version": g_ts_cfg.operator_old_version_tag,
            "operator_and_server_versions": [
                ("8.4.5-2.1.7",  ["8.4.5"]),
                (g_ts_cfg.operator_version_tag, ["9.0.0", "9.4.0", "9.5.0", g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


@unittest.skipUnless(g_ts_cfg.operator_upgrade_run_all_tests, "Extensive operator upgrade testing not enabled")
class OperatorAndClusterMultiUpgradeTestSelfSignedScenario2(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 1,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.31",
            "initial_old_operator_version": g_ts_cfg.operator_old_version_tag,
            "operator_and_server_versions": [
                ("8.0.38-2.0.15", ["8.0.38"]),
                ("8.4.3-2.1.5",  ["8.4.3"]),
                ("9.4.0-2.2.5",  ["9.0.0", "9.0.1", "9.1.0", "9.2.0", "9.3.0", "9.4.0"]),
                ("9.5.0-2.2.6",  ["9.5.0"]),
                (g_ts_cfg.operator_version_tag, [g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


@unittest.skipUnless(g_ts_cfg.operator_upgrade_run_all_tests, "Extensive operator upgrade testing not enabled")
class OperatorAndClusterMultiUpgradeTestSelfSignedScenario3(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 3,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.40",
            "initial_old_operator_version": "8.0.40-2.0.16",
            "operator_and_server_versions": [
                ("8.4.4-2.1.6",  ["8.4.4"]),
                ("9.4.0-2.2.5",  ["9.0.0", "9.2.0", "9.3.0", "9.4.0"]),
                ("9.5.0-2.2.6",  ["9.5.0"]),
                (g_ts_cfg.operator_version_tag, [g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


@unittest.skipUnless(g_ts_cfg.operator_upgrade_run_all_tests, "Extensive operator upgrade testing not enabled")
class OperatorAndClusterMultiUpgradeTestSelfSignedScenario4(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 3,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.43",
            "initial_old_operator_version": "8.0.43-2.0.19",
            "operator_and_server_versions": [
                ("8.4.5-2.1.7",["8.4.5"]),
                ("9.0.0-2.2.0",["9.0.0"]),
                ("9.0.1-2.2.1",["9.0.1"]),
                ("9.1.0-2.2.2",["9.1.0"]),
                ("9.2.0-2.2.3",["9.2.0"]),
                ("9.3.0-2.2.4",["9.3.0"]),
                ("9.4.0-2.2.5",["9.4.0"]),
                ("9.5.0-2.2.6",["9.5.0"]),
                (g_ts_cfg.operator_version_tag, [g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


@unittest.skipUnless(g_ts_cfg.operator_upgrade_run_all_tests, "Extensive operator upgrade testing not enabled")
class OperatorAndClusterMultiUpgradeTestSelfSignedScenario5(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 1,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.43",
            "initial_old_operator_version": "8.0.43-2.0.19",
            "operator_and_server_versions": [
                ("8.1.0-2.1.0",["8.1.0"]),
                ("8.2.0-2.1.1",["8.2.0"]),
                ("8.3.0-2.1.2",["8.3.0"]),
                ("8.4.0-2.1.3",["8.4.0"]),
                ("8.4.1-2.1.4",["8.4.1"]),
                ("8.4.3-2.1.5",["8.4.3"]),
                ("8.4.4-2.1.6",["8.4.4"]),
                ("8.4.5-2.1.7",["8.4.5"]),
                ("9.4.0-2.2.5",["9.4.0"]),
                ("9.5.0-2.2.6",["9.5.0"]),
                (g_ts_cfg.operator_version_tag, [g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


@unittest.skipUnless(g_ts_cfg.operator_upgrade_run_all_tests, "Extensive operator upgrade testing not enabled")
class OperatorAndClusterMultiUpgradeTestSelfSignedScenario6(OperatorAndClusterMultiUpgradeTestSelfSignedBase):
    upgrade_scenarios = [
        {
            "server_instances" : 3,
            "router_instances" : 3,
            "initial_old_server_version": "8.0.43",
            "initial_old_operator_version": "8.0.43-2.0.19",
            "operator_and_server_versions": [
                ("8.1.0-2.1.0",["8.1.0"]),
                ("8.2.0-2.1.1",["8.2.0"]),
                ("8.3.0-2.1.2",["8.3.0"]),
                ("8.4.0-2.1.3",["8.4.0"]),
                ("8.4.1-2.1.4",["8.4.1"]),
                ("8.4.3-2.1.5",["8.4.3"]),
                ("8.4.4-2.1.6",["8.4.4"]),
                ("8.4.5-2.1.7",["8.4.5"]),
                ("9.4.0-2.2.5",["9.4.0"]),
                ("9.5.0-2.2.6",["9.5.0"]),
                (g_ts_cfg.operator_version_tag, [g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


class OperatorAndClusterMultiUpgradeTestTLSBase(OperatorAndClusterMultiUpgradeBase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass("operator-upgrade-tls") # the TLS certs are generated for this namespace
        cls.cluster_name = "mycluster-tls"  # this can't be changed, as the certs are generated for this name!
        cls.cluster_secret_name = f"{cls.cluster_name}-mypwds-{cls.random_suffix}"

    def server_tls_defition(self):
        return f"""
  tlsCASecretName: {self.ca_secret_name}
  tlsSecretName: {self.server_tls_secret_name}
"""

    def router_tls_defition(self):
        return f"""
    tlsSecretName: {self.router_tls_secret_name}
"""

class OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsBase(OperatorAndClusterMultiUpgradeTestTLSBase):
    ca_crt = """
-----BEGIN CERTIFICATE-----
MIIDBzCCAe+gAwIBAgIUGGYHnDP0//oyoqBohgj1Rcle+/AwDQYJKoZIhvcNAQEL
BQAwEjEQMA4GA1UEAwwHVGVzdF9DQTAgFw0yNTA3MjkwODUxMTZaGA8yMTI1MDcw
NTA4NTExNlowEjEQMA4GA1UEAwwHVGVzdF9DQTCCASIwDQYJKoZIhvcNAQEBBQAD
ggEPADCCAQoCggEBAMGwRscZF0LjSaQqNU/h5cCQYhj6ApSQm1wWezCkUhlzsZhM
cABsj/qpVLsW8rB14cfjPKxWEP/DhL+x9UxfbJxg4mx2Ahr1zOWawS2KDSkGTQs+
1WgF+vlJaXdrnF6Cc84ZURGpAj8swMvoQZbhdbe78AXJx7LQ2ARmxyfXRyaa9CjJ
gGXHMBruojLHTYAXkvzT+O0iubzCHcEdkRdduOYkwT5hT5QXHMIcPl4+l4kEHjwh
GBgBCd+TrMjtzBWwrwiwCn2wNVkOqFRQ6YOOToBoLmhsMnuswwBfMVoNVtSlAlLJ
iwNDrVAbyhx5XXSJYIHjLZvH1xepK9y31TT4eYMCAwEAAaNTMFEwHQYDVR0OBBYE
FHcm9P3NM1EuuYWROS7XWA/Ey7cVMB8GA1UdIwQYMBaAFHcm9P3NM1EuuYWROS7X
WA/Ey7cVMA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQELBQADggEBAA5XkwTX
6DOYhwDnm8xn9P6XZK+rHgc9iKqebWHAfBW+5y9BUuhphRNZHFJbDOq3oegSYUi2
CiDP9JBqlZkAHvh/627PulC8Zzv06MO5svcYgLV8avHk3HC1xZhHmGj9TEHowKwD
pyzeLGDW8TGnJ6s7MpGG449C5Y3z59KLhLb7LVpXJFHDPJ4tDk73twhmQ/zIZcrU
CSAjWEqfIopskdn1c/75eJH9o3yYDaaVJ85PC82arrOulFPQhdpPBJWzRp4gu1KT
s8OVhf9n7QzUA9qJLRE3oQl3H7Jv3k+dnPOFRadUx6/IYYKJSlgC6A6Flhm+oosS
8IUOyoYX1lEhwSU=
-----END CERTIFICATE-----
"""

    server_tls_crt = """
-----BEGIN CERTIFICATE-----
MIIDhzCCAm+gAwIBAgIBATANBgkqhkiG9w0BAQsFADASMRAwDgYDVQQDDAdUZXN0
X0NBMB4XDTI1MDcyOTA4NTE1M1oXDTM1MDcyNzA4NTE1M1owFDESMBAGA1UECgwJ
bXljbHVzdGVyMIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA3JeltyLI
TXJRQbUadKl3R7O5KUJO79dE9exA+a2xKnRqp8XnPfx7A8PBAHUkVEThtJKWTpye
C5fYC1ZawKmdoV7SM5UyIjQlKVKKDgcZE8cCduaMAnPI7pZehc4odlAKRRPBM7JP
g6H57eXXTZ5wWzfN/P5XFCtklJ3LWGpM5CFUsg6cn/qNKpjck/n7vcaQli8IxjH8
QumQ4gUeBwNM1jhFzOMIlB/XNSY6D53FRHLHROs/6sUzsxGaNKsba+amByh6niV3
b8yOA6oiL3yRwi2ATAycw+5Yiu2sB6khVCM4/wVtvkitDKqVMQIv5k9NLO0QV5ur
N8YVq2ZIkFJn8QIDAQABo4HlMIHiMAwGA1UdEwEB/wQCMAAwCwYDVR0PBAQDAgWg
MB0GA1UdJQQWMBQGCCsGAQUFBwMBBggrBgEFBQcDAjBmBgNVHREEXzBdgkAqLm15
Y2x1c3Rlci10bHMtaW5zdGFuY2VzLm9wZXJhdG9yLXVwZ3JhZGUtdGxzLnN2Yy5j
bHVzdGVyLmxvY2FsghkqLm15Y2x1c3Rlci10bHMtaW5zdGFuY2VzMB0GA1UdDgQW
BBQNOf0wmI6y60b6J70Z7wIUUe7eTTAfBgNVHSMEGDAWgBR3JvT9zTNRLrmFkTku
11gPxMu3FTANBgkqhkiG9w0BAQsFAAOCAQEAdTInSjXYhXzlExU9dbTLiXsm/+bJ
/1yiUA5M060JiWcHhE4JJRrktw2/vHwRqowhaiIebIk8etAaA8hcEuE74lcOKte/
bep40tZF0LTuOuxsp31tFN8aiWizmO2GAmmhEgMHEUg0P8mdRUPTp6ELFF28UnDt
YEYlMPQaSHlg6J3p4iLhbjQq5dikN8WiCbkA2izVDgU6r7/Ld9ntSSex2Biq5FfW
DcInXuZZj398XuK3jjpZtVRI+t3aSLkhNtFv4yT9Mnii5asvzoT8kun1hCfMMVnW
W9B+jpjvAmaGp4FNSAqEIEYiJ4HzfpAyi2dSOazsdYNj89q6GUuhnqFRMw==
-----END CERTIFICATE-----
"""

    server_tls_key = """
-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDcl6W3IshNclFB
tRp0qXdHs7kpQk7v10T17ED5rbEqdGqnxec9/HsDw8EAdSRUROG0kpZOnJ4Ll9gL
VlrAqZ2hXtIzlTIiNCUpUooOBxkTxwJ25owCc8jull6Fzih2UApFE8Ezsk+Dofnt
5ddNnnBbN838/lcUK2SUnctYakzkIVSyDpyf+o0qmNyT+fu9xpCWLwjGMfxC6ZDi
BR4HA0zWOEXM4wiUH9c1JjoPncVEcsdE6z/qxTOzEZo0qxtr5qYHKHqeJXdvzI4D
qiIvfJHCLYBMDJzD7liK7awHqSFUIzj/BW2+SK0MqpUxAi/mT00s7RBXm6s3xhWr
ZkiQUmfxAgMBAAECggEAHNuT2MTqmkMZwTRNfTSMP6uRzgV4KSUqxtc82szvr+Oh
7kfUDAZjG2d48LPiaEnIkyRYGHmhrVeUcks+PRUTY4BxlEDw2YujzueW2uXv0GgI
+QAJEesOvrOaMvv4zB2Fcmr2q+ooP8qpVQdr5qITBcPjpTSqtJ9GnbCU4QCrBW7e
pYppsYCjK4bJ3fclVetYcSVyOlxATOhvCNE/PUvTz1afbzO+ycXzfG7gvH67FPkz
wpXKd6M9cpF7pKPr/mA2v/3KA0hym5FdUJ5LmqmDm12thAOjPqdmKZ2w3PMp8DQT
ZYV5dLPO7+bAM9lUFN7BbxqMLPeLcuTrQRmt3EIHnwKBgQDvfsV0K5sJkqcUm4jv
oukklZteDKfC39Ngnpxw2yLkZk3kki6HLr9Ec60ZC+zEfva0TsszV4Jevwc46UbX
jNeknjnS+MlS7DCoxFLOCNKYcWc558wd+xoxvJfzpCUg/o9FOahe50ksOdfs+VjZ
Uyoz0PfK0F2yZqqRfLqhxc8I2wKBgQDry2NWeHQ3Zvnn9REsrf3xZkvfpDiNw958
yRxxjkLw49EnxSDOVA02flaXaTMZLOjJDPsn/QRdA3rehXdi13QEKm9scopS011I
opc01GmeZacXHk01KEUpAA4iAyiLjtVAehtLD6IwMl4VK+0AdEZtlP19UfstyhCJ
3rV5+VI2IwKBgAbZJyhFPu+tI93w/J9tlyEWrhSoY/usjszKfEq12W+ShVOt4mq3
KXz5mc+HicOspb1OK31SWUYATGKSORZczqXEaH7h4k/etR1+T6fYlL0LMRp0yF9r
GLqnW+j5np4s6UffeDMOhgcfuE7sal/8gs5sgUlpX+SEDwiN+oA5ucvVAoGAGlf8
3zpR8aaloQ62PoGp74B4VaIpV11czCBD90PnorYxNfpGMgcd+sqergfo15U25M94
d/1CsYmj/px2vCpKIfUDweACKELJF0ZjElnw+utsgZ63DYtUPsJs0cv8iasJlEyQ
JBC5FB1seX0Q244iGDgfIhM4tuLuehjRubDrSHUCgYBvdc6IYL9b3r+Pl2BEaMNF
K+SqM4ofTVVjUZhM2cy2U+0Ltn+g9o8mMyMc7LbWET9Um8s3nsaAuiYfpPI31MIV
pgttVoSdALqhUhJnStnq5snqAkII8cuPKFvEE98DFAnLRviLNNEr+iQJPlfhybh3
wlQSJe7iCRNA5cBM7BnFXg==
-----END PRIVATE KEY-----
"""

    router_tls_crt = """
-----BEGIN CERTIFICATE-----
MIIDgzCCAmugAwIBAgIBATANBgkqhkiG9w0BAQsFADASMRAwDgYDVQQDDAdUZXN0
X0NBMB4XDTI1MDcyOTA4NTE1NFoXDTM1MDcyNzA4NTE1NFowGDEWMBQGA1UEAwwN
bXljbHVzdGVyLXRsczCCASIwDQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAKCP
Cm/erdPHk1sW5Pjv1MEedzsx+eE1md+/wVVGZYebbBAX6IMZa7uQKR4OftQo9ovc
7hqkUKFw0Imf9CcLQ6SSXjvRZm41qZn9Cs8IAA5+3dNAKlPlFNhCal9lw8ABR+2T
3yr7epWYFuQ/kFyLAGy7aHX3GvrbPqiTbG6Y9Vd4UEzUQ7AgAW29FFlBv25vM7vX
jk6KcB+Gnw2KZww/KNYNIlrVnT13Ll3a48+eWHnQ2c9XFVbGJWy2Eu0evIbYBBIY
VgxRQf7Yw8bOE7NBt2DYLKTs+wIWs+KqkvtLe/StwJDVDAY/QVYwJsb/IkOE5S6u
d17kNdeYwNkalUP36uUCAwEAAaOB3TCB2jALBgNVHQ8EBAMCBDAwEwYDVR0lBAww
CgYIKwYBBQUHAwEwdgYDVR0RBG8wbYINbXljbHVzdGVyLXRsc4ImbXljbHVzdGVy
LXRscy5vcGVyYXRvci11cGdyYWRlLXRscy5zdmOCNG15Y2x1c3Rlci10bHMub3Bl
cmF0b3ItdXBncmFkZS10bHMuc3ZjLmNsdXN0ZXIubG9jYWwwHQYDVR0OBBYEFK4N
p3/r+s9Wj5cscFP5ynUvgD+5MB8GA1UdIwQYMBaAFHcm9P3NM1EuuYWROS7XWA/E
y7cVMA0GCSqGSIb3DQEBCwUAA4IBAQB9n8WbRCcAm5txP803MCpvWnBx3sErETSN
i1luRbXT++87YPnnfzvnP9wgZ+Wep75/kSRIYl6xTyS7Pytk041ITUgYoKarQ3kb
1AU9Mn/mykMaK/UvN/gzNcs3/TUlNfunJVmVHQgyMz9O3YoTCipx8GHtpjR8VtmA
XZU0gjOv3wUzDuL2pUEcimBwZkledzeCkyX/bvefIq0o+WMxAtQPKgzGukE0U1M8
1jF3FeGz5DFxUIzRNvf9WNg5bG6vTB+Lk3pv6/OS4P4CmRuPvxmirg4JRcPOxaDl
lkJAp1NbPDi7Jv67tJ2IflbBIj/OMTv+DmLCoe7kKzv19iTgpJ3N
-----END CERTIFICATE-----
"""

    router_tls_key = """
-----BEGIN PRIVATE KEY-----
MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQCgjwpv3q3Tx5Nb
FuT479TBHnc7MfnhNZnfv8FVRmWHm2wQF+iDGWu7kCkeDn7UKPaL3O4apFChcNCJ
n/QnC0Okkl470WZuNamZ/QrPCAAOft3TQCpT5RTYQmpfZcPAAUftk98q+3qVmBbk
P5BciwBsu2h19xr62z6ok2xumPVXeFBM1EOwIAFtvRRZQb9ubzO7145OinAfhp8N
imcMPyjWDSJa1Z09dy5d2uPPnlh50NnPVxVWxiVsthLtHryG2AQSGFYMUUH+2MPG
zhOzQbdg2Cyk7PsCFrPiqpL7S3v0rcCQ1QwGP0FWMCbG/yJDhOUurnde5DXXmMDZ
GpVD9+rlAgMBAAECggEACUddnkueEUDgTcbpL89mZMxkA2usNJ0Xzyk3slFUQUMU
upUIds5uM4Oi3gM5m6e2tdcBpfsB046fkpsymDC2YElQK6tP54XE5IAN6EXeq6Jz
zdylsD2rLSNLc/IU3wtZPYw1PQ3q+UBgmaC9xkdchywedEDLUkd/hmz8pXiZUcxs
AhK88qh8kd0y0W1+Z1/3zBodkT3xWtksaeEOidANz665Fh2mqZ67semYVDIK5/MI
vl0rU5HR8UZvuCxhcRHLABGm3mV/GwZGId6e0BmOfZRPF/p5/EZDW5G4YVrhfGJl
ZXZTfHAspSLPNrOUod6YfHiqQswmzuyxkp19Vh+1wQKBgQDNtRtshfdnccpsY14J
lSECiRqnvScuSPIS3xttJDnYCYbSauaN+1APR9iDsFoTNdtTviKXKYKDe3bY7Z50
4jAHx3IVrALmeORb+7PsD/KNXWM0v5Gbu+LFYnVVdUfmb1Yel63Ob82x+SEP1XQM
bS4rWA8TUsi5MVaaNovAiNs1QQKBgQDH0CaKkTDFODGg5Otu0XqiOvB5LfQFnztH
qt4AhbUNfq8qNBiH+gto67AtojUJ3ovtCiRUwqq89Lgh7vTuQAvR9zUw1RSvLbLa
V1yNSCLYfPHq9fOjLRv9StU+8BaIK52vMnZMgIb4SP4lOL48Vw2EMZpSlTZ/8H3Q
AYPj0xCYpQKBgE2SgFb6ZgPCa31YM3wVmq8kGMMsl3vi0ja/n84WnSGU5hyvZ2Yf
YV+BzmpKtI0OADmiN9UdODRw+K5xXRHiwg4M7j6x8R4MdMEjOARLN0KL9v9LRpOd
1LRunqStZ4HEdotu04JHsx/sPMWFzw9olMKPoI++5ibALlJVQ3pFobSBAoGAa9aR
FnPpqGb9keI6liKEVw4MPrpoWqhna/RxwEJpRpE6pEJdgvWWNtgMy3Qhv+PWDOZO
WVh+oWBKCDZZBkSWnIkYsfQuJ6U4Q9KmZrGp7MDyJ2b6SPTNiFRc5ozY/EDd53IK
mps7PMDYOOp48UPiTZgfu7ZAJah7nklTDyAYcukCgYBn8+UdUwXQoBb1GtMjLPs5
GEtf3+yubI/Mw3n3z14Ovs83IwOWI8K9v7tjOrpa4dSEM+MDL9mUlRbAO3wGxIhc
5HkM6/GnV2l4TMMhl12zazG0hlh+UokzU8L+ApeWvB334FHXEkK/2zgXLO4V/3UY
mBypsER1RjkJWIbKUnr+PQ==
-----END PRIVATE KEY-----
"""
    def create_tls_secrets(self):
        self.ca_secret_name = f"{self.cluster_name}-ca"
        self.server_tls_secret_name = f"{self.cluster_name}-server-certs"
        self.router_tls_secret_name = f"{self.cluster_name}-router-certs"

        ca_cert = f"""
ca.pem: {kutil.b64encode(self.ca_crt)}
"""
        kutil.create_secrets(self.ns, self.ca_secret_name, ca_cert)

        server_certs = f"""
tls.crt: {kutil.b64encode(self.server_tls_crt)}
tls.key: {kutil.b64encode(self.server_tls_key)}
"""
        kutil.create_secrets(self.ns, self.server_tls_secret_name, server_certs, "kubernetes.io/tls")

        router_certs = f"""
tls.crt: {kutil.b64encode(self.router_tls_crt)}
tls.key: {kutil.b64encode(self.router_tls_key)}
"""
        kutil.create_secrets(self.ns, self.router_tls_secret_name, router_certs, "kubernetes.io/tls")

    def delete_tls_secrets(self):
        kutil.delete_secret(self.ns, self.ca_secret_name)
        kutil.delete_secret(self.ns, self.server_tls_secret_name)
        kutil.delete_secret(self.ns, self.router_tls_secret_name)


class OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsScenario0(OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsBase):
    upgrade_scenarios = [
        {
            "server_instances" : 1,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.31",
            "initial_old_operator_version": g_ts_cfg.operator_old_version_tag,
            "operator_and_server_versions": [
                ("8.4.4-2.1.6",  ["8.4.4"]),
                (g_ts_cfg.operator_version_tag, ["9.0.0", "9.4.0", g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


class OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsScenario1(OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsBase):
    upgrade_scenarios = [
        {
            "server_instances" : 3,
            "router_instances" : 3,
            "initial_old_server_version": "8.0.38",
            "initial_old_operator_version": "8.0.38-2.0.15",
            "operator_and_server_versions": [
                ("8.4.5-2.1.7",  ["8.4.5"]),
                (g_ts_cfg.operator_version_tag, ["9.0.0", "9.4.0", g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


@unittest.skipUnless(g_ts_cfg.operator_upgrade_run_all_tests, "Extensive operator upgrade testing not enabled")
class OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsScenario2(OperatorAndClusterMultiUpgradeTestTLSSeparateSecretsBase):
    upgrade_scenarios = [
        {
            "server_instances" : 3,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.40",
            "initial_old_operator_version": "8.0.40-2.0.16",
            "operator_and_server_versions": [
                ("8.1.0-2.1.0",["8.1.0"]),
                ("8.2.0-2.1.1",["8.2.0"]),
                ("8.3.0-2.1.2",["8.3.0"]),
                ("8.4.0-2.1.3",["8.4.0"]),
                ("8.4.1-2.1.4",["8.4.1"]),
                ("8.4.3-2.1.5",["8.4.3"]),
                ("8.4.4-2.1.6",["8.4.4"]),
                ("8.4.5-2.1.7",["8.4.5"]),
                ("9.0.0-2.2.0",["9.0.0"]),
                ("9.1.0-2.2.2",["9.1.0"]),
                ("9.2.0-2.2.3",["9.2.0"]),
                ("9.5.0-2.2.6",["9.3.0", "9.4.0", "9.5.0"]),
                (g_ts_cfg.operator_version_tag, [g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


class OperatorAndClusterMultiUpgradeTestTLSCombinedSecretsBase(OperatorAndClusterMultiUpgradeTestTLSBase):
    ca_crt = """
-----BEGIN CERTIFICATE-----
MIIDBTCCAe2gAwIBAgIRAPik45sGXSmVwNZwA+L6bDwwDQYJKoZIhvcNAQELBQAw
GzEZMBcGA1UEAxMQbXljbHVzdGVyLXRscy1jYTAgFw0yNTA3MzAyMjQxMDNaGA8y
MDc1MDczMTEwNDEwM1owGzEZMBcGA1UEAxMQbXljbHVzdGVyLXRscy1jYTCCASIw
DQYJKoZIhvcNAQEBBQADggEPADCCAQoCggEBAMps/WGiv0VobVQ+gmdynTjj1dXh
9EGwcw9WSH7jGCE47qATXt4WQ7ew9JOquR6/3yjQyaKsDl+9KZNTL+pgugNo0+1f
2zhs2tL0I+WbJ4SHC/XLKVLRQUjO3ewxKqFuofC7pntTmaazV5JCrUL8IT4oW+tg
CAFbQ8vtQtFhfr5BJjJ9GAB8OpLkrC5Zj/ML1wD84x1hjtaGzmZYkOIW1jUwT7qE
9+EWy56xuAylDf65i9CIesdjC8eXP/wGL/Pmzj2TG7QPqcx9vqvyyIN84dniGGJE
9me8PN0Z0zMIVvwwrHgL9UZo+kppYlamEOeKZVPrn+e2nbgNxGw5htr/34sCAwEA
AaNCMEAwDgYDVR0PAQH/BAQDAgGGMA8GA1UdEwEB/wQFMAMBAf8wHQYDVR0OBBYE
FH4MXwioVHUE3aaqob/oU3LJpCoNMA0GCSqGSIb3DQEBCwUAA4IBAQAuzKxzgfBM
6Wgj2tjyeu8zusCmp8hkgLjsgkaUb6fUz5wagT6N3BawjZsOtB3pfhHngETEGzPV
Isxcf5s7p10wJSea+oAu4IxmrSQyvi+jc2Q/Htg5jyE8rgfVmAGolUMyFWCJeRd6
lKlPPSjqDGsTsbpSIJVsnGXg2RzcBHLg8OlULKDVxl3OdA+1wPhTw+yOM4cY3Mj4
/C0+ih/+2RwbPzuU22jCr4pT/S6iXTTLTLYTxJubW3Mn3e50Yde9fxX9r2YgvOFW
WNcH54NOkbU4b01yg1kLEC2+L/VbGO3TQqOkDWlknzgUQbhENN9sGac4SYS6DD6W
KWA+9vO5RYYm
-----END CERTIFICATE-----
"""

    ca_key = """
-----BEGIN RSA PRIVATE KEY-----
MIIEowIBAAKCAQEAymz9YaK/RWhtVD6CZ3KdOOPV1eH0QbBzD1ZIfuMYITjuoBNe
3hZDt7D0k6q5Hr/fKNDJoqwOX70pk1Mv6mC6A2jT7V/bOGza0vQj5ZsnhIcL9csp
UtFBSM7d7DEqoW6h8Lume1OZprNXkkKtQvwhPihb62AIAVtDy+1C0WF+vkEmMn0Y
AHw6kuSsLlmP8wvXAPzjHWGO1obOZliQ4hbWNTBPuoT34RbLnrG4DKUN/rmL0Ih6
x2MLx5c//AYv8+bOPZMbtA+pzH2+q/LIg3zh2eIYYkT2Z7w83RnTMwhW/DCseAv1
Rmj6SmliVqYQ54plU+uf57aduA3EbDmG2v/fiwIDAQABAoIBAFmhtvRzltQJ8Fcc
MqvRsVJOeBHzsi8gDWKeJw2gSr/zMJ5K1XfrTeLZhQKZ1syJ0yuEf7P7EYZNAUrN
y+qxF8yIr0Ztamlqsd404mw5rl7dWWtloQ+dvpWZm+SIxMKkdSpePNJg6ylIQh9u
TL/bWnDmeAUaCZFoMvXqQerqi6976bEoTDjoeKmK7YgcXzufm6OH3AlsI4haTNwq
lcga4cSaCT2k0ve1yN7wrJvQ5zpAqRkM7eNFy+WeFzsTu1K9wAbFGFz1z3O469XT
U7hXAIKScRq7mFpSN7EMGAYJm6vG9hWiatlTG4/C1sdNeqiJy3r2eut6M8ksdzLT
emabSYkCgYEA7ydN8C+R2VUCUCfOM4gd6Rsf5fT0Yu7ZWZO74URjg8bHrLHv7V/C
myYxerl55WXlWZCwJRCFjSHdH4a0lEG3+z/GPfeDFdnJf5okaEyov7Rt/n1WdyAa
OOZOY8JQ7aI0E5xJhl34Zmx5d1mtpxUlQFvPnQXg07canByAvJ5GBnUCgYEA2K9d
7ykeYR48NXuVidCTXwKHm0BC7JzujzRhVVYdTuy9HajSJzKj+jpUL/tjGObQjL78
FNG/f8ObS8EUrmck24tBwQDfW62Jr1+WKtPZtchyFXSSKJj0wU3b5WwGyK0yfaEL
w7/i5D5bIiZQ3rXsUh8O2+YjmpnP1yp2HN7Njf8CgYBCpOpUL31uWgEuAxm0RI9J
QXTPKUbdNwA8tS2bJeqxczh7iI560L+zap5JO9ybL50NK4PaoFMHNFOhkHFMC1BQ
5MHCzPGrqig7teHFau9vlME+NQFeawTDrHJV3HWe8x+eA2R93Dv3luz2dHgu4nai
C7O8Z0Hy3ci9jjfV2CldbQKBgGTPIaqUvLd+M5DGUEnvqcB4EtLd0MT4NdyWB/qF
t9jyNwHHew4Vd0iBPo2cLPx21evFJs4gzzEHtgZGa/K/tdwWK585YrDqSbY3iEug
iBcUjE8DDsSodKZCLN3NplJSkbz04d5ONabug8Odg945URrbrDQOG95fVNzLumuw
FH3NAoGBAOnehLTsMmRSNBFzWJNs/BAlksTzXWoy/vJzr3FqiWiGSVHo1mcCXhlE
T1moCzGMj85amQaLiT+d35jCeOs3c17k2/+yac4vQwGD2f8se4EU/U7ovhrcoPKh
+IGCOfl5wQ0L8X9SUEx2wWYcJ5rIf3EDSnbYUQNCZPQiTHqCFJS1
-----END RSA PRIVATE KEY-----
"""

    server_tls_crt = """
-----BEGIN CERTIFICATE-----
MIIDwjCCAqqgAwIBAgIRALdcLiFWpxfRbi0qEAzt6vgwDQYJKoZIhvcNAQELBQAw
GzEZMBcGA1UEAxMQbXljbHVzdGVyLXRscy1jYTAgFw0yNTA3MzAyMjQxMDNaGA8y
MDc1MDczMTEwNDEwM1owGDEWMBQGA1UEChMNbXljbHVzdGVyLXRsczCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBAL619qd1mWwGhtmMNQTObX3z9WVt3hHT
zAIbt0sLaSL8MuXC/eUutwmJ//z3yYXJAJQxOBlC12Ou0E1veDBywN+N7b/713xS
73ZJBr+Un/z75eTbgZX+DRiwtzQZSnylVXVucXg3iFtUx9M0xMxDV7kdJM6w9KsF
bZIy46eXVX2kT+ii9yG7PU4ESbIpBJKlVvko/1q29ujIcQvjupWCJ0TqeIHzUMG3
gcp8c8VfmF2HMjXxz2qP3LWIJIYcTshkWKHgrmLobE2dFtGG/A4xZ6sijLS9G1RW
DqMclrDCdJr45TEC5mQ9qZedR+sK9yRVagcUsYn4dS4TkHbh5rvQFPECAwEAAaOC
AQAwgf0wDgYDVR0PAQH/BAQDAgWgMB0GA1UdJQQWMBQGCCsGAQUFBwMBBggrBgEF
BQcDAjAMBgNVHRMBAf8EAjAAMB8GA1UdIwQYMBaAFH4MXwioVHUE3aaqob/oU3LJ
pCoNMIGcBgNVHREEgZQwgZGCQCoubXljbHVzdGVyLXRscy1pbnN0YW5jZXMub3Bl
cmF0b3ItdXBncmFkZS10bHMuc3ZjLmNsdXN0ZXIubG9jYWyCMioubXljbHVzdGVy
LXRscy1pbnN0YW5jZXMub3BlcmF0b3ItdXBncmFkZS10bHMuc3ZjghkqLm15Y2x1
c3Rlci10bHMtaW5zdGFuY2VzMA0GCSqGSIb3DQEBCwUAA4IBAQBjGUUcKqKFoNbq
LsYUTJJfHnZQr8fut9Au32W/1hV83/cf4OSZIrVV28+sPN/dhPyq8QgPx2lZi7Tz
haUzR5IK3FXoooctz8vM3tll0gEx7GiZAMYIkRZiOv2DPRWH3bQcsqpCyMi4sPdi
QcKg8p00aFk4akHe+6YMGEv/RRa6RFVaNN0tyYfDxBka4vtCpyNPhO4YkngPnJgr
6vMR+6bLqqi/wIDwRqcR0TtNGgPnFrp8A5L/0Es4miVJRrAReEpFieFBbLmtUcwJ
sgWrzRlhx3SxYQ5qGB3W7YfJlo37eSRbPRrP7dXs7DuN7BkRXn9Db0kIRS5zs/9M
dWOXnXfq
-----END CERTIFICATE-----
"""

    server_tls_key = """
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEAvrX2p3WZbAaG2Yw1BM5tffP1ZW3eEdPMAhu3SwtpIvwy5cL9
5S63CYn//PfJhckAlDE4GULXY67QTW94MHLA343tv/vXfFLvdkkGv5Sf/Pvl5NuB
lf4NGLC3NBlKfKVVdW5xeDeIW1TH0zTEzENXuR0kzrD0qwVtkjLjp5dVfaRP6KL3
Ibs9TgRJsikEkqVW+Sj/Wrb26MhxC+O6lYInROp4gfNQwbeBynxzxV+YXYcyNfHP
ao/ctYgkhhxOyGRYoeCuYuhsTZ0W0Yb8DjFnqyKMtL0bVFYOoxyWsMJ0mvjlMQLm
ZD2pl51H6wr3JFVqBxSxifh1LhOQduHmu9AU8QIDAQABAoIBAQCXUMtO3TkJBDi0
ndFnad5oREvd2YmMfY7t0U3iZkBaON6tfMR0tlcWY39QGM61ruEds9u991HszsSJ
3DrxvesYtYUBgfHcu/Ux1glbsrIqSTeEGUI9X2DGyVfjJ3n5YjlnPmUbrHwtcxxF
kSXgqEIPJ3eP48mNdHYWvPfyflmfoIPIVZ+T+gq4I1yw2GEuytMCMaKlr9zC27JQ
xgGk4ZYmj5oxHxFQIUTmJtzQGmXt6LOeGtY7JmnTXy32UFnxJBO1OIeFHeZruEpN
TCKWBULc3+JMoxKY2S77scUmj4Z7+m+qVLM79E9uzqUhcPWhS39OMuJx/6hFRfsD
qD6HjiKdAoGBAOTrufXNK/QPQQ7Zi4iyj5HUpQvmptmQ4KGQLWaBJ2A2hWyTvxAT
1zXbOkKJWb7PnQJa/Y1NA5oy9e4ZMBEb0lpvuBYZLsGNTdtRfqDhc5d8iakj1FqY
q2GgyOes4GzlWI/0MZZp2kar1957XeH0HPaCadJfN1d9qlPDQ1tGytk/AoGBANVF
JVgRl3hMydAgI39V4bkqGuBjpsk8vz4hFHjhejd+7gZ1EDK4lguSaKHzXZruGwRs
jL3z9CnYlDRwYx6WNs1CFXg5oRzMYKuRfPE9+7AuRMQOINOeZkduDVFJREvgzs31
teCyR+D1YXtMDTUUVIKnT5aKfCQZc1zXhdHVt9XPAoGAG2Gy0Zrj+gJlFsCgtw4c
rCXSRBUnufhhVhHnzE4AhrqexPj7LpIg+NkrI9BIAtHUhvaZQ3CF0Mjtr7gljrZU
N+sLCuGiPRaIzIX37ohpCiKkWK3ndZqzTS8tOMDt6ziXmkhsMgChUji1zm/VL/TC
Dh5VSAuJlBZ87B38DYvvMYMCgYEAknv52HHMzg5AF7nbZ9y/O4VWDIWly1b0LGJg
Q17rqS5/ouPJCm2KccxQHdygkIe2+uTsDpR/QjbGJPaEvj+CyJz5TiiXJsaiJ3W4
kaJ3O12sUdU6at/DdJB8iTZ/uHZi1UhNclZB1Jv2Js/nymt6WHs+yTG5brokaMKH
4cafjDUCgYBRZu0NgpWhwHQkI7KORxSI65xNM1QEHIwsdncpzeybz3NsmrETaxVu
mC84sQzIrp4jNciuwFYtQfoJt8D1ZeOTJJDsiauP+O3rWP/K3Cos1KxAdmvh1p00
9jshy28A9VWWVCBT4NccQsMWnH67UudJ9dJKUG1YgCyNYuHizKfzlw==
-----END RSA PRIVATE KEY-----
"""

    router_tls_crt = """
-----BEGIN CERTIFICATE-----
MIIDkDCCAnigAwIBAgIRAIIXtnRRZ/zglxynflaSWKMwDQYJKoZIhvcNAQELBQAw
GzEZMBcGA1UEAxMQbXljbHVzdGVyLXRscy1jYTAgFw0yNTA3MzAyMjQxMDVaGA8y
MDc1MDczMTEwNDEwNVowGDEWMBQGA1UEChMNbXljbHVzdGVyLXRsczCCASIwDQYJ
KoZIhvcNAQEBBQADggEPADCCAQoCggEBANK5InKccEyL65rIJgxHNUvxq7GMJNwk
+UIu7V9qsESPdwLkDvQdWpyDO3VBvwLZhodnehNuZheEArrJ6zp6IK9BWyR8LzzZ
CNGrEcqAsXJ2rWtobWt1daQiBylpTRD9Vfp182r7XZG93Stg1CW8/kGMz7qE67lK
Aldt/hp9XtNYXWaDG7J29qm9QNWLGdHkwemKqrHB9aJKQec0TaRw3WqT50El1UmW
M0I85wLpuZjzvK/t1q8urygJ42r3RClnG0NWJB9RMRUit+/h27WGGPL+Kolz9EjR
VQOWgZDYyKZ5OBqb8JTffz5XQ7kRai8OzaC+WbG5GEnrp48r3UBU9c8CAwEAAaOB
zzCBzDAOBgNVHQ8BAf8EBAMCBDAwEwYDVR0lBAwwCgYIKwYBBQUHAwEwDAYDVR0T
AQH/BAIwADAfBgNVHSMEGDAWgBR+DF8IqFR1BN2mqqG/6FNyyaQqDTB2BgNVHREE
bzBtgg1teWNsdXN0ZXItdGxzgiZteWNsdXN0ZXItdGxzLm9wZXJhdG9yLXVwZ3Jh
ZGUtdGxzLnN2Y4I0bXljbHVzdGVyLXRscy5vcGVyYXRvci11cGdyYWRlLXRscy5z
dmMuY2x1c3Rlci5sb2NhbDANBgkqhkiG9w0BAQsFAAOCAQEAX0EjSR40eo63M0c0
EyPLC+uxi+RwOn7Kx19uaFZQYo5gqcXj61fi3ao49PD/y6t05X4xvcgeiA3QMwuL
H5JJKEwhMcIi1tb86nex6rJZaXKFzl9+BT3GrJ62MGvxfE9JYi0AMWxCo/sG6o35
31VAg4b9URDocYjTgivW5izXuSYUX86VF9pEUX+O9g58kmnihvMiV8n5hJP60unJ
hTMMvAYmztz6Bc8qfi5X7Nv4VrOTETrEVsq/XN3fmZ1lYv4dtH+IwmZoK3axrm6M
u0FNGudI6bF/vMVB3gyn3QXaWOJpj24FwryxwNczmttrxUTXLw/6f2pXpRowp2uT
60O0Qw==
-----END CERTIFICATE-----
"""

    router_tls_key = """
-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA0rkicpxwTIvrmsgmDEc1S/GrsYwk3CT5Qi7tX2qwRI93AuQO
9B1anIM7dUG/AtmGh2d6E25mF4QCusnrOnogr0FbJHwvPNkI0asRyoCxcnata2ht
a3V1pCIHKWlNEP1V+nXzavtdkb3dK2DUJbz+QYzPuoTruUoCV23+Gn1e01hdZoMb
snb2qb1A1YsZ0eTB6YqqscH1okpB5zRNpHDdapPnQSXVSZYzQjznAum5mPO8r+3W
ry6vKAnjavdEKWcbQ1YkH1ExFSK37+HbtYYY8v4qiXP0SNFVA5aBkNjIpnk4Gpvw
lN9/PldDuRFqLw7NoL5ZsbkYSeunjyvdQFT1zwIDAQABAoIBAF8jcRcX5GfbAGuo
k+0VNU3tddw6buXp15P3Bfr/e0fpEg1/E/VJT4C8i1q8bVYHCgMWR/p/7J4PXwAg
bBDB1QHrWxAU9WrefmnTd7va8MLCLDgDNOvAY3XQWzVqMn5KMsha+qTBipRjiXit
Rfsn4g+MWLlYi+jjLj11DrW9BtQaDke7fJcG4jOFMhvqlFzS1H1h4hDBzSz/55s+
q3zC9aPrYGcncmlnUjnWDjyvySl7YphC7ptdLceIYU9uxBQiIuEiqA3jFF7bXFaC
A5YEZINLx0blH7Pd7CRJKHaOtnjjiLYc6qqVApkbQjGClXatIkBs85+NO2TQOxys
SIYievkCgYEA9InWo9ba4miQrzIxlbNzu/BXCg5yWmaOJ0Md+Y6ZJCfYJ5O+pgQG
r93f8/5L3fbjvA2lgV8vpBHlWLL0VmDQ6GY8v0UUcLry6jVkc/ZF83Gpsy0ZZf0n
ucj7OVOeKIRtzDPXNOTkVfuFsNRCWTzbdL1rDTXVSaS8Dd6xDnRzlrsCgYEA3JmN
97O2O1KqK9SkXGdDN7hGXRHq4/9Ky0R5G+5VmtVHTO6G5cIUneH36YVAmc6vIHwH
MctFFmoOSDAFUiwHhUvY2XLu0mIhp/kOngmJIQjtlzPTLwEGsRK10dLV5ECnpJva
740Qoi/l9HrrrZpnqmjfsS/x76kOe04LRmD8jf0CgYEAtVGnN2DOd+Z0sZMUNR4U
iJwSzcAchm9YpEAd77cOUkLBAceJK2v80VZBApfiBTlVb1DnEWBU4ODsH7LBfXLY
NBnqnSBJobTc/VCNpXZMM9BpthOQq5DkbdtafA1GTmCzUSB3SB8YN7ECVjVs5OI9
DviMcgUimzJsxhQDUOaD9pUCgYEAtfzwA/N8WQBfZv1sAtcluugJFGrdru5Dk+GP
UB2U/cEJq0v0ecNdIsryrPHDU6ARFel8PfYFrvIbii4jlaDBK5Rg9zM8Fs/iGCL9
jh8rw9cLuvuEM3jTPm3DFbCryDwWkVQKkCl+GW8OdFyb5YJGF22hbRmyrBZ53fuK
jashbzkCgYAo/swt8+HVJOgf2qF+wD09UKH57U0v1nI4xDoLa2RdyfbBd9r4pkQy
2MztcyWfraV7muhxafjEoXiQCoy8mJSr7DLulOjsrOS06To9+yj70fvcoBv6y5Kh
ojW3VQKolyIexOrchCbc4knIWoaMsas+cpej8vXTLlvuG4QicCg3cg==
-----END RSA PRIVATE KEY-----
"""

    def create_tls_secrets(self):
        self.ca_secret_name = f"{self.cluster_name}-ca"
        self.server_tls_secret_name = f"{self.cluster_name}-server-certs"
        self.router_tls_secret_name = f"{self.cluster_name}-router-certs"

        # If used and overlaps the the server one, then ok, we will find quickly out
        ca_certs = f"""
ca.crt: {kutil.b64encode(self.ca_crt)}
tls.crt: {kutil.b64encode(self.ca_crt)}
tls.key: {kutil.b64encode(self.ca_key)}
"""
        kutil.create_secrets(self.ns, self.ca_secret_name, ca_certs, "kubernetes.io/tls")

        server_certs = f"""
ca.crt: {kutil.b64encode(self.ca_crt)}
tls.crt: {kutil.b64encode(self.server_tls_crt)}
tls.key: {kutil.b64encode(self.server_tls_key)}
"""
        kutil.create_secrets(self.ns, self.server_tls_secret_name, server_certs, "kubernetes.io/tls")

        router_certs = f"""
ca.crt: {kutil.b64encode(self.ca_crt)}
tls.crt: {kutil.b64encode(self.router_tls_crt)}
tls.key: {kutil.b64encode(self.router_tls_key)}
"""
        kutil.create_secrets(self.ns, self.router_tls_secret_name, router_certs, "kubernetes.io/tls")

    def delete_tls_secrets(self):
        kutil.delete_secret(self.ns, self.ca_secret_name)
        kutil.delete_secret(self.ns, self.server_tls_secret_name)
        kutil.delete_secret(self.ns, self.router_tls_secret_name)


class OperatorAndClusterMultiUpgradeTestTLSCombinedSecretsScenario0(OperatorAndClusterMultiUpgradeTestTLSCombinedSecretsBase):
    upgrade_scenarios = [
        {
            "server_instances" : 1,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.43",
            "initial_old_operator_version": "9.5.0-2.2.6",
            "operator_and_server_versions": [
                (g_ts_cfg.operator_version_tag, ["8.4.5", "9.3.0","9.4.0", "9.5.0", g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()


class OperatorAndClusterMultiUpgradeTestTLSCombinedSecretsScenario1(OperatorAndClusterMultiUpgradeTestTLSCombinedSecretsBase):
    upgrade_scenarios = [
        {
            "server_instances" : 3,
            "router_instances" : 1,
            "initial_old_server_version": "8.0.43",
            "initial_old_operator_version": "9.5.0-2.2.6",
            "operator_and_server_versions": [
                (g_ts_cfg.operator_version_tag, ["8.4.5", "9.3.0","9.4.0", "9.5.0", g_ts_cfg.version_tag])
            ]
        }
    ]

    def testit(self):
        self.runit()
