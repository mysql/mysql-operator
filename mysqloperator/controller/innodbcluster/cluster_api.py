# Copyright (c) 2020, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

import kopf
from .. import utils, config, consts
from ..backup.backup_api import BackupProfile
from ..storage_api import StorageSpec
from ..api_utils import dget_dict, dget_str, dget_int, dget_list, ApiSpecError
from ..utils import version_to_int
from ..kubeutils import api_core, api_apps, api_customobj, ApiException
import yaml
import json
import datetime
import time
from kubernetes import client


MAX_CLUSTER_NAME_LEN = 28


class SecretData:
    secret_name = None
    key = None


class CloneInitDBSpec:
    uri = None
    password_secret_name = None
    password_secret_key = None
    root_user = None

    def parse(self, spec, prefix):
        self.uri = spec.get("donorUrl")
        self.root_user = dget_str(spec, "rootUser", prefix, "root")
        key_ref = dget_dict(spec, "secretKeyRef", prefix)
        self.password_secret_name = dget_str(key_ref, "name", prefix+".secretKeyRef")
        self.password_secret_key = dget_str(key_ref, "clonePasswordKey", prefix+".secretKeyRef")

    def get_password(self, ns):
        secret = api_core.read_namespaced_secret(self.password_secret_name, ns)

        return utils.b64decode(secret.data[self.password_secret_key])

class SnapshotInitDBSpec:
    storage = None # StorageSpec

    def parse(self, spec, prefix):
        self.storage = StorageSpec()
        self.storage.parse(dget_dict(spec, "storage", prefix), prefix+".storage")


class DumpInitDBSpec:
    path = None
    storage = None # StorageSpec

    def parse(self, spec, prefix):
        # path can be "" if we're loading from a bucket
        self.path = dget_str(spec, "path", prefix, "")

        self.storage = StorageSpec()
        self.storage.parse(dget_dict(spec, "storage", prefix), prefix+".storage")



class SQLInitDB:
    storage = None # DataLocation


class InitDB:
    clone = None # CloneInitDBSpec
    snapshot = None # SnapshotInitDBSpec
    dump = None # DumpInitDBSpec

    def parse(self, spec, prefix):
        dump = dget_dict(spec, "dump", "spec.initDB", {})
        clone = dget_dict(spec, "clone", "spec.initDB", {})
        snapshot = dget_dict(spec, "snapshot", "spec.initDB", {})
        if len([x for x in [dump, clone, snapshot] if x]) > 1:
            raise ApiSpecError("Only one of dump, snapshot or clone may be specified in spec.initDB")
        if not dump and not clone and not snapshot:
            raise ApiSpecError("One of dump, snapshot or clone may be specified in spec.initDB")

        if clone:
            self.clone = CloneInitDBSpec()
            self.clone.parse(clone, "spec.initDB.clone")
        elif dump:
            self.dump = DumpInitDBSpec()
            self.dump.parse(dump, "spec.initDB.dump")
        elif snapshot:
            self.snapshot = SnapshotInitDBSpec()
            self.snapshot.parse(snapshot, "spec.initDB.snapshot")


class Backup:
    method = None # dump
    destination = None # DataLocation
    schedule = None


class InnoDBClusterSpec:
    # name of user-provided secret containing root password and SSL certificates (optional)
    secretName = None
    # secret with SSL certificates
    sslSecretName = None

    version = config.DEFAULT_VERSION_TAG

    shellImage = config.MYSQL_SHELL_IMAGE + ":" + config.DEFAULT_SHELL_VERSION_TAG

    image = config.MYSQL_SERVER_IMAGE + ":" + config.DEFAULT_SERVER_VERSION_TAG
    # number of MySQL instances (required)
    instances = None
    # base value for server_id
    baseServerId = config.DEFAULT_BASE_SERVER_ID
    # override volumeClaimTemplates for MySQL pods (optional)
    volumeClaimTemplates = None
    # additional MySQL configuration options
    mycnf = None
    # override pod template for MySQL (optional)
    podSpec = None
    # Initialize DB
    initDB = None

    routerImage = config.MYSQL_ROUTER_IMAGE + ":" + config.DEFAULT_ROUTER_VERSION_TAG
    # number of Router instances (optional)
    routers = 0
    # override pod template for Router (optional)
    routerSpec = None

    # Backup info
    backupProfiles = None
    backupSchedules = None

    # (currently) non-configurable constants
    mysql_port = 3306
    mysql_xport = 33060
    mysql_grport = 33061

    router_rwport = 6446
    router_roport = 6447
    router_rwxport = 64460
    router_roxport = 64470
    router_httpport = 8080
  
    def __init__(self, namespace, name, spec):
        self.namespace = namespace
        self.name = name
        self.load(spec)

    def load(self, spec):
        self.secretName = dget_str(spec, "secretName", "spec")

        self.instances = dget_int(spec, "instances", "spec")

        if "podSpec" in spec:
            self.podSpec = spec.get("podSpec")

        if "volumeClaimTemplates" in spec:
            self.volumeClaimTemplates = spec.get("volumeClaimTemplates")

        if "mycnf" in spec:
            self.mycnf = spec.get("mycnf")

        if "routers" in spec:
            self.routers = spec.get("routers")

        if "routerSpec" in spec:
            self.routerSpec = spec.get("routerSpec")

        if "initDB" in spec:
            self.load_initdb(spec.get("initDB"))

        if "image" in spec:
            self.image = spec.get("image")

        if "routerImage" in spec:
            self.routerImage = spec.get("routerImage")

        # TODO keep a list of base_server_id in the operator to keep things globally unique?
        if "baseServerId" in spec:
            self.baseServerId = spec.get("baseServerId")

        profiles = dget_list(spec, "backupProfiles", "spec", [], content_type=dict)
        self.backupProfiles = []
        for profile in profiles:
            self.backupProfiles.append(self.parse_backup_profile(profile))

        schedules = dget_list(spec, "backupSchedules", "spec", [], content_type=dict)
        self.backupSchedules = []
        for sched in schedules:
            self.backupSchedules.append(self.parse_backup_schedule(sched))


    def parse_backup_profile(self, spec):
        profile = BackupProfile()
        profile.parse(spec, "spec.backupProfiles")
        return profile


    def parse_backup_schedule(self, spec):
        pass


    def load_initdb(self, spec):
        self.initDB = InitDB()
        self.initDB.parse(spec, "spec.initDB")


    def get_backup_profile(self, name):
        if self.backupProfiles:
            for profile in self.backupProfiles:
                if profile.name == name:
                    return profile
        return None


    def validate(self, logger):
        # TODO see if we can move some of these to a schema in the CRD

        if len(self.name) > MAX_CLUSTER_NAME_LEN:
            raise ApiSpecError(
                f"Cluster name {self.name} is too long. Must be < {MAX_CLUSTER_NAME_LEN}")

        if not self.instances:
            raise ApiSpecError(
                f"spec.instances must be set and > 0. Got {instances!r}")

        if self.routers is None:
            raise ApiSpecError(
                f"spec.routers must be set. Got {routers!r}")

        if not self.baseServerId or self.baseServerId < config.MIN_BASE_SERVER_ID or self.baseServerId > config.MAX_BASE_SERVER_ID:
            raise ApiSpecError(
                f"spec.baseServerId must be between {config.MIN_BASE_SERVER_ID} and {config.MAX_BASE_SERVER_ID}")

        # check that the secret exists and it contains rootPassword
        if self.secretName: # TODO
            pass

        # validate podSpec through the Kubernetes API
        if self.podSpec:
            pass

        # validate routerSpec through the Kubernetes API
        if self.routerSpec:
            pass

        if self.mycnf:
            if "[mysqld]" not in self.mycnf:
                logger.warning("spec.mycnf data does not contain a [mysqld] line")

        def check_image(image, option):
            name, _, version = self.image.partition(":")
            try:
                if "-" in version:
                    version = version.split("-")[0]
                version = version_to_int(version)
            except Exception as e:
                logger.debug(f"Can't parse image name {image}: {e}")
                raise ApiSpecError(
                    f"spec.{option} has an invalid value {image}")
            
            if version < version_to_int(config.MIN_SUPPORTED_MYSQL_VERSION):
                raise ApiSpecError(
                    f"spec.{option} is for an unsupported version {version}. Must be at least {config.MIN_SUPPORTED_MYSQL_VERSION}")

            if version > version_to_int(config.MAX_SUPPORTED_MYSQL_VERSION):
                raise ApiSpecError(
                    f"spec.{option} is for an unsupported version {version}. Must be at most {config.MAX_SUPPORTED_MYSQL_VERSION}, unless the Operator is upgraded.")

        check_image(self.image, "image")
        check_image(self.routerImage, "routerImage")

    @property
    def mysql_image_pull_policy(self):
        return config.mysql_image_pull_policy

    @property
    def router_image_pull_policy(self):
        return config.router_image_pull_policy

    @property
    def shell_image_pull_policy(self):
        return config.shell_image_pull_policy

    @property
    def extra_env(self):
        if config.debug:
            return f"""
- name: MYSQL_OPERATOR_DEBUG
  value: "{config.debug}"
"""
        else:
            return ""





class InnoDBCluster:
    def __init__(self, cluster):
        self.obj = cluster

        self.parsed_spec = InnoDBClusterSpec(self.namespace, self.name, self.spec)


    def __str__(self):
        return f"{self.namespace}/{self.name}"

    def __repr__(self):
        return f"<InnoDBCluster {self.name}>"

    @classmethod
    def read(cls, name, namespace):
        return InnoDBCluster(api_customobj.get_namespaced_custom_object(
            consts.GROUP, consts.VERSION, namespace, consts.INNODBCLUSTER_PLURAL, name))

    @property
    def metadata(self):
        return self.obj["metadata"]

    @property
    def spec(self):
        return self.obj["spec"]

    @property
    def status(self):
        if "status" in self.obj:
            return self.obj["status"]
        return {}

    @property
    def name(self):
        return self.metadata["name"]

    @property
    def namespace(self):
        return self.metadata["namespace"]

    @property
    def uid(self):
        return self.metadata["uid"]

    @property
    def deleting(self):
        return "deletionTimestamp" in self.metadata and self.metadata["deletionTimestamp"] is not None


    def reload(self):
        self.obj = api_customobj.get_namespaced_custom_object(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name)


    def owns_pod(self, pod):
        owner_sts = pod.owner_reference("apps/v1", "StatefulSet")
        return owner_sts.name == self.name

    def get_pod(self, index):
        pod = api_core.read_namespaced_pod("%s-%i"%(self.name, index), self.namespace)
        return MySQLPod(pod)

    def get_pods(self):
        # get all pods that belong to the same container
        objects = api_core.list_namespaced_pod(
            self.namespace, label_selector="component=mysqld")

        pods = []

        # Find the MySQLServer object corresponding to the server we're attached to
        for o in objects.items:
            pod = MySQLPod(o)
            if self.owns_pod(pod):
                pods.append(pod)
        pods.sort(key=lambda pod: pod.index)
        return pods

    def get_service(self):
        try:
            return api_core.read_namespaced_service(self.name+"-instances", self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_stateful_set(self):
        try:
            return api_apps.read_namespaced_stateful_set(self.name, self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_router_service(self):
        try:
            return api_core.read_namespaced_service(self.name, self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_router_replica_set(self):
        try:
            return api_apps.read_namespaced_replica_set(self.name+"-router", self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_router_account(self):
        try:
            secret = api_core.read_namespaced_secret(f"{self.name}-router", self.namespace)

            return utils.b64decode(secret.data["routerUsername"]), utils.b64decode(secret.data["routerPassword"])

        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_backup_account(self):
        try:
            secret = api_core.read_namespaced_secret(f"{self.name}-backup", self.namespace)

            return utils.b64decode(secret.data["backupUsername"]), utils.b64decode(secret.data["backupPassword"])

        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_private_secrets(self):
        try:
            return api_core.read_namespaced_secret(f"{self.name}-privsecrets", self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_user_secrets(self):
        name = self.spec.get("secretName")
        try:
            return api_core.read_namespaced_secret(f"{name}", self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_admin_account(self):
        secrets = self.get_private_secrets()
        if secrets:
            return utils.b64decode(secrets.data["clusterAdminUsername"]), utils.b64decode(secrets.data["clusterAdminPassword"])
        return None

    def get_initconf(self):
        try:
            return api_core.read_namespaced_config_map(f"{self.name}-initconf", self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_initmysql(self):
        try:
            return api_core.read_namespaced_config_map(f"{self.name}-initmysql", self.namespace)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def _get_status_field(self, field):
        return self.status.get(field)

    def _set_status_field(self, field, value):
        if isinstance(value, datetime.datetime):
            value = value.replace(microsecond=0).isoformat()+"Z"

        obj = api_customobj.get_namespaced_custom_object(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name)
        
        if "status" not in obj:
            patch = {"status": {}}
        else:
            patch = {"status": obj["status"]}
        patch["status"][field] = value
        self.obj = api_customobj.patch_namespaced_custom_object_status(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name, patch)


    def set_cluster_status(self, cluster_status):
        self._set_status_field("cluster", cluster_status)


    def get_cluster_status(self, field=None):
        status = self._get_status_field("cluster")
        if status and field:
            return status.get(field)
        return status

    def set_status(self, status):
        obj = api_customobj.get_namespaced_custom_object(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name)
        
        if "status" not in obj:
            obj["status"] = status
        else:
            obj["status"] = utils.merge_patch_object(obj["status"], status)
        self.obj = api_customobj.patch_namespaced_custom_object_status(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name, obj)

    def update_cluster_info(self, info):
        """
        Set metadata about the cluster as an annotation.
        Information consumed by ourselves to manage the cluster should go here.
        Information consumed by external observers should go in status.
        """
        patch = {
            "metadata": {
                "annotations": {
                    "mysql.oracle.com/cluster-info": json.dumps(info)
                }
            }
        }
        self.obj = api_customobj.patch_namespaced_custom_object(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name, patch)


    def get_cluster_info(self, field=None):
        if self.metadata["annotations"]:
            info = self.metadata["annotations"].get("mysql.oracle.com/cluster-info", None)
            if info:
                info = json.loads(info)
                if field:
                    return info.get(field)
                return info
        return None

    def set_create_time(self, time):
        self._set_status_field("createTime", time)


    def get_create_time(self):
        return self._get_status_field("createTime")

    def set_last_known_quorum(self, members):
        ### TODO
        pass

    def get_last_known_quorum(self):
        ### TODO
        return None

    def incremental_recovery_allowed(self):
        return self.get_cluster_info("incrementalRecoveryAllowed")

    def _add_finalizer(self, fin):
        """
        Add the named token to the list of finalizers for the cluster object.
        The cluster object will be blocked from deletion until that token is
        removed from the list (remove_finalizer).
        """
        patch = {
            "metadata": {
                "finalizers": [fin]
            }
        }
        self.obj = api_customobj.patch_namespaced_custom_object(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name, body=patch)

    def _remove_finalizer(self, fin):
        # TODO strategic merge patch not working here??
        #patch = { "metadata": { "$deleteFromPrimitiveList/finalizers": [fin] }}
        patch = {"metadata": {"finalizers": [f for f in self.metadata["finalizers"] if f != fin]}}
        self.obj = api_customobj.patch_namespaced_custom_object(
            consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name, body=patch)

    def add_cluster_finalizer(self):
        self._add_finalizer("mysql.oracle.com/cluster")

    def remove_cluster_finalizer(self, cluster_body = None):
        self._remove_finalizer("mysql.oracle.com/cluster")
        if cluster_body:
            # modify the JSON data used internally by kopf to update its finalizer list
            cluster_body["metadata"]["finalizers"].remove("mysql.oracle.com/cluster")

    def set_current_version(self, version):
        v = self.status.get("version")
        if v != version:
            patch = {"status": {"version": version}}

            # TODO store the current server/router version + timestamp
            # store previous versions in a version history log
            self.obj = api_customobj.patch_namespaced_custom_object(
                consts.GROUP, consts.VERSION, self.namespace, consts.INNODBCLUSTER_PLURAL, self.name, body=patch)

    # TODO store last known majority and use it for diagnostics when there are
    # unconnectable pods



def get_all_clusters():
    objects = api_customobj.list_cluster_custom_object(consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL)
    return [InnoDBCluster(o) for o in objects["items"]]


class MySQLPod:
    def __init__(self, pod):
        if isinstance(pod, client.V1Pod):
            self.pod = pod
        else:
            class Wrapper:
                def __init__(self, data):
                    self.data = json.dumps(data)

            if not isinstance(pod, str):
                pod = eval(str(pod))

            self.pod = api_core.api_client.deserialize(
                Wrapper(pod), client.V1Pod)

        self.port = 3306
        self.xport = 33060

        self.admin_account = None

    def __str__(self):
        return self.name

    def __repr__(self):
        return f"<MySQLPod {self.name}>"

    @classmethod
    def read(cls, name, namespace):
        return MySQLPod(api_core.read_namespaced_pod(name, namespace))

    @property
    def metadata(self):
        return self.pod.metadata

    @property
    def status(self):
        return self.pod.status

    @property
    def phase(self):
        return self.status.phase

    @property
    def deleting(self):
        return self.metadata.deletion_timestamp is not None

    @property
    def spec(self):
        return self.pod.spec

    @property
    def name(self):
        return self.metadata.name

    @property
    def index(self):
        return int(self.name.rpartition("-")[-1])

    @property
    def namespace(self):
        return self.metadata.namespace

    @property
    def cluster_name(self):
        return self.name.rpartition("-")[0]

    @property
    def address(self):
        return self.name+"."+self.spec.subdomain

    @property
    def address_fqdn(self):
        return self.name+"."+self.spec.subdomain+"."+self.namespace+".svc.cluster.local"

    @property
    def endpoint(self):
        return self.address_fqdn + ":" + str(self.port)

    @property
    def xendpoint(self):
        return self.address_fqdn + ":" + str(self.xport)

    @property
    def endpoint_co(self):
        if not self.admin_account:
            self.admin_account = self.get_cluster().get_admin_account()

        return {"scheme": "mysql",
                "user": self.admin_account[0],
                "password": self.admin_account[1],
                "host": self.address_fqdn,
                "port": self.port}

    @property
    def endpoint_url_safe(self):
        if not self.admin_account:
            self.admin_account = self.get_cluster().get_admin_account()

        return {"scheme": "mysql",
                "user": self.admin_account[0],
                "password": "****",
                "host": self.address_fqdn,
                "port": self.port}

    @property
    def xendpoint_co(self):
        if not self.admin_account:
            self.admin_account = self.get_cluster().get_admin_account()

        return {"scheme": "mysqlx",
                "user": self.admin_account[0],
                "password": self.admin_account[1],
                "host": self.address_fqdn,
                "port": self.xport}

    def reload(self):
        self.pod = api_core.read_namespaced_pod(self.name, self.namespace)

    def owner_reference(self, api_version, kind):
        for owner in self.metadata.owner_references:
            if owner.api_version == api_version and owner.kind == kind:
                return owner

        return None

    def get_cluster(self):
        try:
            return InnoDBCluster.read(self.cluster_name, self.namespace)
        except ApiException as e:
            print(f"Could not get cluster {self.namespace}/{self.cluster_name}: {e}")
            if e.status == 404:
                return None
            raise

    def check_condition(self, cond_type):
        if self.status and self.status.conditions:
            for c in self.status.conditions:
                if c.type == cond_type:
                    return c.status == "True"

        return None

    def check_containers_ready(self):
        return self.check_condition("ContainersReady")

    def check_container_ready(self, container_name):
        if self.status.container_statuses:
            for cs in self.status.container_statuses:
                if cs.name == container_name:
                    return cs.ready
        return None
    
    def get_container_restarts(self, container_name):
        if self.status.container_statuses:
            for cs in self.status.container_statuses:
                if cs.name == container_name:
                    return cs.restart_count
        return None

    def get_member_readiness_gate(self, gate):
        return self.check_condition(f"mysql.oracle.com/{gate}")

    def update_member_readiness_gate(self, gate, value):
        now = utils.isotime()

        if self.check_condition(f"mysql.oracle.com/{gate}") != value:
            changed = True
        else:
            changed = False

        patch = {"status": {
            "conditions": [{
                "type": f"mysql.oracle.com/{gate}",
                "status": "True" if value else "False",
                "lastProbeTime": '%s' % now,
                "lastTransitionTime": '%s' % now if changed else None
            }]}}

        self.pod = api_core.patch_namespaced_pod_status(
            self.name, self.namespace, body=patch)
    

    def get_membership_info(self, field=None):
        if self.metadata.annotations:
            info = self.metadata.annotations.get("mysql.oracle.com/membership-info", None)
            if info:
                info = json.loads(info)
                if info and field:
                    return info.get(field)
                return info
        return None


    def update_membership_status(self, member_id, role, status, view_id, version, joined=False):
        now = utils.isotime()
        last_probe_time = now

        info = self.get_membership_info() or {}
        if not info or info.get("role") != role or info.get("status") != status or info.get("groupViewId") != view_id or info.get("memberId") != member_id:
            last_transition_time = now
        else:
            last_transition_time = info.get("lastTransitionTime")

        info.update({
            "memberId": member_id,
            "lastTransitionTime": last_transition_time,
            "lastProbeTime": last_probe_time,
            "groupViewId": view_id,
            "status": status,
            "version": version,
            "role": role
        })
        if joined:
            info["joinTime"] = now

        patch = {
            "metadata": {
                "labels": {
                    "mysql.oracle.com/cluster-role": role if status == "ONLINE" else None
                },
                "annotations": {
                    "mysql.oracle.com/membership-info": json.dumps(info)
                }
            }
        }
        self.pod = api_core.patch_namespaced_pod(self.name, self.namespace, patch)

    def add_member_finalizer(self):
        self._add_finalizer("mysql.oracle.com/membership")

    def remove_member_finalizer(self, pod_body = None):
        self._remove_finalizer("mysql.oracle.com/membership", pod_body)
    
    def _add_finalizer(self, fin):
        """
        Add the named token to the list of finalizers for the Pod.
        The Pod will be blocked from deletion until that token is
        removed from the list (remove_finalizer).
        """
        patch = { "metadata": { "finalizers": [fin] }}
        self.obj = api_core.patch_namespaced_pod(
            self.name, self.namespace, body=patch)

    def _remove_finalizer(self, fin, pod_body=None):
        patch = { "metadata": { "$deleteFromPrimitiveList/finalizers": [fin] }}
        self.obj = api_core.patch_namespaced_pod(
            self.name, self.namespace, body=patch)

        if pod_body:
            # modify the JSON data used internally by kopf to update its finalizer list
            if fin in pod_body["metadata"]["finalizers"]:
                pod_body["metadata"]["finalizers"].remove(fin)

