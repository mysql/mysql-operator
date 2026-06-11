# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
from utils.auxutil import isotime
from setup import defaults
from utils import tutil
from utils import kutil
import logging
import re
from utils.tutil import g_full_log
from setup.config import g_ts_cfg
from utils.optesting import DEFAULT_MYSQL_ACCOUNTS, COMMON_OPERATOR_ERRORS

# TODO additional checks that could be done via webhooks
#  - version field (should be <= operator version)
#  -


class ClusterSpecAdmissionChecks(tutil.OperatorTest):
    """
    spec errors checked during admission (by CRD schema or webhook)
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    _cluster_size = 1
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def tearDown(self):
        # none of the tests should create anything
        self.assertEqual([], kutil.ls_ic(self.ns))
        self.assertEqual([], kutil.ls_sts(self.ns))
        self.assertEqual([], kutil.ls_po(self.ns))

        return super().tearDown()

    def assertApplyFails(self, yaml, pattern):
        r = kutil.apply(self.ns, yaml, check=False)
        self.assertEqual(1, r.returncode)
        self.assertRegex(r.stdout.decode("utf8"), pattern)

    def test_0_invalid(self):
        """
        Checks:
        - Invalid field in spec
        """
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  secretName: {self.cluster_secret_name}
  instances: {self.cluster_size}
  tlsUseSelfSigned: true
  bogus: 1234
"""
        self.assertApplyFails(
            yaml, r'ValidationError\(InnoDBCluster.spec\): unknown field "bogus" in com.oracle.mysql.v2.InnoDBCluster.spec' if kutil.server_version() < '1.25' else
                  r'InnoDBCluster in version "v2" cannot be handled as a InnoDBCluster: strict decoding error: unknown field "spec.bogus"')

    def test_1_name_too_long(self):
        """
        Checks:
        - cluster name can't be longer than allowed in innodb cluster (40 chars)
        """
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: veryveryveryveryveryveryveryverylongnamex
spec:
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
"""
        if kutil.server_version() < '1.24':
            too_long_message =  r'metadata.name in body should be at most 40 chars long'
        elif kutil.server_version() < '1.31':
            too_long_message = 'The InnoDBCluster "veryveryveryveryveryveryveryverylongnamex" is invalid: metadata.name: Too long: may not be longer than 40'
        else:
            too_long_message = 'The InnoDBCluster "veryveryveryveryveryveryveryverylongnamex" is invalid: metadata.name: Too long: may not be more than 40 bytes'
        self.assertApplyFails(
            yaml, too_long_message)

    def test_1_no_name(self):
        """
        Checks:
        - metadata.name is mandatory
        (blocked even before the schema validation)
        """
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
spec:
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(yaml, r'resource name may not be empty')

    def test_1_no_secret(self):
        """
        Checks:
        - spec.secretName is mandatory
        """
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
"""
        self.assertApplyFails(
            yaml, r'ValidationError\(InnoDBCluster\): missing required field "spec" in com.oracle.mysql.v2.InnoDBCluster' if kutil.server_version() < '1.25' else
                  rf'The InnoDBCluster "{self.cluster_name}" is invalid: spec: Required value')

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  tlsUseSelfSigned: true
  instances: 1
"""
        self.assertApplyFails(
            yaml, r'error validating data: ValidationError\(InnoDBCluster.spec\): missing required field "secretName"' if kutil.server_version() < '1.25' else
                  rf'The InnoDBCluster "{self.cluster_name}" is invalid: spec.secretName: Required value')

    def test_1_instances(self):
        """
        Checks:
        - Invalid values for spec.instances (too small, too big, not number)
        """
        # This will fail on 1.18 and 1.19 (and previous due to https://github.com/kubernetes/kubernetes/issues/90128)
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  instances: 0
"""
        self.assertApplyFails(
            yaml, 'spec.instances: Invalid value: 0: spec.instances in body should be greater than or equal to 1')

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  instances: 14
"""
        self.assertApplyFails(
            yaml, 'spec.instances: Invalid value: 14: spec.instances in body should be less than or equal to 9')

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  instances: "bla"
"""
        self.assertApplyFails(
            yaml, r'ValidationError\(InnoDBCluster.spec.instances\): invalid type for com.oracle.mysql.v2.InnoDBCluster.spec.instances: got "string", expected "integer"' if kutil.server_version() < '1.25' else
                  rf'The InnoDBCluster "{self.cluster_name}" is invalid: spec.instances: Invalid value: "string": spec.instances in body must be of type integer: "string"')

        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  secretName: {self.cluster_secret_name}
  mycnf: 42
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, r'spec.mycnf: Invalid value: "integer": spec.mycnf in body must be of type string: "integer"')

        # TODO bad imagePullPolicy


class ClusterSpecRuntimeChecksCreation(tutil.OperatorTest):
    """
    spec errors checked by the operator, once the ic object was accepted
    by the admission controllers.
    In all cases:
    - the status of the ic should become ERROR
    - an event describing the error should be posted

    Also:
    - fixing the error should recover from error
    - deleting cluster with error should be possible
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 5
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_prepare(self):
        # this also checks that the root user can be completely customized
        kutil.create_user_secrets(self.ns, "mypwds", root_user="admin", root_host="%", root_pass="secret")

    def test_1_bad_secret_delete(self):
        """
        Checks:
        - secret that doesn't exist
        - cluster can be deleted after the failure
        """
        for cluster_size in (1, 2, 3, 5):
            self.cluster_size = cluster_size
            for routers_count in (0, 1, 2, 5):
                self.routers_count = routers_count
                yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  router:
    instances: {self.routers_count}
  secretName: badsecret
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""
                start_time = isotime()

                kutil.apply(self.ns, yaml)

                self.wait_ic(self.cluster_name, "PENDING")

                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{self.cluster_name}-{instance}", ["Pending"])

                for instance in range(0, self.cluster_size):
                  self.wait_got_pod_event(
                      f"{self.cluster_name}-{instance}", after=start_time, type="Warning",
                      reason="FailedMount",
                      msg='MountVolume.SetUp failed for volume "rootcreds" : secret "badsecret" not found')

                kutil.delete_ic(self.ns, self.cluster_name)

                self.wait_pods_gone(f"{self.cluster_name}-\d")
                self.wait_routers_gone(f"{self.cluster_name}-router-*")
                self.wait_ic_gone(self.cluster_name)
                kutil.delete_secret(self.ns, self.cluster_secret_name)
                kutil.delete_pvc(self.ns, None)

    def test_1_bad_secret_recover(self):
        pass

    def test_1_unsupported_version_delete(self):
        """
        Checks that setting an unsupported version is detected before any pods
        are created and that the cluster can be deleted in that state.
        """

        # create cluster with mostly default configs, but a specific server version
        self.cluster_size = 1
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
  version: "5.7.30"
"""
        kutil.apply(self.ns, yaml)

        self.wait(kutil.get_ic_ev, (self.ns, self.cluster_name),
                  lambda evs: len(evs) > 0)

        # version is invalid/not supported, runtime check should prevent the
        # sts from being created
        self.assertFalse(kutil.ls_po(self.ns))
        self.assertFalse(kutil.ls_sts(self.ns))

        # there should be an event for the cluster resource indicating the
        # problem
        self.assertGotClusterEvent(
            self.cluster_name, type="Error", reason="InvalidArgument", msg="version 5.7.30 must be between .*")

        # deleting the ic should work despite the error
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-\d")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_pvc(self.ns, None)

    def test_1_unsupported_version_recover(self):
        """
        Checks that setting an unsupported version is detected before any pods
        are created and that the cluster can be recovered by fixing the version.
        """

        # create cluster with mostly default configs, but a specific server version
        self.cluster_size = 1
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  version: "5.7.30"
  podSpec:
    terminationGracePeriodSeconds: 5
"""
        kutil.apply(self.ns, yaml)

        # the ic object will error out before sts is created
        self.wait(kutil.get_ic_ev, (self.ns, self.cluster_name),
                  lambda evs: len(evs) > 0)

        # fixing the version should let the cluster resume creation
        kutil.patch_ic(self.ns, self.cluster_name, {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        # check cluster ok now
        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE")

        # cleanup
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-\d")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_pvc(self.ns, None)

    def test_2_bad_pod_delete(self):
        """
        Checks that using a bad spec that fails at the pod can be deleted.
        """
        # create cluster with mostly default configs, but a specific option
        # that will be accepted by the runtime checks but will fail at pod
        # creation
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")

        for cluster_size in (1, 2, 3, 5):
            self.cluster_size = cluster_size
            for routers_count in (0, 1, 2, 5):
                self.routers_count = routers_count
                with self.subTest(cluster_size=self.cluster_size, routers_count=self.routers_count):
                    kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")
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
  podSpec:
    terminationGracePeriodSeconds: 5
  imageRepository: invalid
"""
                    kutil.apply(self.ns, yaml)

                    self.wait_ic(self.cluster_name, "PENDING")
                    for instance in range(0, self.cluster_size):
                        self.wait_pod(f"{self.cluster_name}-{instance}", "Pending")

                    self.assertEqual(len(kutil.ls_po(self.ns)), cluster_size) # routers are not up until the cluster is ONLINE
                    print(kutil.ls_sts(self.ns))
                    self.assertEqual(len(kutil.ls_sts(self.ns)), 1)

                    def pod_error(idx):
                        pod = kutil.ls_pod(self.ns, f"{self.cluster_name}-{idx}")
                        pod_status = pod[0]["STATUS"]
                        return pod_status in ("Init:ErrImageNeverPull", "Init:ErrImagePull", "Init:ImagePullBackOff")

                    for instance in range(0, cluster_size):
                        self.wait(pod_error, args=(instance,), timeout=180, delay=10)

                    kutil.delete_ic(self.ns, self.cluster_name)
                    self.wait_pods_gone(f"{self.cluster_name}-\d")
                    self.wait_ic_gone(self.cluster_name)
                    kutil.delete_secret(self.ns, self.cluster_secret_name)
                    kutil.delete_pvc(self.ns, None)

    def test_2_bad_pod_creation(self):
        """
        Checks that using a bad spec that fails at the pod can be recovered (via deletion)
        If the cluster fails at creation, the only recovery alternative is deletion.
        Recovery must work if a working cluster breaks after an update.
        """
        # create cluster with mostly default configs, but a specific option
        # that will be accepted by the runtime checks but will fail at pod
        # creation
        self.assertEqual(len(kutil.ls_po(self.ns)), 0)
        self.assertEqual(len(kutil.ls_sts(self.ns)), 0)
        self.assertEqual(len(kutil.ls_ic(self.ns)), 0)


        for cluster_size in (1, 2, 3, 5):
            self.cluster_size = cluster_size
            for routers_count in (0, 1, 2, 5):
                self.routers_count = routers_count
                with self.subTest(cluster_size=self.cluster_size, routers_count=self.routers_count):
                    kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="admin", root_host="%", root_pass="secret")
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
  podSpec:
    terminationGracePeriodSeconds: 5
  imageRepository: invalid
"""
                    kutil.apply(self.ns, yaml)

                    self.wait_ic(self.cluster_name, "PENDING")
                    for instance in range(0, self.cluster_size):
                        self.wait_pod(f"{self.cluster_name}-{instance}", ["Pending"])

                    self.assertEqual(len(kutil.ls_po(self.ns)), cluster_size) # routers are not up until the cluster is ONLINE
                    self.assertEqual(len(kutil.ls_sts(self.ns)), 1)

                    def pod_error(idx):
                        pod = kutil.ls_pod(self.ns, f"{self.cluster_name}-{idx}")
                        pod_status = pod[0]["STATUS"]
                        return pod_status in ("Init:ErrImageNeverPull", "Init:ErrImagePull", "Init:ImagePullBackOff")

                    for instance in range(0, self.cluster_size):
                        self.wait(pod_error, args=(instance,), timeout=180, delay=10)

                    # the only way out when ic fails during creation is deleting and retrying
                    kutil.delete_ic(self.ns, self.cluster_name)

                    self.wait_pods_gone(f"{self.cluster_name}-\d")
                    self.wait_ic_gone(self.cluster_name)
                    kutil.delete_secret(self.ns, self.cluster_secret_name)
                    kutil.delete_pvc(self.ns, None)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-\d")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_secret(self.ns, self.cluster_secret_name)
        kutil.delete_pvc(self.ns, None)


class ClusterSpecRuntimeChecksModification(tutil.OperatorTest):
    """
    Same as ClusterSpecRuntimeChecksCreation, but for clusters that already
    exist and have invalid spec changes made.
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    _cluster_size = 3
    _routers_count = 0

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()
        cls.set_ts_var("cluster_size", cls._cluster_size)
        cls.set_ts_var("routers_count", cls._routers_count)

        for instance in range(0, cls.get_ts_var("cluster_size")):
            g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.get_ts_var("cluster_size"))):
            g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-{instance}")

        super().tearDownClass()

    def test_0_prepare(self):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self.cluster_size}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

    def test_1_bad_upgrade(self):
        """
        Change spec with invalid version, it should be ignored but notified in events.
        """
        prev_ic_evs = kutil.get_ic_ev(self.ns, self.cluster_name) or []
        prev_ic_ev_names = {ev["metadata"]["name"] for ev in prev_ic_evs}
        invalid_upgrade_version = "100.8.8"
        kutil.patch_ic(self.ns, self.cluster_name, {"spec": {
            "version": invalid_upgrade_version
        }}, type="merge")

        # ensure cluster is still healthy
        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        # there should be events for the cluster resource indicating the update problem
        expected_events = [
            ("Normal", "SpecChanged", re.compile(r"^Field spec.version modified$")),
            ("Normal", "SpecChanged", re.compile(r"^CR changed$")),
            ("Normal", "VersionChangeAttempt", re.compile(
                rf"^Attempting version change from \d+\.\d+\.\d+ to {invalid_upgrade_version}$")),
            ("Warning", "SpecChanged", re.compile(
                rf"^Permanent error: version {invalid_upgrade_version} must be between .*$")),
        ]

        def has_expected_upgrade_events():
            events = kutil.get_ic_ev(self.ns, self.cluster_name) or []
            new_events = [ev for ev in events if ev["metadata"]["name"] not in prev_ic_ev_names]
            return all(any(
                ev["type"] == ev_type and
                ev["reason"] == reason and
                msgpat.match(ev["message"])
                for ev in new_events)
                for ev_type, reason, msgpat in expected_events)

        self.wait(has_expected_upgrade_events, timeout=90, delay=2)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)


# test only 1 or 2 bad syntax spec values and do the rest as unit-tests
# TODO find out what happens if version and image values conflict
# TODO invalid image repo, also auth error for repos
# errors after a cluster already exists should be recoverable
# before creation can be permanent
#   def test_replicas(self):
#   pass
#   def test_routers(self):
#   pass
#   def test_routers(self):
#   pass
