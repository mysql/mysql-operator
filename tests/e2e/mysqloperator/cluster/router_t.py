# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import json
import requests

from time import sleep
from utils import tutil
from utils import kutil
from utils import mutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS


def check_sidecar_health(test, ns, pod):
    logs = kutil.logs(ns, [pod, "sidecar"])
    # check that the sidecar is running and waiting for events
    test.assertIn("Starting Operator request handler...", logs)

def get_routing_options(ns, pod) -> dict:
    result = kutil.execp(ns, [pod, "sidecar"],
                         ["mysqlsh", "root:sakila@localhost", "--js", "-e",
                          "print(dba.getCluster().routerOptions())",
                          "--quiet-start=2"])
    try:
        return json.loads(result)
    except json.decoder.JSONDecodeError:
        print(f"Failed shell output: {result=}")
        raise


# Test 1 member cluster with all default configs
class Router(tutil.OperatorTest):
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
        g_full_log.stop_watch(cls.ns, "mycluster-0")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-2")

        super().tearDownClass()

    def test_0_create(self):
        """
        Create cluster with router
        """
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
  router:
    instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "ONLINE", num_online=3)

        self.wait_routers("mycluster-router-.*", 1)

        # check that router pods didn't restart, which could be a side-effect
        # of router replicaset being created before the cluster is ready
        pods = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        self.assertEqual(1, len(pods))
        self.assertEqual('0', pods[0]["RESTARTS"])


    def test_1_load_distribution(self):
        def check(port, hosts_expected):
            # checks that we connect to both secondaries at least once
            h = tutil.run_from_operator_pod(f"mysql://root:sakila@mycluster.{self.ns}.svc.cluster.local:{port}",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
            self.logger.debug(h)
            hosts_expected.remove(h)
            return len(hosts_expected) == 0

        # ensure connecting to 6446 takes us to the primary
        hosts_expected = set(["mycluster-0"])
        self.wait(check, (6446, hosts_expected))

        # ensure connecting to 6447 takes us to all secondaries
        hosts_expected = set(["mycluster-1", "mycluster-2"])
        self.wait(check, (6447, hosts_expected))


    def test_2_stopped_members(self):
        """
        Ensure that we don't get to a non-ONLINE member when connecting.
        """

        def check(secondaries_seen):
            # checks that we connect to both secondaries at least once
            h = tutil.run_from_operator_pod(f"mysql://root:sakila@mycluster.{self.ns}.svc.cluster.local:6447",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
            secondaries_seen.add(h)
            return len(secondaries_seen) == 2

        # stop GR on one of the members
        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s:
            s.exec_sql("stop group_replication")

        # connect through the router
        h = tutil.run_from_operator_pod(f"root:sakila@mycluster.{self.ns}.svc.cluster.local:6446",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
        self.assertEqual("mycluster-0", h)

        for _ in range(5):
            h = tutil.run_from_operator_pod(f"root:sakila@mycluster.{self.ns}.svc.cluster.local:6447",
                "print(session.run_sql('select @@hostname').fetch_one()[0])")
            self.assertIn(h, ["mycluster-2"])

        # start GR back and ensure it's returned to the pool
        with mutil.MySQLPodSession(self.ns, "mycluster-1", "root", "sakila") as s:
            s.exec_sql("start group_replication")

        secondaries_seen = set()
        self.wait(check, (secondaries_seen,), timeout=300)


    def test_3_rest_api(self):
        with kutil.PortForward(self.ns, "mycluster", "router-rest", target_type="service") as port:
            res = requests.get(f'https://127.0.0.1:{port}/api/20190715/swagger.json',
                               verify=False)
            self.assertEqual(res.status_code, 200)


    def test_4_remove_router_metadata(self):
        def list_routers() -> dict:
            # TODO - with ClusterSet we got to change from cluster to clusterset
            router_list = kutil.execp(self.ns, ("mycluster-0", "sidecar"),
                                      ["mysqlsh", "root:sakila@localhost",
                                       "--quiet-start=2", "--",
                                       "cluster", "list-routers"])
            return json.loads(router_list)["routers"]

        def assert_metadata_matches_running_pods(expected_count):
            routers = sorted(list(list_routers()))

            expected = sorted([pod['NAME']+'::'
                               for pod in
                               kutil.ls_po(self.ns,
                                           pattern="mycluster-router-.*")])

            # testing ListEqual first gives better error if they mismatch
            self.assertListEqual(routers, expected)
            self.assertEqual(len(routers), expected_count)
            self.assertEqual(len(expected), expected_count)

        def scale_routers(new_scale: int):
            patch = {
                "spec": {
                    "router": {
                        "instances": new_scale
                    }
                }
            }
            kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
            self.wait_routers("mycluster-router-.*", new_scale)

        # 1. check initial router registered
        assert_metadata_matches_running_pods(expected_count=1)

        # 2. scale up and see if routers register
        scale_routers(3)
        assert_metadata_matches_running_pods(expected_count=3)

        # 3. scale down and see if they unregister
        scale_routers(1)
        assert_metadata_matches_running_pods(expected_count=1)

        # 4. rotate pod by deleting, should unregister old and register new
        name = kutil.ls_po(self.ns, pattern="mycluster-router-.*")[0]["NAME"]

        kutil.delete_po(self.ns, name)
        self.wait_routers("mycluster-router-.*", 1)

        new_name = kutil.ls_po(self.ns, pattern="mycluster-router-.*")[0]["NAME"]
        self.assertNotEqual(name, new_name)

        assert_metadata_matches_running_pods(expected_count=1)

        # 5. failure while removing doesn't cause an issue

        name = new_name

        # removing meta data will lead to an exception when operator tries
        # which would case the pod to stick around in terminating state if
        # we don't handle that error
        kutil.exec(self.ns, ("mycluster-0", "sidecar"),
                   ["mysqlsh", "root:sakila@localhost",
                    "--quiet-start=2", "--",
                    "cluster", "remove-router-metadata", name+'::'])

        kutil.delete_po(self.ns, name)
        self.wait_routers("mycluster-router-.*", 1)

        new_name = kutil.ls_po(self.ns, pattern="mycluster-router-.*")[0]["NAME"]
        self.assertNotEqual(name, new_name)

        assert_metadata_matches_running_pods(expected_count=1)

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


class RouterLabelsAndAnnotations(tutil.OperatorTest):
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
        g_full_log.stop_watch(cls.ns, "mycluster-0")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-2")

        super().tearDownClass()

    def test_00_create(self):
        """
        Create cluster with router
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
    instances: 1
    podLabels:
      router-label1: "mycluster-router-label1-value"
      router-label2: "mycluster-router-label2-value"
    podAnnotations:
      router.mycluster.example.com/ann1: "rtr-ann1-value"
      router.mycluster.example.com/ann2: "rtr-ann2-value"
      router.mycluster.example.com/ann3: "rtr-ann3-value"
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "ONLINE", num_online=1)

        self.wait_routers("mycluster-router-.*", 1)

        # check that router pods didn't restart, which could be a side-effect
        # of router replicaset being created before the cluster is ready
        pods = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        self.assertEqual(1, len(pods))
        self.assertEqual('0', pods[0]["RESTARTS"])

    def test_01_check_initial_ic(self):
        ### IC
        ic = kutil.get_ic(self.ns, "mycluster")

        labels = ic["spec"]["router"]["podLabels"]
        self.assertTrue('router-label1' in labels)
        self.assertTrue('router-label2' in labels)
        self.assertEqual(labels['router-label1'], 'mycluster-router-label1-value')
        self.assertEqual(labels['router-label2'], 'mycluster-router-label2-value')

        annotations = ic["spec"]["router"]["podAnnotations"]
        self.assertTrue('router.mycluster.example.com/ann1' in annotations)
        self.assertTrue('router.mycluster.example.com/ann2' in annotations)
        self.assertTrue('router.mycluster.example.com/ann3' in annotations)
        self.assertEqual(annotations['router.mycluster.example.com/ann1'], 'rtr-ann1-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann2'], 'rtr-ann2-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann3'], 'rtr-ann3-value')

    def test_03_check_initial_deploy(self):
        ### DEPLOY
        deploy = kutil.get_deploy(self.ns, "mycluster-router")

        labels = deploy["spec"]["template"]["metadata"]["labels"]
        self.assertTrue('router-label1' in labels)
        self.assertTrue('router-label2' in labels)
        self.assertEqual(labels['router-label1'], 'mycluster-router-label1-value')
        self.assertEqual(labels['router-label2'], 'mycluster-router-label2-value')

        annotations = deploy["spec"]["template"]["metadata"]["annotations"]
        self.assertTrue('router.mycluster.example.com/ann1' in annotations)
        self.assertTrue('router.mycluster.example.com/ann2' in annotations)
        self.assertTrue('router.mycluster.example.com/ann3' in annotations)
        self.assertEqual(annotations['router.mycluster.example.com/ann1'], 'rtr-ann1-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann2'], 'rtr-ann2-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann3'], 'rtr-ann3-value')

    def test_05_patch_labels_and_annotations(self):
        patch = [
            {
                "op":"replace",
                "path":"/spec/router/podLabels",
                "value": {
                    "router-label1" : "mycluster-router-label11-value",
                    "router-label222": "mycluster-router-label222-value",
                }
            },
            {
                "op":"replace",
                "path":"/spec/router/podAnnotations",
                "value": {
                    "router.mycluster.example.com/ann1": "rtr-ann111-value",
                    "router.mycluster.example.com/ann2": "rtr-ann222-value",
                    "router.mycluster.example.com/ann333": "rtr-ann333-value",
                }
            },
        ]

        waiter = tutil.get_deploy_rollover_update_waiter(self, "mycluster", timeout=300, delay=5)
        kutil.patch_ic(self.ns, "mycluster", patch, type="json", data_as_type='json')
        waiter()
        server_pods = kutil.ls_po(self.ns, pattern=f"mycluster-\d")
        pod_names = [server["NAME"] for server in server_pods]
        for pod_name in pod_names:
            self.wait_pod(pod_name, "Running")

        self.wait_routers("mycluster-router-*", 1)

    def test_07_check_ic(self):
        labels = kutil.get_ic(self.ns, "mycluster")["spec"]["router"]["podLabels"]

        self.assertTrue('router-label1' in labels)
        self.assertTrue('router-label222' in labels)
        self.assertFalse('router-label2' in labels)
        self.assertEqual(labels['router-label1'], 'mycluster-router-label11-value')
        self.assertEqual(labels['router-label222'], 'mycluster-router-label222-value')

        annotations = kutil.get_ic(self.ns, "mycluster")["spec"]["router"]["podAnnotations"]
        self.assertTrue('router.mycluster.example.com/ann1' in annotations)
        self.assertTrue('router.mycluster.example.com/ann2' in annotations)
        self.assertTrue('router.mycluster.example.com/ann333' in annotations)
        self.assertFalse('router.mycluster.example.com/ann3' in annotations)
        self.assertEqual(annotations['router.mycluster.example.com/ann1'], 'rtr-ann111-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann2'], 'rtr-ann222-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann333'], 'rtr-ann333-value')

    def test_09_check_deployment(self):
        deploy = kutil.get_deploy(self.ns, "mycluster-router")
        labels = deploy["spec"]["template"]["metadata"]["labels"]

        self.assertTrue('router-label1' in labels)
        self.assertTrue('router-label222' in labels)
        self.assertFalse('router-label2' in labels)
        self.assertEqual(labels['router-label1'], 'mycluster-router-label11-value')
        self.assertEqual(labels['router-label222'], 'mycluster-router-label222-value')

        annotations = deploy["spec"]["template"]["metadata"]["annotations"]
        self.assertTrue('router.mycluster.example.com/ann1' in annotations)
        self.assertTrue('router.mycluster.example.com/ann2' in annotations)
        self.assertTrue('router.mycluster.example.com/ann333' in annotations)
        self.assertFalse('router.mycluster.example.com/ann3' in annotations)
        self.assertEqual(annotations['router.mycluster.example.com/ann1'], 'rtr-ann111-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann2'], 'rtr-ann222-value')
        self.assertEqual(annotations['router.mycluster.example.com/ann333'], 'rtr-ann333-value')

    def test_99_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")


class RouterOptions(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()

    def test_0_create(self):
        """
        Create cluster with router and some options
        """
        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with custom config options
        #
        # TODO To check escaping of bootstrap options we need a router image which
        #      handles the escaped values properly
        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
    instances: 1
    bootstrapOptions:
    - --conf-set-option=DEFAULT.name=somename
    options:
    - "--pid-file=/tmp/it's e$caping properly"
    routingOptions:
      read_only_targets: read_replicas
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic("mycluster", "ONLINE", num_online=1)

        self.wait_routers("mycluster-router-.*", 1)

        # check that router pods didn't restart, which could be a side-effect
        # of router replicaset being created before the cluster is ready
        pods = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        self.assertEqual(1, len(pods))
        self.assertEqual('0', pods[0]["RESTARTS"])


    def test_1_verify_provided_options(self):
        [pod] = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        router_config = kutil.cat(self.ns, pod["NAME"], "/tmp/mysqlrouter/mysqlrouter.conf")
        self.assertIn(b"name=somename", router_config, "router config does not contain the name")

        self.assertTrue(kutil.file_exists(self.ns, pod["NAME"], "/tmp/it's e$caping properly"))


    def test_2_update_bootstrap_config(self):
        [old_pod] = kutil.ls_po(self.ns, pattern="mycluster-router-.*")

        patch = {
            "spec": {
                "router": {
                    "bootstrapOptions": [
                        "--conf-set-option=DEFAULT.name=othername"
                    ]
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")

        self.wait_pod_gone(old_pod["NAME"], ns=self.ns)
        self.wait_routers("mycluster-router-.*", 1)

        [new_pod] = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        router_config = kutil.cat(self.ns, new_pod["NAME"], "/tmp/mysqlrouter/mysqlrouter.conf")
        self.assertIn(b"name=othername", router_config, "router config does not contain the name")

    def test_3_update_config(self):
        [old_pod] = kutil.ls_po(self.ns, pattern="mycluster-router-.*")

        patch = {
            "spec": {
                "router": {
                    "options": [
                        "--pid-file=/tmp/with`backtick"
                    ]
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")

        self.wait_pod_gone(old_pod["NAME"], ns=self.ns)
        self.wait_routers("mycluster-router-.*", 1)

        [new_pod] = kutil.ls_po(self.ns, pattern="mycluster-router-.*")
        self.assertTrue(kutil.file_exists(self.ns, new_pod["NAME"], "/tmp/with`backtick"))

    def test_4_initial_routing_options(self):
        routing_options = get_routing_options(self.ns, "mycluster-0")
        rules = routing_options["configuration"]["routing_rules"]
        self.assertEqual(rules["read_only_targets"], "read_replicas")
        # stats_updates_frequencies is set as it has a default value in the CRD,
        # which matches router's default
        self.assertEqual(rules["stats_updates_frequency"], 0)

    def test_5_add_routing_options(self):
        patch = {
            "spec": {
                "router": {
                    "routingOptions": {
                        "stats_updates_frequency": 10
                    }
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        routing_options = get_routing_options(self.ns, "mycluster-0")
        rules = routing_options["configuration"]["routing_rules"]
        self.assertEqual(rules["read_only_targets"], "read_replicas")
        self.assertEqual(rules["stats_updates_frequency"], 10)

    def test_6_remove_routing_options(self):
        patch = {
            "spec": {
                "router": {
                    "routingOptions": None
                }
            }
        }
        kutil.patch_ic(self.ns, "mycluster", patch, type="merge")
        routing_options = get_routing_options(self.ns, "mycluster-0")
        rules = routing_options["configuration"]["routing_rules"]
        self.assertEqual(rules["read_only_targets"], "secondaries")
        self.assertEqual(rules["stats_updates_frequency"], -1)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
