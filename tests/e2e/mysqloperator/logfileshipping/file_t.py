# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from time import sleep, time
from asyncio import subprocess
from utils.auxutil import isotime
from utils import tutil
from utils import kutil
from utils import mutil
from setup import defaults
import logging
import json
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS
from e2e.mysqloperator.cluster.cluster_t import check_all
from setup.config import g_ts_cfg, Config


class LFSBadSpec(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_log_tag = "slowLogTag"
    collector_container_fluentd_path = "/tmp/fluent"
    instances = 1
    common_cr_manifest = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
"""

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def assertApplyFails(self, yaml, pattern):
        r = kutil.apply(self.ns, yaml, check=False)
        self.assertEqual(1, r.returncode)
        self.assertRegex(r.stdout.decode("utf8"), pattern)

    def test_00_no_default_collector_image(self):
        yaml = f"""{self.common_cr_manifest}
  logs:
    slowQuery:
      enabled: true
      longQueryTime: 1.8
      collect: true
    collector:
      env:
      - name: SOME_OPT
        value: "some_opt_value"
      fluentd:
        slowQueryLog:
          tag: {self.slow_log_tag}
"""
        self.assertApplyFails(yaml, r'spec.logs.collector.image: Required value')

    def test_02_no_fluentd_section(self):
        yaml = f"""{self.common_cr_manifest}
  logs:
    slowQuery:
      enabled: true
      longQueryTime: 1.8
      collect: true
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      env:
      - name: SOME_OPT
        value: "some_opt_value"
"""
        self.assertApplyFails(yaml, r'spec.logs.collector.fluentd: Required value')


    def test_04_wrong_verbosity(self):
        yaml = f"""{self.common_cr_manifest}
  logs:
    error:
      verbosity: aaa
"""
        self.assertApplyFails(yaml, r'spec.logs.error.verbosity: got "string", expected "integer"' if kutil.server_version() < '1.25'
                                    else r'spec.logs.error.verbosity in body must be of type integer: "string"')

        yaml = f"""{self.common_cr_manifest}
  logs:
    error:
      verbosity: 4
"""
        self.assertApplyFails(yaml, r'spec.logs.error.verbosity in body should be less than or equal to 3')

        yaml = f"""{self.common_cr_manifest}
  logs:
    error:
      verbosity: 0
"""
        self.assertApplyFails(yaml, r'spec.logs.error.verbosity: Invalid value: 0: spec.logs.error.verbosity in body should be greater than or equal to 1')

    def test_06_wrong_long_query_time(self):
        yaml = f"""{self.common_cr_manifest}
  logs:
    slowQuery:
      enabled: true
      longQueryTime: bbb
"""
        self.assertApplyFails(yaml, r'spec.logs.slowQuery.longQueryTime: got "string", expected "number"' if kutil.server_version() < '1.25' else
                                    r'spec.logs.slowQuery.longQueryTime in body must be of type number: "string"')

        yaml = f"""
{self.common_cr_manifest}
  logs:
    slowQuery:
      enabled: true
      longQueryTime: -10
"""
        self.assertApplyFails(yaml, r'spec.logs.slowQuery.longQueryTime in body should be greater than or equal to 0')

    def test_08_no_sinks(self):
        yaml = f"""
{self.common_cr_manifest}
  logs:
    slowQuery:
      enabled: true
      longQueryTime: 2
      collect: true
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      fluentd:
        slowQueryLog:
          tag: slowLogTag
"""
        self.assertApplyFails(yaml, r'spec.logs.collector.fluentd.sinks: Required value')

    def test_10_wrong_augmentation_fields(self):

        ra_common_manifest = f"""
{self.common_cr_manifest}
  logs:
    slowQuery:
      enabled: true
      longQueryTime: 2
      collect: true
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      fluentd:
        sinks:
        - name: stdout
          rawConfig: |
            <store>
              @type stdout
            </store>
        recordAugmentation:
          enabled: true
"""
        yaml = f"""
{ra_common_manifest}

          annotations:
          - field: ann1
            annotationName: server.mycluster.example.com/ann1
"""
        self.assertApplyFails(yaml, r'unknown field "field" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.annotations' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.annotations\[0\].field')

        yaml = f"""
{ra_common_manifest}
          labels:
          - field: pod_name
            label: statefulset.kubernetes.io/pod-name
"""
        self.assertApplyFails(yaml,r'unknown field "field" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.labels' if kutil.server_version() < '1.25' else
                                   r'unknown field "spec.logs.collector.fluentd.recordAugmentation.labels\[0\].field')

        self.assertApplyFails(yaml,r'unknown field "label" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.labels' if kutil.server_version() < '1.25' else
                                   r'unknown field "spec.logs.collector.fluentd.recordAugmentation.labels\[0\].label')

        yaml = f"""
{ra_common_manifest}
          podFields:
          - field: pod_ip
            path: status.podIP
"""
        self.assertApplyFails(yaml, r'unknown field "field" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.podFields' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.podFields\[0\].field')

        self.assertApplyFails(yaml, r'unknown field "path" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.podFields' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.podFields\[0\].path')

        yaml = f"""
{ra_common_manifest}
          resourceFields:
          - containerName: mysql
            field: mysql_requests_memory
            resource: requests.memory
          - container: mysql
            field: mysql_requests_memory
            resource: requests.memory
"""
        self.assertApplyFails(yaml, r'unknown field "field" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.resourceFields' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.resourceFields\[0\].field')
        self.assertApplyFails(yaml, r'unknown field "container" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.resourceFields' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.resourceFields\[1\].container')

        yaml = f"""
{ra_common_manifest}
          staticFields:
          - field: static_field_1
            value: static_field_1_value
"""
        self.assertApplyFails(yaml, r'unknown field "field" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.staticFields' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.staticFields\[0\].field')
        self.assertApplyFails(yaml, r'unknown field "value" in com.oracle.mysql.v2.InnoDBCluster.spec.logs.collector.fluentd.recordAugmentation.staticFields' if kutil.server_version() < '1.25' else
                                    r'unknown field "spec.logs.collector.fluentd.recordAugmentation.staticFields\[0\].value')


class LFSSlowLogEnableDisableEnableBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_query_log_file_name = "slow_query.log"
    general_log_file_name = "general_query.log"
    instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition(cls) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {cls.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    slowQuery:
      enabled: true
      longQueryTime: 2.8
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, "mypwds", root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        self.wait_pod("mycluster-0", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def _02_check_slow_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(2.98)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(2.98)") != -1)

            # General Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line)

    def _04_disable_slow_log(self):
        patch = {"spec": { "logs" : { "slowQuery" : { "enabled": False }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        """
        patch = [
            {
                "op":"replace",
                "path":"/spec/logs/slowQuery/enabled",
                "value": False
            },
        ]
        kutil.patch_ic(self.ns, "mycluster", patch, type="json", data_as_type='json')
        """
        waiter()
        self.wait_ic("mycluster", "ONLINE")
        print("[04_disable_slow_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _06_check_slow_log_doesnt_exist(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(3.39)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(3.39)"), -1)

            out = kutil.execp(self.ns, [pod_name, "mysql"], ["rm", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            print(out.strip().decode("utf-8"))
            # Now slow log should not exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.slow_query_log_file_name}': No such file or directory" in line)

            # General Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line)

    def _08_reenable_slow_log(self):
        patch = {"spec": { "logs" : { "slowQuery" : { "enabled": True }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        waiter()
        self.wait_ic("mycluster", "ONLINE")
        print("[08_reenable_slow_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _10_check_slow_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(3.49)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(2.89)"), -1)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(3.39)"), -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(3.49)") > 0)

            # General Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line)

    def _12_enable_general_log(self):
        patch = {"spec": { "logs" : { "general" : { "enabled": True }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        waiter()
        self.wait_ic("mycluster", "ONLINE")
        print("[12_enable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _14_check_general_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(3.19)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            # Queries from the removed slow log should not exists any more
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(2.89)"), -1)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(3.39)"), -1)
            # Queries from the new slow log should be there
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(3.49)") != -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(3.19)") != -1)

            # General Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertEqual(f"/var/lib/mysql/{self.general_log_file_name} mysql 640", line)

    def _99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    def runit(self):
        self._00_create()
        self._02_check_slow_log_exists()
        self._04_disable_slow_log()
        self._06_check_slow_log_doesnt_exist()
        self._08_reenable_slow_log()
        self._10_check_slow_log_exists()
        self._12_enable_general_log()
        self._14_check_general_exists()
        self._99_destroy()

class Cluster1LFSSlowLogEnableDisableEnable(LFSSlowLogEnableDisableEnableBase):
    instances = 1

    def testit(self):
        self.runit()

class Cluster3LFSSlowLogEnableDisableEnable(LFSSlowLogEnableDisableEnableBase):
    instances = 3

    def testit(self):
        self.runit()


class LFSSlowLogEnableAndCollectBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    collector_container_name = "logcollector"
    slow_query_log_file_name = "slow_query.log"
    collector_container_fluentd_path = "/tmp/fluent"
    slow_log_tag = "slowLogTag"
    instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition(cls) -> str:
        # big longQueryTime due to the following or "START GROUP_REPLICATION" taking long time
        #  # User@Host: mysql_innodb_cluster_1002[mysql_innodb_cluster_1002] @ mycluster-2.mycluster-instances.cluster3-lfsslow-and-general-log-enable-and-collect.svc.cluster.local [10.42.3.6]  Id:    66
        #  # Query_time: 3.645823  Lock_time: 0.000000 Rows_sent: 0  Rows_examined: 0
        #  #SET timestamp=1698163172;
        #  # administrator command: Binlog Dump GTID;

        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {cls.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podLabels:
    server-label1: "mycluster-server-label1-value"
  podAnnotations:
    server.mycluster.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    error:
      collect: false
    general:
      collect: false
      enabled: false
    slowQuery:
      collect: true
      enabled: true
      longQueryTime: 13.0 #Test fails with k3d on slow systems when the long query time is high one digit seconds
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      containerName: "{cls.collector_container_name}"
      env:
      - name: FLUENTD_OPT
        value: -c /tmp/fluent.conf
      fluentd:
        errorLog:
          tag: errLogTag
          options:
            ELoption11: ELoption11Value
            ELoption22: ELoption22Value
        generalLog:
          tag: genLogTag
          options:
            GLoption1: GLoption1Value
            GLoption2: GLoption2Value
        slowQueryLog:
          options:
            SLoption55: SLoption55Value
            SLoption66: SLoption66Value
          tag: {cls.slow_log_tag}
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.mycluster.example.com/ann1
          labels:
          - fieldName: pod_name
            labelName: statefulset.kubernetes.io/pod-name
          - fieldName: server-label1
            labelName: server-label1
          podFields:
          - fieldName: pod_ip
            fieldPath: status.podIP
          - fieldName: host_ip
            fieldPath: status.hostIP
          resourceFields:
          - containerName: mysql
            fieldName: mysql_requests_memory
            resource: requests.memory
          staticFields:
          - fieldName: static_field_1
            fieldValue: static_field_1_value
        additionalFilterConfiguration: |
          <filter slowLogTag>
            @type record_transformer
            <record>
              slowLogField XYZT2
            </record>
          </filter>
        sinks:
        - name: stdout
          rawConfig: |
            <store>
              @type stdout
            </store>
        - name: file
          rawConfig: |
            <store>
              @type file
              append true
              add_path_suffix false
              path {cls.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {cls.collector_container_fluentd_path}/buffer
                timekey 1 # 1s partition
                timekey_wait 1s
                timekey_use_utc true # use utc
                flush_interval 1s
              </buffer>
              <format>
                @type json
              </format>
            </store>
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, "mypwds", root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def _02_check_slow_log(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(13.2)").fetch_all()
                s.query_sql("SELECT SLEEP(13.5)").fetch_all()
            sleep(15)

            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(13.2)") != -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(13.5)") != -1)

            log_file_name = kutil.execp(self.ns, [pod_name, self.collector_container_name], ["bash", "-c", f"ls {self.collector_container_fluentd_path}/{self.slow_log_tag}/"]).decode().strip()
            log_file_name = log_file_name.split("\n", 1)[0]
            slow_log_contents_js = kutil.cat(self.ns, [pod_name, self.collector_container_name], f"{self.collector_container_fluentd_path}/{self.slow_log_tag}/{log_file_name}").decode().strip()
            first_lines_js = slow_log_contents_js.split("\n", 2 + 1)[0:2:1]
            line_no = 0
            for log_line_js in first_lines_js:
                try:
                    slow_log_contents = json.loads(log_line_js)
                except json.JSONDecodeError as exc:
                    print(exc)
                    print(log_line_js)
                    print(container_names)
                    print(kutil.get_po(self.ns, pod_name))
                    for container_name in container_names:
                        print(kutil.logs(self.ns, [pod_name, container_name]))
                    raise
                """ {
                  "user":"root",
                  "current_user":"root",
                  "host":"localhost",
                  "ip":"127.0.0.1",
                  "id":"44",
                  "query_time":"13.200602",
                  "lock_time":"0.000000",
                  "rows_sent":"1",
                  "rows_examined":"1",
                  "schema":"mysql",
                  "timestamp":"1684958481",
                  "query":"SELECT SLEEP(13.2);",
                  "log_type":1,
                  "pod_name": "mycluster-0",
                  "ann1":"ann1-value",
                  "static_field_1":"static_field_1_value",
                  "pod_ip":"10.42.2.6",
                  "host_ip":"172.24.0.2",
                  "mysql_requests_memory":"0",
                  "slowLogField":"XYZT2"
                }"""
                if line_no == 0:
                    self.assertTrue(slow_log_contents["query_time"] >= 13.2)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(13.2);")
                elif line_no == 1:
                    self.assertTrue(slow_log_contents["query_time"] >= 13.5)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(13.5);")
                self.assertEqual(slow_log_contents["user"], "root")
                self.assertEqual(slow_log_contents["current_user"], "root")
                self.assertEqual(slow_log_contents["host"], "localhost")
                self.assertTrue("id" in slow_log_contents)
                self.assertTrue("lock_time" in slow_log_contents)
                self.assertEqual(slow_log_contents["rows_sent"], 1)
                self.assertEqual(slow_log_contents["rows_examined"], 1)
                self.assertEqual(slow_log_contents["schema"], "mysql")
                self.assertTrue("timestamp" in slow_log_contents)
                self.assertEqual(slow_log_contents["log_type"], 1)
                self.assertEqual(slow_log_contents["pod_name"], pod_name)
                self.assertEqual(slow_log_contents["ann1"], "ann1-value")
                self.assertEqual(slow_log_contents["static_field_1"], "static_field_1_value")
                self.assertTrue("pod_ip" in slow_log_contents)
                self.assertTrue("host_ip" in slow_log_contents)
                self.assertTrue("mysql_requests_memory" in slow_log_contents)
                self.assertEqual(slow_log_contents["slowLogField"], "XYZT2")
                line_no = line_no + 1

    def _99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    def runit(self):
        self._00_create()
        self._02_check_slow_log()
        self._99_destroy()


class Cluster1LFSSlowLogEnableAndCollect(LFSSlowLogEnableAndCollectBase):
    instances = 1
    def testit(self):
        self.runit()

class Cluster3LFSSlowLogEnableAndCollect(LFSSlowLogEnableAndCollectBase):
    instances = 3
    def testit(self):
        self.runit()


class LFSGeneralLogEnableDisableEnableBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_query_log_file_name = "slow_query.log"
    general_log_file_name = "general_query.log"
    instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition(cls) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {cls.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podLabels:
    server-label1: "mycluster-server-label1-value"
  podAnnotations:
    server.mycluster.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    general:
      enabled: true
    slowQuery:
      enabled: false
      longQueryTime: 2.7
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, "mypwds", root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def _02_check_general_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            print(pod_name)
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(2.9)").fetch_all()
            sleep(15)
            # General Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.general_log_file_name} mysql 640", line)

            # Slow Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.slow_query_log_file_name}': No such file or directory") != -1)

    def _04_disable_general_log(self):
        patch = {"spec": { "logs" : { "general" : { "enabled": False }}}}
        start_time = time()
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        waiter()
        self.wait_ic("mycluster", "ONLINE")
        print("[04_disable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _06_delete_general_log_after_restart(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            print(pod_name)
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(2.89)").fetch_all()
            sleep(15)

            # General Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertEqual(f"/var/lib/mysql/{self.general_log_file_name} mysql 640", line)

            print(f"Deleting /var/lib/mysql/{self.general_log_file_name} on ({pod_name}::mysql)")
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["rm", f"/var/lib/mysql/{self.general_log_file_name}"])
            print(out.strip().decode("utf-8"))

            # General Log should be gone
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory") != -1)

            # Slow Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.slow_query_log_file_name}': No such file or directory") != -1)

    def _08_restart_sts(self):
        patch = [
            {
                "op":"replace",
                "path":"/spec/podLabels",
                "value": {
                    "server-label" : "mycluster-server-label1-value",
                }
            }
        ]
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, "mycluster", patch, type="json", data_as_type='json')
        # We have set the terminationGracePeriodSeconds to 5s, so the pod should die quickly and be
        # scheduled a new also quickly
        waiter()
        for instance in reversed(range(0, self.instances)):
            self.wait_pod(f"mycluster-{instance}", "Running")
        self.wait_ic("mycluster", "ONLINE")
        print("[08_restart_sts] Cluster ONLINE after %2.f seconds " % (time() - start_time))

    def _10_check_general_log_doesnt_exist(self):
        patch = {"spec": { "logs" : { "general" : { "enabled": False }}}}
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        sleep(5)
        for instance in reversed(range(0, self.instances)):
            self.wait_pod(f"mycluster-{instance}", "Running")
        self.wait_ic("mycluster", "ONLINE")

        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            print(pod_name)
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                # should be less than the longquerytime
                s.query_sql("SELECT SLEEP(1.9)").fetch_all()
            sleep(15)
            # General Log not should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            if not f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line:
                log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.general_log_file_name}").decode().strip()
                print(log_contents[0:400])
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line)

            # Slow Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.slow_query_log_file_name}': No such file or directory") != -1)

    def _12_reenable_general_log(self):
        patch = {"spec": { "logs" : { "general" : { "enabled": True }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        waiter()
        self.wait_ic("mycluster", "ONLINE")
        print("[12_reenable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _14_recheck_general_log(self):
        self._02_check_general_log_exists()

    def _99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    def runit(self):
        self._00_create()
        self._02_check_general_log_exists()
        self._04_disable_general_log()
        self._06_delete_general_log_after_restart()
        self._08_restart_sts()
        self._10_check_general_log_doesnt_exist()
        self._12_reenable_general_log()
        self._14_recheck_general_log
        self._99_destroy()

class Cluster1LFSGeneralLogEnableDisableEnable(LFSGeneralLogEnableDisableEnableBase):
    instances = 1

    def testit(self):
        self.runit()

class Cluster3LFSGeneralLogEnableDisableEnable(LFSGeneralLogEnableDisableEnableBase):
    instances = 3

    def testit(self):
        self.runit()


class LFSGeneralLogEnableAndCollectBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_query_log_file_name = "slow_query.log"
    general_log_file_name = "general_query.log"
    general_log_tag = "genLogTag"
    collector_container_fluentd_path = "/tmp/fluent"
    collector_container_name = "logcollector" #the default name
    max_log_lines_to_be_tested = 10000
    instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition(cls) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {cls.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podLabels:
    server-label1: "mycluster-server-label1-value"
  podAnnotations:
    server.mycluster.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    general:
      enabled: true
      collect: true
    slowQuery:
      enabled: false
      longQueryTime: 2.5
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      env:
      - name: FLUENTD_OPT
        value: -c /tmp/fluent.conf
      fluentd:
        generalLog:
          tag: {cls.general_log_tag}
          options:
            GLoption1: GLoption1Value
            GLoption2: GLoption2Value
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.mycluster.example.com/ann1
          labels:
          - fieldName: pod_name
            labelName: statefulset.kubernetes.io/pod-name
          - fieldName: server-label1
            labelName: server-label1
          podFields:
          - fieldName: pod_ip
            fieldPath: status.podIP
          - fieldName: host_ip
            fieldPath: status.hostIP
          resourceFields:
          - containerName: mysql
            fieldName: mysql_requests_memory
            resource: requests.memory
          staticFields:
          - fieldName: static_field_1
            fieldValue: static_field_1_value
        additionalFilterConfiguration: |
          <filter {cls.general_log_tag}>
            @type record_transformer
            <record>
              generalLogField XYZT2
            </record>
          </filter>
        sinks:
        - name: stdout
          rawConfig: |
            <store>
              @type stdout
            </store>
        - name: file
          rawConfig: |
            <store>
              @type file
              append true
              add_path_suffix false
              path {cls.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {cls.collector_container_fluentd_path}/buffer
                timekey 1 # 1s partition
                timekey_wait 1s
                timekey_use_utc true # use utc
                flush_interval 1s
              </buffer>
              <format>
                @type json
              </format>
            </store>
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, "mypwds", root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def _02_check_general_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(3.05)").fetch_all()
            sleep(15) # let the error log accumulate some entries

            # Slow Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.slow_query_log_file_name}': No such file or directory") != -1)

            # General Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.general_log_file_name} mysql 640", line)

            log_file_name = kutil.execp(self.ns, [pod_name, self.collector_container_name], ["bash", "-c", f"ls {self.collector_container_fluentd_path}/{self.general_log_tag}/"]).decode().strip()
            log_file_name = log_file_name.split("\n", 1)[0]

            general_log_contents_js = kutil.cat(self.ns, [pod_name, self.collector_container_name], f"{self.collector_container_fluentd_path}/{self.general_log_tag}/{log_file_name}").decode().strip()
            first_lines_js = general_log_contents_js.split("\n",self.max_log_lines_to_be_tested+1)[0:self.max_log_lines_to_be_tested:1]
            line_no = 0
            for log_line_js in first_lines_js:
                try:
                    log_line = json.loads(log_line_js)
                except json.JSONDecodeError as exc:
                    print(exc)
                    print(log_line_js)
                    print(container_names)
                    print(kutil.get_po(self.ns, pod_name))
                    for container_name in container_names:
                        print(kutil.logs(self.ns, [pod_name, container_name]))
                    raise
                print(f"{line_no} ", end = " ")
                line_no = line_no + 1
                """
                {
                  "thread":"6",
                  "command_type":"Query",
                  "command":"USE mysql;\n",
                  "log_type":1,
                  "pod_name":"mycluster-0",
                  "server-label1":"",
                  "ann1":"",
                  "static_field_1":"static_field_1_value",
                  "pod_ip":"10.42.2.24",
                  "host_ip":"172.18.0.2",
                  "mysql_requests_memory":"0",
                  "generalLogField": "XYZT2"
                }
                """
                self.assertTrue("thread" in log_line)
                self.assertTrue("command_type" in log_line)
                self.assertTrue("command" in log_line)
                self.assertTrue("log_type" in log_line)
                self.assertEqual(log_line["pod_name"], pod_name)
                self.assertEqual(log_line["server-label1"], "mycluster-server-label1-value")
                self.assertEqual(log_line["ann1"], "ann1-value")
                self.assertEqual(log_line["static_field_1"], "static_field_1_value")
                self.assertTrue("pod_ip" in log_line)
                self.assertTrue("host_ip" in log_line)
                self.assertTrue("mysql_requests_memory" in log_line)
                self.assertEqual(log_line["generalLogField"], "XYZT2")

    def _99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    def runit(self):
        self._00_create()
        self._02_check_general_log_exists()
        self._99_destroy()

class Cluster1LFSGeneralLogEnableAndCollect(LFSGeneralLogEnableAndCollectBase):
    instances = 1

    def testit(self):
        self.runit()

class Cluster3LFSGeneralLogEnableAndCollect(LFSGeneralLogEnableAndCollectBase):
    instances = 3

    def testit(self):
        self.runit()


class LFSErrorLogCollectBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_query_log_file_name = "slow_query.log"
    general_log_file_name = "general_query.log"
    error_log_file_name = "error.log"
    error_log_tag = "errorLogTag"
    collector_container_fluentd_path = "/tmp/fluent"
    collector_container_name = "collector" #the default name
    max_log_lines_to_be_tested = 10000

    instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition(cls) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {cls.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podLabels:
    server-label1: "mycluster-server-label1-value"
  podAnnotations:
    server.mycluster.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    error:
      collect: true
    slowQuery:
      enabled: false
      longQueryTime: 2.5
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      containerName: {cls.collector_container_name}
      env:
      - name: FLUENTD_OPT
        value: -c /tmp/fluent.conf
      fluentd:
        errorLog:
          tag: {cls.error_log_tag}
          options:
            ELoption1: ELoption1Value
            ELoption2: ELoption2Value
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.mycluster.example.com/ann1
          labels:
          - fieldName: pod_name
            labelName: statefulset.kubernetes.io/pod-name
          - fieldName: server-label1
            labelName: server-label1
          podFields:
          - fieldName: pod_ip
            fieldPath: status.podIP
          - fieldName: host_ip
            fieldPath: status.hostIP
          resourceFields:
          - containerName: mysql
            fieldName: mysql_requests_memory
            resource: requests.memory
          staticFields:
          - fieldName: static_field_1
            fieldValue: static_field_1_value
        additionalFilterConfiguration: |
          <filter {cls.error_log_tag}>
            @type record_transformer
            <record>
              errorLogField XYZT2
            </record>
          </filter>
        sinks:
        - name: stdout
          rawConfig: |
            <store>
              @type stdout
            </store>
        - name: file
          rawConfig: |
            <store>
              @type file
              append true
              add_path_suffix false
              path {cls.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {cls.collector_container_fluentd_path}/buffer
                timekey 1 # 1s partition
                timekey_wait 1s
                timekey_use_utc true # use utc
                flush_interval 1s
              </buffer>
              <format>
                @type json
              </format>
            </store>
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, "mypwds", root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def _02_check_error_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            # Check that the default name is not there
            self.assertFalse("logcollector" in container_names)
            # Check that the setting logs.collector.containerName is used
            self.assertTrue(self.collector_container_name in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(2.92)").fetch_all()
            sleep(15) # let the error log accumulate quite some entries

            # General Log should MOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory") != -1)

            # Slow Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(line.find(f"stat: cannot statx '/var/lib/mysql/{self.slow_query_log_file_name}': No such file or directory") != -1)

            # Error Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.error_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.error_log_file_name} mysql 640", line)
            error_log_contents_js = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.error_log_file_name}.00.json").decode().strip()
            try:
                self.assertTrue(len(json.loads(error_log_contents_js.split("\n", 1)[0])) > 0)
            except json.JSONDecodeError as exc:
                print(exc)
                print(error_log_contents_js)
                print(container_names)
                print(kutil.get_po(self.ns, pod_name))
                for container_name in container_names:
                    print(kutil.logs(self.ns, [pod_name, container_name]))
                raise

            log_file_name = kutil.execp(self.ns, [pod_name, self.collector_container_name], ["bash", "-c", f"ls {self.collector_container_fluentd_path}/{self.error_log_tag}/"]).decode().strip()
            log_file_name = log_file_name.split("\n", 1)[0]

            error_log_contents_js = kutil.cat(self.ns, (pod_name, self.collector_container_name), f"{self.collector_container_fluentd_path}/{self.error_log_tag}/{log_file_name}").decode().strip()
            first_lines_js = error_log_contents_js.split("\n",self.max_log_lines_to_be_tested+1)[0:self.max_log_lines_to_be_tested:1]
            line_no = 0
            for log_line_js in first_lines_js:
                try:
                    log_line = json.loads(log_line_js)
                except json.JSONDecodeError as exc:
                    print(exc)
                    print(log_line_js)
                    print(container_names)
                    print(kutil.get_po(self.ns, pod_name))
                    for container_name in container_names:
                        print(kutil.logs(self.ns, [pod_name, container_name]))
                    raise
                print(f"{line_no} ", end = " ")
                line_no = line_no + 1
                """
                {
                  "prio":3,
                  "err_code":10096,
                  "subsystem":"Server",
                  "source_line":11261,
                  "source_file":"mysqld.cc",
                  "function":"check_secure_file_priv_path",
                  "msg":"Ignoring --secure-file-priv value as server is running with --initialize(-insecure).",
                  "ts":1686246288020,
                  "err_symbol":"ER_SEC_FILE_PRIV_IGNORED",
                  "SQL_state":"HY000",
                  "buffered":1686246288020396,
                  "label":"Note",
                  "log_type":1,
                  "pod_name":"mycluster-0",
                  "server-label1":"",
                  "ann1":"",
                  "static_field_1":"static_field_1_value",
                  "pod_ip":"10.42.1.7",
                  "host_ip":"172.19.0.4",
                  "mysql_requests_memory":"0",
                  "errorLogField":"XYZT2"
                }
                """
                self.assertTrue("prio" in log_line)
                self.assertTrue("err_code" in log_line)
                self.assertTrue("err_code" in log_line)
                # source_line may not be there
                # source_file may not be there
                self.assertTrue("subsystem" in log_line)
                self.assertTrue("msg" in log_line)
                self.assertTrue("ts" in log_line)
                self.assertTrue("SQL_state" in log_line)
                self.assertTrue("log_type" in log_line)
                self.assertEqual(log_line["pod_name"], pod_name)
                self.assertEqual(log_line["server-label1"], "mycluster-server-label1-value")
                self.assertEqual(log_line["ann1"], "ann1-value")
                self.assertEqual(log_line["static_field_1"], "static_field_1_value")
                self.assertTrue("pod_ip" in log_line)
                self.assertTrue("host_ip" in log_line)
                self.assertTrue("mysql_requests_memory" in log_line)
                self.assertEqual(log_line["errorLogField"], "XYZT2")

    def _99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    def runit(self):
        self._00_create()
        self._02_check_error_log_exists()
        self._99_destroy()

class Cluster1LFSErrorLogCollect(LFSErrorLogCollectBase):
    instances = 1

    def testit(self):
        self.runit()

class Cluster3LFSErrorLogCollect(LFSErrorLogCollectBase):
    instances = 3

    def testit(self):
        self.runit()


class LFSSlowAndGeneralLogEnableAndCollectBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    collector_container_name = "logcollector"
    general_log_file_name = "general_query.log"
    general_log_tag = "genLogTag"
    slow_query_log_file_name = "slow_query.log"
    collector_container_fluentd_path = "/tmp/fluent"
    slow_log_tag = "slowLogTag"
    instances = 1

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        for instance in range(0, cls.instances):
            g_full_log.watch_mysql_pod(cls.ns, f"mycluster-{instance}")

    @classmethod
    def tearDownClass(cls):
        for instance in reversed(range(0, cls.instances)):
            g_full_log.stop_watch(cls.ns, f"mycluster-{instance}")

        super().tearDownClass()

    @classmethod
    def cluster_definition(cls) -> str:
        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: {cls.instances}
  router:
    instances: 0
  secretName: mypwds
  edition: community
  tlsUseSelfSigned: true
  podLabels:
    server-label1: "mycluster-server-label1-value"
  podAnnotations:
    server.mycluster.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    error:
      collect: false
    general:
      collect: true
      enabled: true
    slowQuery:
      collect: true
      enabled: true
      longQueryTime: 12.9 #Test fails with k3d on slow systems when the long query time is high one digit seconds
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      containerName: "{cls.collector_container_name}"
      env:
      - name: FLUENTD_OPT
        value: -c /tmp/fluent.conf
      fluentd:
        errorLog:
          tag: errLogTag
          options:
            ELoption11: ELoption11Value
            ELoption22: ELoption22Value
        generalLog:
          tag: genLogTag
          options:
            GLoption1: GLoption1Value
            GLoption2: GLoption2Value
        slowQueryLog:
          options:
            SLoption55: SLoption55Value
            SLoption66: SLoption66Value
          tag: {cls.slow_log_tag}
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.mycluster.example.com/ann1
          labels:
          - fieldName: pod_name
            labelName: statefulset.kubernetes.io/pod-name
          - fieldName: server-label1
            labelName: server-label1
          podFields:
          - fieldName: pod_ip
            fieldPath: status.podIP
          - fieldName: host_ip
            fieldPath: status.hostIP
          resourceFields:
          - containerName: mysql
            fieldName: mysql_requests_memory
            resource: requests.memory
          staticFields:
          - fieldName: static_field_1
            fieldValue: static_field_1_value
        additionalFilterConfiguration: |
          <filter slowLogTag>
            @type record_transformer
            <record>
              slowLogField XYZT2
            </record>
          </filter>
        sinks:
        - name: stdout
          rawConfig: |
            <store>
              @type stdout
            </store>
        - name: file
          rawConfig: |
            <store>
              @type file
              append true
              add_path_suffix false
              path {cls.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {cls.collector_container_fluentd_path}/buffer
                timekey 1 # 10s partition
                timekey_wait 1s
                timekey_use_utc true # use utc
                flush_interval 1s
              </buffer>
              <format>
                @type json
              </format>
            </store>
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, "mypwds", root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic("mycluster", ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.instances):
            self.wait_pod(f"mycluster-{instance}", "Running")

        self.wait_ic("mycluster", "ONLINE")

        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            "mycluster", after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. 1 member\(s\) ONLINE")

    def _02_check_slow_log(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(13.2)").fetch_all()
                s.query_sql("SELECT SLEEP(13.5)").fetch_all()
            sleep(15)

            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(13.2)") != -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(13.5)") != -1)

            log_file_name = kutil.execp(self.ns, [pod_name, self.collector_container_name], ["bash", "-c", f"ls {self.collector_container_fluentd_path}/{self.slow_log_tag}/"]).decode().strip()
            log_file_name = log_file_name.split("\n", 1)[0]
            slow_log_contents_js = kutil.cat(self.ns, [pod_name, self.collector_container_name], f"{self.collector_container_fluentd_path}/{self.slow_log_tag}/{log_file_name}").decode().strip()
            first_lines_js = slow_log_contents_js.split("\n", 2 + 1)[0:2:1]
            line_no = 0
            for log_line_js in first_lines_js:
                try:
                    slow_log_contents = json.loads(log_line_js)
                except json.JSONDecodeError as exc:
                    print(exc)
                    print(log_line_js)
                    print(container_names)
                    print(kutil.get_po(self.ns, pod_name))
                    for container_name in container_names:
                        print(kutil.logs(self.ns, [pod_name, container_name]))
                    raise
                """ {
                  "user":"root",
                  "current_user":"root",
                  "host":"localhost",
                  "ip":"127.0.0.1",
                  "id":"44",
                  "query_time":"13.200602",
                  "lock_time":"0.000000",
                  "rows_sent":"1",
                  "rows_examined":"1",
                  "schema":"mysql",
                  "timestamp":"1684958481",
                  "query":"SELECT SLEEP(13.2);",
                  "log_type":1,
                  "pod_name": "mycluster-0",
                  "ann1":"ann1-value",
                  "static_field_1":"static_field_1_value",
                  "pod_ip":"10.42.2.6",
                  "host_ip":"172.24.0.2",
                  "mysql_requests_memory":"0",
                  "slowLogField":"XYZT2"
                }"""
                if line_no == 0:
                    self.assertTrue(slow_log_contents["query_time"] >= 13.2)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(13.2);")
                elif line_no == 1:
                    self.assertTrue(slow_log_contents["query_time"] >= 13.5)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(13.5);")
                self.assertEqual(slow_log_contents["user"], "root")
                self.assertEqual(slow_log_contents["current_user"], "root")
                self.assertEqual(slow_log_contents["host"], "localhost")
                self.assertTrue("id" in slow_log_contents)
                self.assertTrue("lock_time" in slow_log_contents)
                self.assertEqual(slow_log_contents["rows_sent"], 1)
                self.assertEqual(slow_log_contents["rows_examined"], 1)
                self.assertEqual(slow_log_contents["schema"], "mysql")
                self.assertTrue("timestamp" in slow_log_contents)
                self.assertEqual(slow_log_contents["log_type"], 1)
                self.assertEqual(slow_log_contents["pod_name"], pod_name)
                self.assertEqual(slow_log_contents["ann1"], "ann1-value")
                self.assertEqual(slow_log_contents["static_field_1"], "static_field_1_value")
                self.assertTrue("pod_ip" in slow_log_contents)
                self.assertTrue("host_ip" in slow_log_contents)
                self.assertTrue("mysql_requests_memory" in slow_log_contents)
                self.assertEqual(slow_log_contents["slowLogField"], "XYZT2")
                line_no = line_no + 1

    def _04_check_general_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(2.05)").fetch_all()
            sleep(15)

            # General Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.general_log_file_name} mysql 640", line)

            out = kutil.execp(self.ns, [pod_name, "mysql"], ["ls", "-l", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)

            line = kutil.execp(self.ns, [pod_name, self.collector_container_name], ["bash", "-c", f"ls -l {self.collector_container_fluentd_path}/{self.general_log_tag}/"]).decode().strip()
            print(line)

            log_file_name = kutil.execp(self.ns, [pod_name, self.collector_container_name], ["bash", "-c", f"ls {self.collector_container_fluentd_path}/{self.general_log_tag}/"]).decode().strip()
            log_file_name = log_file_name.split("\n")[-1]
            print(f"log_file_name={log_file_name}")

            general_log_contents_js = kutil.cat(self.ns, [pod_name, self.collector_container_name], f"{self.collector_container_fluentd_path}/{self.general_log_tag}/{log_file_name}").decode().strip()

    def _06_disable_general_log(self):
        patch = {"spec": { "logs" : { "general" : { "enabled": False, "collect": False }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, "mycluster", timeout=500, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")

        waiter()
        self.wait_ic("mycluster", "ONLINE")
        print("[06_disable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pods_gone("mycluster-*")
        self.wait_routers_gone("mycluster-router-*")
        self.wait_ic_gone("mycluster")

        kutil.delete_default_secret(self.ns)

    def runit(self):
        self._00_create()
        self._02_check_slow_log()
        self._04_check_general_log_exists()
        self._06_disable_general_log()
        self._99_destroy()


class Cluster1LFSSlowAndGeneralLogEnableAndCollect(LFSSlowAndGeneralLogEnableAndCollectBase):
    instances = 1
    def testit(self):
        self.runit()

class Cluster3LFSSlowAndGeneralLogEnableAndCollect(LFSSlowLogEnableAndCollectBase):
    instances = 3
    def testit(self):
        self.runit()
