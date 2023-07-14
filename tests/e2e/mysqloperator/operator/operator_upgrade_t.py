# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import json
import time

from utils import tutil
from utils import kutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from setup.config import g_ts_cfg

def change_operator_version(version=None, store_operator_log=None):
    """Change to the given operator version"""

    # Get name of current operator pod, once this is gone we know the new one
    # took over as it only be deleted once new one is ready
    pods = kutil.ls_pod("mysql-operator", "mysql-operator.*")

    old_pod = kutil.get_po("mysql-operator", pods[0]["NAME"])

    target_image = g_ts_cfg.get_operator_image(version)

    if target_image == old_pod["spec"]["containers"][0]["image"]:
        # We are already running the expected version
        return

    # Patch version
    patch = {"spec": {
            "template": {
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

    kutil.patch_dp("mysql-operator", "mysql-operator", patch)

    if store_operator_log:
        store_operator_log()

    # Wait till old operator is gone
    if pods:
        kutil.wait_pod_gone("mysql-operator", pods[0]["NAME"])

    # Wait for the new operator running
    pods = kutil.ls_pod("mysql-operator", "mysql-operator.*")
    kutil.wait_pod("mysql-operator", pods[0]["NAME"])


class OperatorUpgradeTest(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

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
            spec = kutil.get_po(self.ns, "mycluster-0")["spec"]
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

        change_operator_version(g_ts_cfg.operator_old_version_tag, store_operator_log=lambda: self.take_log_operator_snapshot())

        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        #
        # This will schedule backups for Jan 1st for a year where this is a Monday
        # it wil fail to create a backup at that time, which is okay, as we don't
        # want it to run anyways and jsut want to inspect the CronJob
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
      instances: 0
  secretName: mypwds
  tlsUseSelfSigned: true
  version: "{g_ts_cfg.get_old_version_tag()}"

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

        self.wait_pod("mycluster-0", "Running")
        self.wait_ic("mycluster", "ONLINE", 1)

        old_operator_image = g_ts_cfg.get_operator_image(g_ts_cfg.operator_old_version_tag)
        assert_sidecar_image(old_operator_image)
        time.sleep(10)
        assert_cj_image("mycluster-testschedule-cb", old_operator_image)
        assert_cj_image("mycluster-testscheduleinactive-cb", old_operator_image)

        # 2 - Upgrading Operator doesn't change sidecar
        change_operator_version(store_operator_log=lambda: self.take_log_operator_snapshot())

        assert_sidecar_image(old_operator_image)
        time.sleep(10)
        operator_image = g_ts_cfg.get_operator_image()
        assert_cj_image("mycluster-testschedule-cb", operator_image)
        assert_cj_image("mycluster-testscheduleinactive-cb", operator_image)

        # 3 - Upgrading the InnoDB Cluster updates sidecar
        kutil.patch_ic(self.ns, "mycluster", {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        def check_done(pod):
            po = kutil.get_po(self.ns, pod)
            # self.logger.debug(json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")))
            return json.loads(po["metadata"].get("annotations", {}).get("mysql.oracle.com/membership-info", "{}")).get("version", "")

        self.wait(check_done, args=("mycluster-0", ),
                  check=lambda s: s.startswith(g_ts_cfg.version_tag), timeout=150, delay=10)

        assert_sidecar_image(g_ts_cfg.get_operator_image())


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
