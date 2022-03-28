# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import tutil
from utils import kutil
from .cluster_t import check_all
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS

class ClusterVolume(tutil.OperatorTest):
    """
    cluster volumes
    """
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

        g_full_log.watch_mysql_pod(cls.ns, "mycluster-0")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-1")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-2")
        g_full_log.watch_mysql_pod(cls.ns, "mycluster-3")

    @classmethod
    def tearDownClass(cls):
        g_full_log.stop_watch(cls.ns, "mycluster-3")
        g_full_log.stop_watch(cls.ns, "mycluster-2")
        g_full_log.stop_watch(cls.ns, "mycluster-1")
        g_full_log.stop_watch(cls.ns, "mycluster-0")

        super().tearDownClass()

    def look_up_volume_mount(self, containers, container_name):
        # look for e.g. json-path: spec.containers[1].volumeMounts[0]
        for container in containers:
            if container["name"] == container_name:
                volume_mounts = container["volumeMounts"]
                for volume_mount in volume_mounts:
                    if volume_mount["name"] == "datadir":
                        return volume_mount
        return None

    def check_ic_datadir(self, icname):
        icobj = kutil.get_ic(self.ns, icname)
        datadir_spec = icobj["spec"]["datadirVolumeClaimTemplate"]
        self.assertEqual(datadir_spec["accessModes"], ["ReadWriteOnce"])
        self.assertEqual(datadir_spec["resources"]["requests"]["storage"], "3Gi")

    def check_sts_datadir(self, stsname):
        sts = kutil.get_sts(self.ns, stsname)
        template_spec = sts["spec"]["template"]["spec"]

        # json-path: spec.template.spec.containers[1].volumeMounts[0].name
        # "name": "mysql",
        # [...]
        # "volumeMounts": [
        #     [...]
        #     {
        #         "mountPath": "/var/lib/mysql",
        #         "name": "datadir"
        #     },
        containers = template_spec["containers"]
        volume_mount = self.look_up_volume_mount(containers, "mysql")
        self.assertIsNotNone(volume_mount, "datadir mount not found")
        self.assertEqual(volume_mount["mountPath"], "/var/lib/mysql")

        # json-path: spec.template.spec.initContainers[0].volumeMounts[1].name
        # "name": "initconf",
        # [...]
        # "volumeMounts": [
        #     [...]
        #     {
        #         "mountPath": "/var/lib/mysql",
        #         "name": "datadir"
        #     },
        init_containers = template_spec["initContainers"]
        volume_mount = self.look_up_volume_mount(init_containers, "initconf")
        self.assertIsNotNone(volume_mount, "datadir mount not found")
        self.assertEqual(volume_mount["mountPath"], "/var/lib/mysql")

        volume_mount = self.look_up_volume_mount(init_containers, "initmysql")
        self.assertIsNotNone(volume_mount, "datadir mount not found")
        self.assertEqual(volume_mount["mountPath"], "/var/lib/mysql")

        # json-path: spec.volumeClaimTemplates[0]
        # [...]
        # "volumeClaimTemplates": [
        #     {
        #         "apiVersion": "v1",
        #         "kind": "PersistentVolumeClaim",
        #         "metadata": {
        #             "creationTimestamp": null,
        #             "name": "datadir"
        #         },
        #     [...]
        volume_templates = sts["spec"]["volumeClaimTemplates"]
        pvc_template_found = False
        for volume_template in volume_templates:
            if volume_template["metadata"]["name"] == "datadir":
                self.assertEqual(volume_template["kind"], "PersistentVolumeClaim")
                volume_template_spec = volume_template["spec"]
                self.assertEqual(volume_template_spec["accessModes"], ["ReadWriteOnce"])
                self.assertEqual(volume_template_spec["resources"]["requests"]["storage"], "3Gi")
                self.assertEqual(volume_template_spec["volumeMode"], "Filesystem")
                pvc_template_found = True
                break
        self.assertTrue(pvc_template_found, "datadir volume claim template not found")

    def check_pod_datadir(self, podname):
        pod = kutil.get_po(self.ns, podname)
        spec = pod["spec"]

        # json-path: spec.containers[1].volumeMounts[0].name
        # "name": "mysql",
        # [...]
        # "volumeMounts": [
        #     [...]
        #     {
        #         "mountPath": "/var/lib/mysql",
        #         "name": "datadir"
        #     },
        containers = spec["containers"]
        volume_mount = self.look_up_volume_mount(containers, "mysql")
        self.assertIsNotNone(volume_mount, "datadir mount not found")
        self.assertEqual(volume_mount["mountPath"], "/var/lib/mysql")

        # json-path: spec.initContainers[0].volumeMounts[1].name
        # "name": "initconf",
        # [...]
        # "volumeMounts": [
        #     [...]
        #     {
        #         "mountPath": "/var/lib/mysql",
        #         "name": "datadir"
        #     },
        init_containers = spec["initContainers"]
        volume_mount = self.look_up_volume_mount(init_containers, "initconf")
        self.assertIsNotNone(volume_mount, "datadir mount not found")
        self.assertEqual(volume_mount["mountPath"], "/var/lib/mysql")

        volume_mount = self.look_up_volume_mount(init_containers, "initmysql")
        self.assertIsNotNone(volume_mount, "datadir mount not found")
        self.assertEqual(volume_mount["mountPath"], "/var/lib/mysql")

        # json-path: spec.volumes[0].persistentVolumeClaim.claimName
        # [...]
        # "volumes": [
        #     [...]
        #     {
        #         "name": "datadir",
        #         "persistentVolumeClaim": {
        #             "claimName": "datadir-mycluster-0"
        #         }
        #     },
        pvc_found = False
        volumes = spec["volumes"]
        for volume in volumes:
            if volume["name"] == "datadir":
                self.assertEqual(volume["persistentVolumeClaim"]["claimName"], f"datadir-{podname}")
                pvc_found = True
                break
        self.assertTrue(pvc_found, "datadir volume not found")

    def test_0_create_with_datadir(self):
        kutil.create_default_user_secrets(self.ns)

        yaml = """
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  instances: 4
  router:
    instances: 1
  secretName: mypwds
  tlsUseSelfSigned: true
  datadirVolumeClaimTemplate:
    accessModes: [ "ReadWriteOnce" ]
    resources:
      requests:
        storage: 3Gi
"""

        kutil.apply(self.ns, yaml)

        self.wait_pod("mycluster-0", "Running")
        self.wait_pod("mycluster-1", "Running")
        self.wait_pod("mycluster-2", "Running")
        self.wait_pod("mycluster-3", "Running")

        self.wait_ic("mycluster", "ONLINE", 4)

        # self.wait_routers("mycluster-router-*", 1)

        # check_all(self, self.ns, "mycluster", instances=4, routers=1, primary=0)

        self.check_ic_datadir("mycluster")

        self.check_sts_datadir("mycluster")

        self.check_pod_datadir("mycluster-0")
        self.check_pod_datadir("mycluster-1")
        self.check_pod_datadir("mycluster-2")
        self.check_pod_datadir("mycluster-3")

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, "mycluster")

        self.wait_pod_gone("mycluster-3")
        self.wait_pod_gone("mycluster-2")
        self.wait_pod_gone("mycluster-1")
        self.wait_pod_gone("mycluster-0")

        self.wait_ic_gone("mycluster")

        kutil.delete_secret(self.ns, "mypwds")
