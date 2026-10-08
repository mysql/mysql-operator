# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import unittest
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
            yaml, r'ValidationError\(InnoDBCluster.spec\): unknown field "bogus" in com.oracle.mysql.v2.InnoDBCluster.spec' if kutil.server_version_less_than("1.25") else
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
        if kutil.server_version_less_than("1.24"):
            too_long_message =  r'metadata.name in body should be at most 40 chars long'
        elif kutil.server_version_less_than("1.31"):
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
            yaml, r'ValidationError\(InnoDBCluster\): missing required field "spec" in com.oracle.mysql.v2.InnoDBCluster' if kutil.server_version_less_than("1.25") else
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
            yaml, r'error validating data: ValidationError\(InnoDBCluster.spec\): missing required field "secretName"' if kutil.server_version_less_than("1.25") else
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
            yaml, r'ValidationError\(InnoDBCluster.spec.instances\): invalid type for com.oracle.mysql.v2.InnoDBCluster.spec.instances: got "string", expected "integer"' if kutil.server_version_less_than("1.25") else
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


class ClusterValidationErrorSurfacing(tutil.OperatorTest):
    """Prove operator validation reaches users and a corrected spec recovers.

    These integration tests exercise the real validator, reconciliation, and
    Kubernetes status/events. Individual version rules and error messages are
    covered by unit tests; this group does not test Server upgrade lineage.
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__ + ":" + cls.__name__)
        super().setUpClass()
        cls.set_ts_var("cluster_size", 1)
        cls.set_ts_var("routers_count", 0)
        g_full_log.watch_mysql_pod(cls.ns, f"{cls.cluster_name}-0")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, f"{cls.cluster_name}-0")
        super().tearDownClass()

    def _create_cluster(self, version):
        kutil.create_user_secrets(self.ns, self.cluster_secret_name,
                                  root_user="root", root_host="%", root_pass="sakila")
        kutil.apply(self.ns, f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: 1
  router:
    instances: 0
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  version: "{version}"
  podSpec:
    terminationGracePeriodSeconds: 5
""")

    def _wait_healthy(self):
        self.wait_pod(f"{self.cluster_name}-0", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=1)

    def _delete_cluster(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)
        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def test_creation_validation_error_reaches_status_and_event_then_recovers(self):
        """Expose creation errors; allow deletion or recovery after correcting the spec."""
        invalid_version = "100.8.8"
        for recover in (False, True):
            with self.subTest(recover=recover):
                since = kutil.get_event_snapshot(self.ns)
                try:
                    self._create_cluster(invalid_version)
                    self.wait_ic(self.cluster_name, "INVALID", num_online=0)
                    self.wait_got_cluster_event(
                        self.cluster_name, since=since, type="Error", reason="InvalidArgument",
                        msg=rf".*version {re.escape(invalid_version)}.*",
                    )
                    self.assertEqual([], kutil.ls_sts(self.ns))
                    self.assertEqual([], kutil.ls_po(self.ns))

                    if recover:
                        kutil.patch_ic(self.ns, self.cluster_name,
                                       {"spec": {"version": g_ts_cfg.version_tag}}, type="merge")
                        self._wait_healthy()
                finally:
                    self._delete_cluster()


    def test_update_validation_error_reaches_event_and_preserves_healthy_deployment(self):
        """Expose a rejected update without replacing the working Server pod."""
        try:
            self._create_cluster(g_ts_cfg.version_tag)
            self._wait_healthy()
            before = kutil.get_po(self.ns, f"{self.cluster_name}-0")
            since = kutil.get_event_snapshot(self.ns)
            invalid_version = "100.8.8"
            kutil.patch_ic(self.ns, self.cluster_name,
                           {"spec": {"version": invalid_version}}, type="merge")
            self.wait_got_cluster_event(
                self.cluster_name, since=since, type="Warning", reason="SpecChanged",
                msg=rf"Permanent error: .*version {re.escape(invalid_version)}.*",
            )
            self._wait_healthy()
            after = kutil.get_po(self.ns, f"{self.cluster_name}-0")
            self.assertEqual(before["metadata"]["uid"], after["metadata"]["uid"])
            for key in ("initContainers", "containers"):
                self.assertEqual(
                    {c["name"]: c["image"] for c in before["spec"][key]},
                    {c["name"]: c["image"] for c in after["spec"][key]},
                )
            kutil.patch_ic(self.ns, self.cluster_name,
                           {"spec": {"version": g_ts_cfg.version_tag}}, type="merge")
            self._wait_healthy()
        finally:
            self._delete_cluster()


class ClusterSpecRuntimeChecksCreation(tutil.OperatorTest):
    """
    Exercise Kubernetes secret-mount and image-pull failures, and cleanup of
    clusters whose pods cannot start. Operator validation surfacing is covered
    separately by ClusterValidationErrorSurfacing.
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
                event_snapshot = kutil.get_event_snapshot(self.ns)

                kutil.apply(self.ns, yaml)

                self.wait_ic(self.cluster_name, "PENDING")

                for instance in range(0, self.cluster_size):
                    self.wait_pod(f"{self.cluster_name}-{instance}", ["Pending"])

                for instance in range(0, self.cluster_size):
                  self.wait_got_pod_event(
                      f"{self.cluster_name}-{instance}", since=event_snapshot, type="Warning",
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
