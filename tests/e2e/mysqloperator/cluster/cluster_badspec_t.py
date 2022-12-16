# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
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

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

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
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
  edition: community
  bogus: 1234
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, r'ValidationError\(InnoDBCluster.spec\): unknown field "bogus" in com.oracle.mysql.v2.InnoDBCluster.spec' if kutil.server_version() < '1.25' else
                  r'InnoDBCluster in version "v2" cannot be handled as a InnoDBCluster: strict decoding error: unknown field "spec.bogus"')

    def test_1_name_too_long(self):
        """
        Checks:
        - cluster name can't be longer than allowed in innodb cluster (40 chars)
        """
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: veryveryveryveryveryveryveryverylongnamex
spec:
  secretName: mypwds
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, r'metadata.name in body should be at most 40 chars long' if kutil.server_version() < '1.24' else
                 'The InnoDBCluster "veryveryveryveryveryveryveryverylongnamex" is invalid: metadata.name: Too long: may not be longer than 40')

    def test_1_no_name(self):
        """
        Checks:
        - metadata.name is mandatory
        (blocked even before the schema validation)
        """
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
spec:
  secretName: mypwds
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(yaml, r'resource name may not be empty')

    def test_1_no_secret(self):
        """
        Checks:
        - spec.secretName is mandatory
        """
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
"""
        self.assertApplyFails(
            yaml, r'ValidationError\(InnoDBCluster\): missing required field "spec" in com.oracle.mysql.v2.InnoDBCluster' if kutil.server_version() < '1.25' else
                  r'The InnoDBCluster "mycluster" is invalid: spec: Required value')

        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, r'error validating data: ValidationError\(InnoDBCluster.spec\): missing required field "secretName"' if kutil.server_version() < '1.25' else
                  r'The InnoDBCluster "mycluster" is invalid: spec.secretName: Required value')

    def test_1_instances(self):
        """
        Checks:
        - Invalid values for spec.instances (too small, too big, not number)
        """
        # This will fail on 1.18 and 1.19 (and previous due to https://github.com/kubernetes/kubernetes/issues/90128)
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
  instances: 0
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, 'spec.instances: Invalid value: 0: spec.instances in body should be greater than or equal to 1')

        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
  instances: 14
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, 'spec.instances: Invalid value: 14: spec.instances in body should be less than or equal to 9')

        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
  instances: "bla"
  tlsUseSelfSigned: true
"""
        self.assertApplyFails(
            yaml, r'ValidationError\(InnoDBCluster.spec.instances\): invalid type for com.oracle.mysql.v2.InnoDBCluster.spec.instances: got "string", expected "integer"' if kutil.server_version() < '1.25' else
                  r'The InnoDBCluster "mycluster" is invalid: spec.instances: Invalid value: "string": spec.instances in body must be of type integer: "string"')

        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
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

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def test_0_prepare(self):
        # this also checks that the root user can be completely customized
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="admin", root_host="%", root_pass="secret")

    def test_1_bad_secret_delete(self):
        """
        Checks:
        - secret that doesn't exist
        - cluster can be deleted after the failure
        """
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  secretName: badsecret
  tlsUseSelfSigned: true
"""
        start_time = isotime()

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Pending")

        # the initmysql container will fail during creation with
        # CreateContainerConfigError because the container is setup to read from
        # it to set MYSQL_ROOT_PASSWORD, so the operator or sidecars will never
        # run
        self.wait(kutil.ls_po, (self.ns,),
                  lambda pods: pods[0]["STATUS"] == "Init:CreateContainerConfigError",
                  timeout=90, delay=5)

        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")
        kutil.delete_pvc(self.ns, None)

    def test_1_bad_secret_recover(self):
        pass

    def test_1_unsupported_version_delete(self):
        """
        Checks that setting an unsupported version is detected before any pods
        are created and that the cluster can be deleted in that state.
        """

        # create cluster with mostly default configs, but a specific server version
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  version: "5.7.30"
"""
        kutil.apply(self.ns, yaml)

        self.wait(kutil.get_ic_ev, (self.ns, "mycluster"),
                  lambda evs: len(evs) > 0)

        # version is invalid/not supported, runtime check should prevent the
        # sts from being created
        self.assertFalse(kutil.ls_po(self.ns))
        self.assertFalse(kutil.ls_sts(self.ns))

        # there should be an event for the cluster resource indicating the
        # problem
        self.assertGotClusterEvent(
            "mycluster", type="Error", reason="InvalidArgument", msg="version 5.7.30 must be between .*")

        # deleting the ic should work despite the error
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")
        kutil.delete_pvc(self.ns, None)

    def test_1_unsupported_version_recover(self):
        """
        Checks that setting an unsupported version is detected before any pods
        are created and that the cluster can be recovered by fixing the version.
        """

        # create cluster with mostly default configs, but a specific server version
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
  version: "5.7.30"
"""
        kutil.apply(self.ns, yaml)

        # the ic object will error out before sts is created
        self.wait(kutil.get_ic_ev, (self.ns, "mycluster"),
                  lambda evs: len(evs) > 0)

        # fixing the version should let the cluster resume creation
        kutil.patch_ic(self.ns, "mycluster", {"spec": {
            "version": g_ts_cfg.version_tag
        }}, type="merge")

        # check cluster ok now
        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE")

        # cleanup
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")
        kutil.delete_pvc(self.ns, None)

    def test_2_bad_pod_delete(self):
        """
        Checks that using a bad spec that fails at the pod can be deleted.
        """
        # create cluster with mostly default configs, but a specific option
        # that will be accepted by the runtime checks but will fail at pod
        # creation
        yaml = """
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
  imageRepository: invalid
"""
        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "PENDING")
        self.wait_pod("mycluster-0", ["Pending"])

        self.assertEqual(len(kutil.ls_po(self.ns)), 1)
        self.assertEqual(len(kutil.ls_sts(self.ns)), 1)

        def pod_error():
            clusterStatus = kutil.ls_pod(self.ns, "mycluster-0")[0]["STATUS"]
            return clusterStatus in ("Init:ErrImageNeverPull", "Init:ErrImagePull", "Init:ImagePullBackOff")

        self.wait(pod_error)

        kutil.delete_ic(self.ns, "mycluster")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")
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
        yaml = """
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
  imageRepository: invalid
"""
        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "PENDING")
        self.wait_pod("mycluster-0", ["Pending"])

        self.assertEqual(len(kutil.ls_po(self.ns)), 1)
        self.assertEqual(len(kutil.ls_sts(self.ns)), 1)

        def pod_error():
            clusterStatus = kutil.ls_pod(self.ns, "mycluster-0")[0]["STATUS"]
            return clusterStatus in ("Init:ErrImageNeverPull", "Init:ErrImagePull", "Init:ImagePullBackOff")

        self.wait(pod_error)

        # the only way out when ic fails during creation is deleting and retrying
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")
        kutil.delete_pvc(self.ns, None)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


class ClusterSpecRuntimeChecksModification(tutil.OperatorTest):
    """
    Same as ClusterSpecRuntimeChecksCreation, but for clusters that already
    exist and have invalid spec changes made.
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

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

    def test_0_prepare(self):
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 3
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-2", "Running")
        self.wait_ic("mycluster", "ONLINE", 3)

    def test_1_bad_upgrade(self):
        """
        Change spec with invalid version, it should be ignored but notified in events.
        """
        ic_ev_num = len(kutil.get_ic_ev(self.ns, "mycluster"))

        kutil.patch_ic(self.ns, "mycluster", {"spec": {
            "version": "8.8.8"
        }}, type="merge")

        # ensure cluster is still healthy
        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")

        self.wait_ic("mycluster", "ONLINE", 3)

        # ensure new events arrived
        self.wait(kutil.get_ic_ev, (self.ns, "mycluster"),
                  lambda evs: len(evs) > ic_ev_num)

        # there should be events for the cluster resource indicating the update problem
        self.assertGotClusterEvent(
            "mycluster", type="Normal", reason="Logging", msg=rf"Propagating spec.version=8.8.8 for {self.ns}/mycluster \(was None\)")
        self.assertGotClusterEvent(
            "mycluster", type="Error", reason="Logging", msg="Handler 'on_innodbcluster_field_version/spec.version' failed permanently: version 8.8.8 must be between .*")
        self.assertGotClusterEvent(
            "mycluster", type="Normal", reason="Logging", msg="Updating is processed: 0 succeeded; 1 failed.")

        # version is invalid/not supported, runtime check should prevent the
        # sts from being created
        self.assertTrue(kutil.ls_po(self.ns))
        self.assertTrue(kutil.ls_sts(self.ns))

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster", 180)

        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


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
