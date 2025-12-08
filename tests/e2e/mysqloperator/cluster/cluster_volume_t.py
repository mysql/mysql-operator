# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
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
    _cluster_size = 4
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
        #             "claimName": "datadir-{self.cluster_name}-0"
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
        kutil.create_default_user_secrets(self.ns, name=self.cluster_secret_name)

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
  datadirVolumeClaimTemplate:
    accessModes: [ "ReadWriteOnce" ]
    resources:
      requests:
        storage: 3Gi
"""

        kutil.apply(self.ns, yaml)

        self.wait_ic(self.cluster_name, ["PENDING", "INITIALIZING", "ONLINE"])

        for instance in range(0, self.cluster_size):
            self.wait_pod(f"{self.cluster_name}-{instance}", "Running")

        self.wait_ic(self.cluster_name, "ONLINE", num_online=self.cluster_size)

        if self.routers_count:
            self.wait_routers(f"{self.cluster_name}-router-*", self.routers_count, timeout=self.cluster_size*120)

        check_all(self, self.ns, self.cluster_name, instances=self.cluster_size, routers=self.routers_count, primary=0)

        self.check_ic_datadir(self.cluster_name)

        self.check_sts_datadir(self.cluster_name)

        for instance in range(0, self.cluster_size):
            pod_name = f"{self.cluster_name}-{instance}"
            with self.subTest(pod_name):
                self.check_pod_datadir(pod_name)

    def test_9_destroy(self):
        kutil.delete_ic(self.ns, self.cluster_name)
        self.wait_pods_gone(f"{self.cluster_name}-*")
        self.wait_routers_gone(f"{self.cluster_name}-router-*")
        self.wait_ic_gone(self.cluster_name)
        kutil.delete_pvc(self.ns, None)

        kutil.delete_secret(self.ns, self.cluster_secret_name)
