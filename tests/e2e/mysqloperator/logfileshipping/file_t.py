# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
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


def assert_mysql_log_file_mode(testcase, stat_line, log_path):
    # Log files may gain group write when kubelet remounts the PVC with fsGroup.
    testcase.assertIn(stat_line, (
        f"{log_path} mysql 640",
        f"{log_path} mysql 660",
    ))


class LFSSlowLogEnableDisableEnableBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_query_log_file_name = "slow_query.log"
    general_log_file_name = "general_query.log"
    _cluster_size = 1
    _routers_count = 1

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

    #@classmethod
    def cluster_definition(self) -> str:
        return f"""
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
  logs:
    slowQuery:
      enabled: true
      longQueryTime: 22.8
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def _02_check_slow_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(22.98)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(22.98)") != -1)

            # General Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line)

    def _04_disable_slow_log(self):
        patch = {"spec": { "logs" : { "slowQuery" : { "enabled": False }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        """
        patch = [
            {
                "op":"replace",
                "path":"/spec/logs/slowQuery/enabled",tests/e2e/mysqloperator/keyring/oci_vault_t.py
                "value": False
            },
        ]
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="json", data_as_type='json')
        """
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", self.cluster_size)
        print("[04_disable_slow_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _06_check_slow_log_doesnt_exist(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(23.39)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            assert_mysql_log_file_mode(self, line, f"/var/lib/mysql/{self.slow_query_log_file_name}")
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(23.39)"), -1)

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
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", self.cluster_size)
        print("[08_reenable_slow_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _10_check_slow_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(23.49)").fetch_all()
            sleep(15)
            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(22.89)"), -1)
            self.assertEqual(slow_log_contents.find("SELECT SLEEP(23.39)"), -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.49)") > 0)

            # General Log should NOT exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            self.assertTrue(f"stat: cannot statx '/var/lib/mysql/{self.general_log_file_name}': No such file or directory" in line)

    def _12_enable_general_log(self):
        la_index = 8
        self.label_name = f"server-label{la_index}"
        self.label_value = f"myc-server-label{la_index}-value"
        self.annotation_name = f"server.myc.example.com/ann{la_index}"
        self.annotation_value = f"server-ann{la_index}-value"
        patch = {
            "spec": {
                "logs": {
                    "general": {
                        "enabled": True
                    }
                },
                "podLabels": {
                    f"server-label{la_index}": f"myc-server-label{la_index}-value"
                },
                "podAnnotations": {
                    f"server.myc.example.com/ann{la_index}": f"server-ann{la_index}-value"
                }
            }
        }

        #patch = {"spec": { "logs" : { "general" : { "enabled": True }}}}
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        print("[12_enable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _14_check_general_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            with self.subTest(pod_name):
                pod_manifest = kutil.get_po(self.ns, pod_name)
                container_names = [container['name'] for container in pod_manifest['spec']['containers']]
                self.assertFalse("logcollector" in container_names)

                self.assertTrue(self.label_name in pod_manifest['metadata']['labels'])
                self.assertEqual(pod_manifest['metadata']['labels'][self.label_name], self.label_value)
                self.assertTrue(self.annotation_name in pod_manifest['metadata']['annotations'])
                self.assertEqual(pod_manifest['metadata']['annotations'][self.annotation_name], self.annotation_value)

                with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                    s.query_sql("SELECT SLEEP(23.19)").fetch_all()
                sleep(15)
                # Slow Log should exist
                out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
                line = out.strip().decode("utf-8")
                assert_mysql_log_file_mode(self, line, f"/var/lib/mysql/{self.slow_query_log_file_name}")
                slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
                print(slow_log_contents)
                # Queries from the removed slow log should not exists any more
                self.assertEqual(slow_log_contents.find("SELECT SLEEP(22.89)"), -1)
                self.assertEqual(slow_log_contents.find("SELECT SLEEP(23.39)"), -1)
                # Queries from the new slow log should be there
                self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.49)") != -1)
                self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.19)") != -1)

                # General Log should exist
                out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
                line = out.strip().decode("utf-8")
                print(line)
                self.assertEqual(f"/var/lib/mysql/{self.general_log_file_name} mysql 640", line)

    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)

        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

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
    _cluster_size = 1

    def testit(self):
        self.runit()

class Cluster3LFSSlowLogEnableDisableEnable(LFSSlowLogEnableDisableEnableBase):
    _cluster_size = 3

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
    _cluster_size = 1
    _routers_count = 1

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

    #@classmethod
    def cluster_definition(self) -> str:
        # big longQueryTime due to the following or "START GROUP_REPLICATION" taking long time
        #  # User@Host: mysql_innodb_cluster_1002[mysql_innodb_cluster_1002] @ {self.cluster_name}-2.{self.cluster_name}-instances.cluster3-lfsslow-and-general-log-enable-and-collect.svc.cluster.local [10.42.3.6]  Id:    66
        #  # Query_time: 3.645823  Lock_time: 0.000000 Rows_sent: 0  Rows_examined: 0
        #  #SET timestamp=1698163172;
        #  # administrator command: Binlog Dump GTID;

        return f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: {self.cluster_name}
spec:
  instances: {self._cluster_size}
  router:
    instances: {self.routers_count}
  secretName: {self.cluster_secret_name}
  tlsUseSelfSigned: true
  podLabels:
    server-label1: "myc-server-label1-value"
  podAnnotations:
    server.myc.example.com/ann1: "ann1-value"
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
      longQueryTime: 23.0 #Test fails with k3d on slow systems when the long query time is high one digit seconds
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      containerName: "{self.collector_container_name}"
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
          tag: {self.slow_log_tag}
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.myc.example.com/ann1
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
              path {self.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {self.collector_container_fluentd_path}/buffer
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
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def _02_check_slow_log(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(23.2)").fetch_all()
                s.query_sql("SELECT SLEEP(23.5)").fetch_all()
            sleep(15)

            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.2)") != -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.5)") != -1)

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
                  "query_time":"23.200602",
                  "lock_time":"0.000000",
                  "rows_sent":"1",
                  "rows_examined":"1",
                  "schema":"mysql",
                  "timestamp":"1684958481",
                  "query":"SELECT SLEEP(23.2);",
                  "log_type":1,
                  "pod_name": "{self.cluster_name}-0",
                  "ann1":"ann1-value",
                  "static_field_1":"static_field_1_value",
                  "pod_ip":"10.42.2.6",
                  "host_ip":"172.24.0.2",
                  "mysql_requests_memory":"0",
                  "slowLogField":"XYZT2"
                }"""
                if line_no == 0:
                    self.assertTrue(slow_log_contents["query_time"] >= 23.2)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(23.2);")
                elif line_no == 1:
                    self.assertTrue(slow_log_contents["query_time"] >= 23.5)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(23.5);")
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
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def runit(self):
        self._00_create()
        self._02_check_slow_log()
        self._99_destroy()


class Cluster1LFSSlowLogEnableAndCollect(LFSSlowLogEnableAndCollectBase):
    _cluster_size = 1
    def testit(self):
        self.runit()

class Cluster3LFSSlowLogEnableAndCollect(LFSSlowLogEnableAndCollectBase):
    _cluster_size = 3
    def testit(self):
        self.runit()


class LFSGeneralLogEnableDisableEnableBase(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS
    root_user = "root"
    root_host = "%"
    root_pass = "sakila"
    slow_query_log_file_name = "slow_query.log"
    general_log_file_name = "general_query.log"
    _cluster_size = 1
    _routers_count = 1

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

    #@classmethod
    def cluster_definition(self) -> str:
        return f"""
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
  podLabels:
    server-label1: "myc-server-label1-value"
  podAnnotations:
    server.myc.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    general:
      enabled: true
    slowQuery:
      enabled: false
      longQueryTime: 22.7
"""

    def _00_create(self):
        """
        Create cluster, check posted events.
        """
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def _02_check_general_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            print(pod_name)
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(22.9)").fetch_all()
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
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        print("[04_disable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _06_delete_general_log_after_restart(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            print(pod_name)
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(22.89)").fetch_all()
            sleep(15)

            # General Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.general_log_file_name}"])
            line = out.strip().decode("utf-8")
            print(line)
            assert_mysql_log_file_mode(self, line, f"/var/lib/mysql/{self.general_log_file_name}")

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
                    "server-label" : "myc-server-label1-value",
                }
            }
        ]
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="json", data_as_type='json')
        # We have set the terminationGracePeriodSeconds to 5s, so the pod should die quickly and be
        # scheduled a new also quickly
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        print("[08_restart_sts] Cluster ONLINE after %2.f seconds " % (time() - start_time))

    def _10_check_general_log_doesnt_exist(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            print(pod_name)
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertFalse("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                # should be less than the longquerytime
                s.query_sql("SELECT SLEEP(2)").fetch_all()
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
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        print("[12_reenable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _14_recheck_general_log(self):
        self._02_check_general_log_exists()

    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def runit(self):
        self._00_create()
        self._02_check_general_log_exists()
        self._04_disable_general_log()
        self._06_delete_general_log_after_restart()
        self._08_restart_sts()
        self._10_check_general_log_doesnt_exist()
        self._12_reenable_general_log()
        self._14_recheck_general_log()
        self._99_destroy()

class Cluster1LFSGeneralLogEnableDisableEnable(LFSGeneralLogEnableDisableEnableBase):
    _cluster_size = 1

    def testit(self):
        self.runit()

class Cluster3LFSGeneralLogEnableDisableEnable(LFSGeneralLogEnableDisableEnableBase):
    _cluster_size = 3

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
    _cluster_size = 1
    _routers_count = 1

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

    #@classmethod
    def cluster_definition(self) -> str:
        return f"""
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
  podLabels:
    server-label1: "myc-server-label1-value"
  podAnnotations:
    server.myc.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    general:
      enabled: true
      collect: true
    slowQuery:
      enabled: false
      longQueryTime: 22.5
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      env:
      - name: FLUENTD_OPT
        value: -c /tmp/fluent.conf
      fluentd:
        generalLog:
          tag: {self.general_log_tag}
          options:
            GLoption1: GLoption1Value
            GLoption2: GLoption2Value
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.myc.example.com/ann1
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
          <filter {self.general_log_tag}>
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
              path {self.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {self.collector_container_fluentd_path}/buffer
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
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def _02_check_general_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(23.05)").fetch_all()
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
                  "pod_name":"{self.cluster_name}-0",
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
                self.assertEqual(log_line["server-label1"], "myc-server-label1-value")
                self.assertEqual(log_line["ann1"], "ann1-value")
                self.assertEqual(log_line["static_field_1"], "static_field_1_value")
                self.assertTrue("pod_ip" in log_line)
                self.assertTrue("host_ip" in log_line)
                self.assertTrue("mysql_requests_memory" in log_line)
                self.assertEqual(log_line["generalLogField"], "XYZT2")

    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def runit(self):
        self._00_create()
        self._02_check_general_log_exists()
        self._99_destroy()

class Cluster1LFSGeneralLogEnableAndCollect(LFSGeneralLogEnableAndCollectBase):
    _cluster_size = 1

    def testit(self):
        self.runit()

class Cluster3LFSGeneralLogEnableAndCollect(LFSGeneralLogEnableAndCollectBase):
    _cluster_size = 3

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
    _cluster_size = 1
    _routers_count = 1

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

    #@classmethod
    def cluster_definition(self) -> str:
        return f"""
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
  podLabels:
    server-label1: "myc-server-label1-value"
  podAnnotations:
    server.myc.example.com/ann1: "ann1-value"
  podSpec:
    terminationGracePeriodSeconds: 5
  logs:
    error:
      collect: true
    slowQuery:
      enabled: false
      longQueryTime: 22.5
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      containerName: {self.collector_container_name}
      env:
      - name: FLUENTD_OPT
        value: -c /tmp/fluent.conf
      fluentd:
        errorLog:
          tag: {self.error_log_tag}
          options:
            ELoption1: ELoption1Value
            ELoption2: ELoption2Value
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.myc.example.com/ann1
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
          <filter {self.error_log_tag}>
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
              path {self.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {self.collector_container_fluentd_path}/buffer
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
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running") # timeout??

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def _02_check_error_log_exists(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            # Check that the default name is not there
            self.assertFalse("logcollector" in container_names)
            # Check that the setting logs.collector.containerName is used
            self.assertTrue(self.collector_container_name in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(22.92)").fetch_all()
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
                  "pod_name":"{self.cluster_name}-0",
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
                self.assertEqual(log_line["server-label1"], "myc-server-label1-value")
                self.assertEqual(log_line["ann1"], "ann1-value")
                self.assertEqual(log_line["static_field_1"], "static_field_1_value")
                self.assertTrue("pod_ip" in log_line)
                self.assertTrue("host_ip" in log_line)
                self.assertTrue("mysql_requests_memory" in log_line)
                self.assertEqual(log_line["errorLogField"], "XYZT2")

    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

    def runit(self):
        self._00_create()
        self._02_check_error_log_exists()
        self._99_destroy()

class Cluster1LFSErrorLogCollect(LFSErrorLogCollectBase):
    _cluster_size = 1

    def testit(self):
        self.runit()

class Cluster3LFSErrorLogCollect(LFSErrorLogCollectBase):
    _cluster_size = 3

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
    _cluster_size = 1
    _routers_count = 1

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

    #@classmethod
    def cluster_definition(self) -> str:
        return f"""
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
  podLabels:
    server-label1: "myc-server-label1-value"
  podAnnotations:
    server.myc.example.com/ann1: "ann1-value"
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
      longQueryTime: 22.9 #Test fails with k3d on slow systems when the long query time is high one digit seconds
    collector:
      image: {g_ts_cfg.get_image(Config.Image.FLUENTD)}
      containerName: "{self.collector_container_name}"
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
          tag: {self.slow_log_tag}
        recordAugmentation:
          enabled: true
          annotations:
          - fieldName: ann1
            annotationName: server.myc.example.com/ann1
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
              path {self.collector_container_fluentd_path}/${{tag}}/${{tag}}
              <buffer tag,time>
                @type file
                path {self.collector_container_fluentd_path}/buffer
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
        kutil.create_user_secrets(self.ns, self.cluster_secret_name, root_user=self.root_user, root_host=self.root_host, root_pass=self.root_pass)

        apply_time = isotime()
        kutil.apply(self.ns, self.cluster_definition())

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", self.cluster_size)
        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", num_online=self.routers_count, timeout=self.cluster_size*120)

        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason="ResourcesCreated",
            msg="Dependency resources created, switching status to PENDING")
        self.assertGotClusterEvent(
            self.cluster_name, after=apply_time, type="Normal",
            reason=r"StatusChange", msg=r"Cluster status changed to ONLINE. \d member\(s\) ONLINE")

    def _02_check_slow_log(self):
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            container_names = [container['name'] for container in kutil.get_po(self.ns, pod_name)['spec']['containers']]
            self.assertTrue("logcollector" in container_names)

            with mutil.MySQLPodSession(self.ns, pod_name, self.root_user, self.root_pass) as s:
                s.query_sql("SELECT SLEEP(23.2)").fetch_all()
                s.query_sql("SELECT SLEEP(23.5)").fetch_all()
            sleep(15)

            # Slow Log should exist
            out = kutil.execp(self.ns, [pod_name, "mysql"], ["stat", "-c%n %U %a", f"/var/lib/mysql/{self.slow_query_log_file_name}"])
            line = out.strip().decode("utf-8")
            self.assertEqual(f"/var/lib/mysql/{self.slow_query_log_file_name} mysql 640", line)
            slow_log_contents = kutil.cat(self.ns, [pod_name, "mysql"], f"/var/lib/mysql/{self.slow_query_log_file_name}").decode().strip()
            print(slow_log_contents)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.2)") != -1)
            self.assertTrue(slow_log_contents.find("SELECT SLEEP(23.5)") != -1)

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
                  "query_time":"23.200602",
                  "lock_time":"0.000000",
                  "rows_sent":"1",
                  "rows_examined":"1",
                  "schema":"mysql",
                  "timestamp":"1684958481",
                  "query":"SELECT SLEEP(23.2);",
                  "log_type":1,
                  "pod_name": "{self.cluster_name}-0",
                  "ann1":"ann1-value",
                  "static_field_1":"static_field_1_value",
                  "pod_ip":"10.42.2.6",
                  "host_ip":"172.24.0.2",
                  "mysql_requests_memory":"0",
                  "slowLogField":"XYZT2"
                }"""
                if line_no == 0:
                    self.assertTrue(slow_log_contents["query_time"] >= 23.2)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(23.2);")
                elif line_no == 1:
                    self.assertTrue(slow_log_contents["query_time"] >= 23.5)
                    self.assertEqual(slow_log_contents["query"], "SELECT SLEEP(23.5);")
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
        server_pods = kutil.ls_po(self.ns, pattern=f"{self.cluster_name}-\d")
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
        waiter = tutil.get_sts_rollover_update_waiter(self, self.cluster_name, timeout=900, delay=50)
        start_time = time()
        kutil.patch_ic(self.ns, self.cluster_name, patch, type="merge")
        waiter()
        for instance in reversed(range(0, self.cluster_size)):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")
        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)
        print("[06_disable_general_log] Cluster ONLINE after %.2f seconds " % (time() - start_time))

    def _99_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)

        kutil.delete_default_secret(self.ns, self.cluster_secret_name)

    def runit(self):
        self._00_create()
        self._02_check_slow_log()
        self._04_check_general_log_exists()
        self._06_disable_general_log()
        self._99_destroy()


class Cluster1LFSSlowAndGeneralLogEnableAndCollect(LFSSlowAndGeneralLogEnableAndCollectBase):
    _cluster_size = 1
    def testit(self):
        self.runit()

class Cluster3LFSSlowAndGeneralLogEnableAndCollect(LFSSlowLogEnableAndCollectBase):
    _cluster_size = 3
    def testit(self):
        self.runit()
