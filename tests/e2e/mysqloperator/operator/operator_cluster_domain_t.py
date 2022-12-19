# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import logging
import json
import requests
import unittest

from utils import tutil
from utils import kutil
from utils import mutil
from utils.optesting import COMMON_OPERATOR_ERRORS
from setup.config import g_ts_cfg

def change_operator_cluster_domain(cluster_domain=None):
    """Change to the given operator configuration"""

    # Get name of current operator pod, once this is gone we know the new one
    # took over as it only be deleted once new one is ready
    pods = kutil.ls_pod("mysql-operator", "mysql-operator.*")

    old_pod = kutil.get_po("mysql-operator", pods[0]["NAME"])
    try:
        domain_env = next(filter(
            lambda e: e["name"] == "MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN",
            old_pod["spec"]["containers"][0]["env"]
        ))
    except StopIteration:
        domain_env = None

    if cluster_domain:
        if domain_env and domain_env["value"] == cluster_domain:
            # We already have the required env setting
            return

        # Patch version
        kutil.patch_dp("mysql-operator", "mysql-operator", {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [{
                            "name": "mysql-operator",
                            "env": [{
                                "name": "MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN",
                                "value": cluster_domain
                            }]
                        }]
                    }
                }
            }
        })
    else:
        if not domain_env:
            # We are already unset the environment
            return

        # patch to remove env
        deploy = kutil.get_deploy("mysql-operator", "mysql-operator")
        new_container = deploy["spec"]["template"]["spec"]["containers"][0]
        new_container["env"] = list(filter(lambda e: e["name"] != "MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN",
                                           new_container["env"]))

        kutil.patch_dp("mysql-operator", "mysql-operator", {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            new_container
                        ]
                    }
                }
            }
        }, type="merge")

    # Wait till old operator is gone
    if pods:
        kutil.wait_pod_gone("mysql-operator", pods[0]["NAME"])

@unittest.skipUnless(g_ts_cfg.k8s_cluster_domain_alias, "No cluster domain alias provided")
class OperatorClusterDomainTest(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        # Revert to current operator version under test, if tests passed this
        # should be a no-op as the test itself should do that already
        change_operator_cluster_domain()

        super().tearDownClass()

    def test_1_idc_with_alias(self):
        domain_alias = g_ts_cfg.k8s_cluster_domain_alias

        change_operator_cluster_domain(domain_alias)

        kutil.create_user_secrets(
            self.ns, "mypwds", root_user="root", root_host="%", root_pass="sakila")

        # create cluster with mostly default configs
        yaml = f"""
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 1
  router:
      instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_ic("mycluster", "ONLINE", 1)

        # Check the server Pod is correctly configured, this will then be used
        # for all the things
        with mutil.MySQLPodSession(self.ns, "mycluster-0", "root", "sakila") as s:
            expected_host = f"mycluster-0.mycluster-instances.{self.ns}.svc.{domain_alias}"
            report_host = s.query_sql("SELECT @@report_host").fetch_one()[0]
            self.assertEqual(report_host, expected_host)

            # create router REST API user using mysql user password
            mutil.router_rest_api_create_user(s, "root")

        # Check Router uses the right name
        self.wait_routers("mycluster-router-*", 1)
        router = kutil.ls_po(self.ns, pattern="mycluster-router-.*")[0]

        with kutil.PortForward(self.ns, router["NAME"], "http") as port:
            r = requests.get(f'https://127.0.0.1:{port}/api/20190715/metadata/bootstrap/config',
                            auth=("root", "sakila"), verify=False)
            self.assertEqual(r.status_code, 200)

            nodes = r.json()["nodes"]
            expected_nodes = [{
                "hostname": f"mycluster-0.mycluster-instances.{self.ns}.svc.{domain_alias}",
                "port": 3306
            }]
            self.assertEqual(nodes, expected_nodes)


    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-0")
        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")

        change_operator_cluster_domain()
