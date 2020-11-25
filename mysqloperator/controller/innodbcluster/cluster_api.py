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

import typing
from typing import Optional, List, Tuple, cast, overload
import kopf
from kubernetes.client.models.v1_secret import V1Secret
from .. import utils, config, consts
from ..backup.backup_api import BackupProfile
from ..storage_api import StorageSpec
from ..api_utils import dget_dict, dget_str, dget_int, dget_list, ApiSpecError
from ..utils import version_to_int
from ..kubeutils import api_core, api_apps, api_customobj
from ..kubeutils import client as api_client, ApiException
from logging import Logger
import yaml
import json
import datetime
import time
from kubernetes import client


MAX_CLUSTER_NAME_LEN = 28


class SecretData:
    secret_name: Optional[str] = None
    key: Optional[str] = None


class CloneInitDBSpec:
    uri: str = ""
    password_secret_name: Optional[str] = None
    root_user: Optional[str] = None

    def parse(self, spec: dict, prefix: str) -> None:
        self.uri = dget_str(spec, "donorUrl", prefix)  # TODO make mandatory
        self.root_user = dget_str(spec, "rootUser", prefix, "root")
        key_ref = dget_dict(spec, "secretKeyRef", prefix)
        self.password_secret_name = dget_str(
            key_ref, "name", prefix+".secretKeyRef")

    def get_password(self, ns: str) -> str:
        secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
            self.password_secret_name, ns))

        return utils.b64decode(secret.data["rootPassword"])


class SnapshotInitDBSpec:
    storage: Optional[StorageSpec] = None

    def parse(self, spec: dict, prefix: str) -> None:
        self.storage = StorageSpec()
        self.storage.parse(
            dget_dict(spec, "storage", prefix), prefix+".storage")


class DumpInitDBSpec:
    path: Optional[str] = None
    storage: Optional[StorageSpec] = None
    loadOptions: dict = {}  # TODO

    def parse(self, spec: dict, prefix: str) -> None:
        # path can be "" if we're loading from a bucket
        self.path = dget_str(spec, "path", prefix, "")

        self.storage = StorageSpec()
        self.storage.parse(
            dget_dict(spec, "storage", prefix), prefix+".storage")


class SQLInitDB:
    storage = None  # TODO type


class InitDB:
    clone: Optional[CloneInitDBSpec] = None
    snapshot: Optional[SnapshotInitDBSpec] = None
    dump: Optional[DumpInitDBSpec] = None

    def parse(self, spec: dict, prefix: str) -> None:
        dump = dget_dict(spec, "dump", "spec.initDB", {})
        clone = dget_dict(spec, "clone", "spec.initDB", {})
        snapshot = dget_dict(spec, "snapshot", "spec.initDB", {})
        if len([x for x in [dump, clone, snapshot] if x]) > 1:
            raise ApiSpecError(
                "Only one of dump, snapshot or clone may be specified in spec.initDB")
        if not dump and not clone and not snapshot:
            raise ApiSpecError(
                "One of dump, snapshot or clone may be specified in spec.initDB")

        if clone:
            self.clone = CloneInitDBSpec()
            self.clone.parse(clone, "spec.initDB.clone")
        elif dump:
            self.dump = DumpInitDBSpec()
            self.dump.parse(dump, "spec.initDB.dump")
        elif snapshot:
            self.snapshot = SnapshotInitDBSpec()
            self.snapshot.parse(snapshot, "spec.initDB.snapshot")


class BackupSchedule:
    method: str = ""  # dump
    storage: Optional[StorageSpec] = None
    schedule = None


class InnoDBClusterSpec:
    # name of user-provided secret containing root password and SSL certificates (optional)
    secretName: Optional[str] = None
    # secret with SSL certificates
    sslSecretName: Optional[str] = None

    # MySQL server version
    version: str = config.DEFAULT_VERSION_TAG

    # TODO Router version, if user wants to override it (latest by default)
    # routerVersion : Optional[str] = None

    # number of MySQL instances (required)
    instances: int = 1
    # base value for server_id
    baseServerId: int = config.DEFAULT_BASE_SERVER_ID
    # override volumeClaimTemplates for MySQL pods (optional)
    volumeClaimTemplates = None
    # additional MySQL configuration options
    mycnf: str = ""
    # override pod template for MySQL (optional)
    podSpec = None
    # Initialize DB
    initDB: Optional[InitDB] = None

    # number of Router instances (optional)
    routers: int = 0
    # override pod template for Router (optional)
    routerSpec = None

    # Backup info
    backupProfiles: List[BackupProfile] = []
    backupSchedules: List[BackupSchedule] = []

    # (currently) non-configurable constants
    mysql_port: int = 3306
    mysql_xport: int = 33060
    mysql_grport: int = 33061

    router_rwport: int = 6446
    router_roport: int = 6447
    router_rwxport: int = 6448
    router_roxport: int = 6449
    router_httpport: int = 8080

    def __init__(self, namespace: str, name: str, spec: dict):
        self.namespace = namespace
        self.name = name
        self.load(spec)

    def load(self, spec: dict) -> None:
        self.secretName = dget_str(spec, "secretName", "spec")

        self.instances = dget_int(spec, "instances", "spec")

        if "version" in spec:
            self.version = dget_str(spec, "version", "spec")

        if "podSpec" in spec:  # TODO - replace with something more specific
            self.podSpec = spec.get("podSpec")

        if "volumeClaimTemplates" in spec:
            self.volumeClaimTemplates = spec.get("volumeClaimTemplates")

        if "mycnf" in spec:
            self.mycnf = dget_str(spec, "mycnf", "spec")

        if "routers" in spec:
            self.routers = dget_int(spec, "routers", "spec")

        if "routerSpec" in spec:  # TODO - replace with something more specific
            self.routerSpec = spec.get("routerSpec")

        if "initDB" in spec:
            self.load_initdb(dget_dict(spec, "initDB", "spec"))

        # TODO keep a list of base_server_id in the operator to keep things globally unique?
        if "baseServerId" in spec:
            self.baseServerId = dget_int(spec, "baseServerId", "spec")

        profiles = dget_list(spec, "backupProfiles",
                             "spec", [], content_type=dict)
        self.backupProfiles = []
        for profile in profiles:
            self.backupProfiles.append(self.parse_backup_profile(profile))

        schedules = dget_list(spec, "backupSchedules",
                              "spec", [], content_type=dict)
        self.backupSchedules = []
        for sched in schedules:
            self.backupSchedules.append(self.parse_backup_schedule(sched))

    def parse_backup_profile(self, spec: dict) -> BackupProfile:
        profile = BackupProfile()
        profile.parse(spec, "spec.backupProfiles")
        return profile

    def parse_backup_schedule(self, spec: dict) -> BackupSchedule:
        return BackupSchedule()

    def load_initdb(self, spec: dict) -> None:
        self.initDB = InitDB()
        self.initDB.parse(spec, "spec.initDB")

    def get_backup_profile(self, name: str) -> Optional[BackupProfile]:
        if self.backupProfiles:
            for profile in self.backupProfiles:
                if profile.name == name:
                    return profile
        return None

    def validate(self, logger: Logger) -> None:
        # TODO see if we can move some of these to a schema in the CRD

        if len(self.name) > MAX_CLUSTER_NAME_LEN:
            raise ApiSpecError(
                f"Cluster name {self.name} is too long. Must be < {MAX_CLUSTER_NAME_LEN}")

        if not self.instances:
            raise ApiSpecError(
                f"spec.instances must be set and > 0. Got {self.instances!r}")

        if self.routers is None:
            raise ApiSpecError(
                f"spec.routers must be set. Got {self.routers!r}")

        if not self.baseServerId or self.baseServerId < config.MIN_BASE_SERVER_ID or self.baseServerId > config.MAX_BASE_SERVER_ID:
            raise ApiSpecError(
                f"spec.baseServerId must be between {config.MIN_BASE_SERVER_ID} and {config.MAX_BASE_SERVER_ID}")

        # check that the secret exists and it contains rootPassword
        if self.secretName:  # TODO
            pass

        # validate podSpec through the Kubernetes API
        if self.podSpec:
            pass

        # validate routerSpec through the Kubernetes API
        if self.routerSpec:
            pass

        if self.mycnf:
            if "[mysqld]" not in self.mycnf:
                logger.warning(
                    "spec.mycnf data does not contain a [mysqld] line")

        # TODO ensure that if version is set, then image and routerImage are not
        # TODO should we support upgrading router only?

        def check_image(image, option):
            """
            name, _, version = self.image.rpartition(":")
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
            """
            pass

        # TODO check version instead
        #check_image(self.image, "image")
        #check_image(self.routerImage, "routerImage")

    @property
    def mysql_image(self) -> str:
        # server image version is the one given by the user or latest by default
        return f"{config.MYSQL_SERVER_IMAGE}:{self.version}"

    @property
    def router_image(self) -> str:
        # router image version is always the latest
        return f"{config.MYSQL_ROUTER_IMAGE}:{config.DEFAULT_ROUTER_VERSION_TAG}"

    @property
    def shell_image(self) -> str:
        # shell image version is the same as ours (operator)
        return f"{config.MYSQL_SHELL_IMAGE}:{config.DEFAULT_SHELL_VERSION_TAG}"

    @property
    def mysql_image_pull_policy(self) -> str:
        return config.mysql_image_pull_policy

    @property
    def router_image_pull_policy(self) -> str:
        return config.router_image_pull_policy

    @property
    def shell_image_pull_policy(self) -> str:
        return config.shell_image_pull_policy

    @property
    def extra_env(self) -> str:
        if config.debug:
            return f"""
- name: MYSQL_OPERATOR_DEBUG
  value: "{config.debug}"
"""
        else:
            return ""


class InnoDBCluster:
    def __init__(self, cluster: dict):
        self.obj: dict = cluster

        self.parsed_spec = InnoDBClusterSpec(
            self.namespace, self.name, self.spec)

    def __str__(self):
        return f"{self.namespace}/{self.name}"

    def __repr__(self):
        return f"<InnoDBCluster {self.name}>"

    @classmethod
    def _get(cls, ns: str, name: str) -> dict:
        return cast(dict,
                    api_customobj.get_namespaced_custom_object(
                        consts.GROUP, consts.VERSION, ns,
                        consts.INNODBCLUSTER_PLURAL, name))

    @classmethod
    def _patch(cls, ns: str, name: str, patch: dict) -> dict:
        return cast(dict,
                    api_customobj.patch_namespaced_custom_object(
                        consts.GROUP, consts.VERSION, ns,
                        consts.INNODBCLUSTER_PLURAL, name, body=patch))

    @classmethod
    def read(cls, ns: str, name: str) -> 'InnoDBCluster':
        return InnoDBCluster(cls._get(ns, name))

    @property
    def metadata(self) -> dict:
        return self.obj["metadata"]

    @property
    def spec(self) -> dict:
        return self.obj["spec"]

    @property
    def status(self) -> dict:
        if "status" in self.obj:
            return self.obj["status"]
        return {}

    @property
    def name(self) -> str:
        return self.metadata["name"]

    @property
    def namespace(self) -> str:
        return self.metadata["namespace"]

    @property
    def uid(self) -> str:
        return self.metadata["uid"]

    @property
    def deleting(self) -> bool:
        return "deletionTimestamp" in self.metadata and self.metadata["deletionTimestamp"] is not None

    def reload(self) -> None:
        self.obj = self._get(self.namespace, self.name)

    def owns_pod(self, pod) -> bool:
        owner_sts = pod.owner_reference("apps/v1", "StatefulSet")
        return owner_sts.name == self.name

    def get_pod(self, index) -> 'MySQLPod':
        pod = cast(api_client.V1Pod, api_core.read_namespaced_pod(
            "%s-%i" % (self.name, index), self.namespace))
        return MySQLPod(pod)

    def get_pods(self) -> typing.List['MySQLPod']:
        # get all pods that belong to the same container
        objects = cast(api_client.V1PodList, api_core.list_namespaced_pod(
            self.namespace, label_selector="component=mysqld"))

        pods = []

        # Find the MySQLServer object corresponding to the server we're attached to
        for o in objects.items:
            pod = MySQLPod(o)
            if self.owns_pod(pod):
                pods.append(pod)
        pods.sort(key=lambda pod: pod.index)
        return pods

    def get_service(self) -> typing.Optional[api_client.V1Service]:
        try:
            return cast(api_client.V1Service,
                        api_core.read_namespaced_service(self.name+"-instances", self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_stateful_set(self) -> typing.Optional[api_client.V1StatefulSet]:
        try:
            return cast(api_client.V1StatefulSet,
                        api_apps.read_namespaced_stateful_set(self.name, self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_router_service(self) -> typing.Optional[api_client.V1Service]:
        try:
            return cast(api_client.V1Service,
                        api_core.read_namespaced_service(self.name, self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_router_replica_set(self) -> typing.Optional[api_client.V1ReplicaSet]:
        try:
            return cast(api_client.V1ReplicaSet,
                        api_apps.read_namespaced_replica_set(self.name+"-router", self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_router_account(self) -> Tuple[str, str]:
        secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
            f"{self.name}-router", self.namespace))

        return utils.b64decode(secret.data["routerUsername"]), utils.b64decode(secret.data["routerPassword"])

    def get_backup_account(self) -> Tuple[str, str]:
        secret = cast(api_client.V1Secret,
                      api_core.read_namespaced_secret(
                          f"{self.name}-backup", self.namespace))

        return utils.b64decode(secret.data["backupUsername"]), utils.b64decode(secret.data["backupPassword"])

    def get_private_secrets(self) -> api_client.V1Secret:
        return cast(api_client.V1Secret,
                    api_core.read_namespaced_secret(f"{self.name}-privsecrets", self.namespace))

    def get_user_secrets(self) -> typing.Optional[api_client.V1Secret]:
        name = self.spec.get("secretName")
        try:
            return cast(api_client.V1Secret,
                        api_core.read_namespaced_secret(f"{name}", self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_admin_account(self) -> Tuple[str, str]:
        secrets = self.get_private_secrets()

        return (utils.b64decode(secrets.data["clusterAdminUsername"]),
                utils.b64decode(secrets.data["clusterAdminPassword"]))

    def get_initconf(self) -> typing.Optional[api_client.V1ConfigMap]:
        try:
            return cast(api_client.V1ConfigMap,
                        api_core.read_namespaced_config_map(f"{self.name}-initconf", self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_initmysql(self) -> typing.Optional[api_client.V1ConfigMap]:
        try:
            return cast(api_client.V1ConfigMap,
                        api_core.read_namespaced_config_map(f"{self.name}-initmysql", self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def _get_status_field(self, field: str) -> typing.Any:
        return cast(str, self.status.get(field))

    def _set_status_field(self, field: str, value: typing.Any) -> None:
        obj = self._get(self.namespace, self.name)

        if "status" not in obj:
            patch = {"status": {}}
        else:
            patch = {"status": obj["status"]}
        patch["status"][field] = value
        self.obj = self._patch(self.namespace, self.name, patch)

    def set_cluster_status(self, cluster_status) -> None:
        self._set_status_field("cluster", cluster_status)

    def get_cluster_status(self, field=None):  # TODO -> dict, remove field
        status = self._get_status_field("cluster")
        if status and field:
            return status.get(field)
        return status

    def set_status(self, status) -> None:
        obj = self._get(self.namespace, self.name)

        if "status" not in obj:
            obj["status"] = status
        else:
            obj["status"] = utils.merge_patch_object(obj["status"], status)
        self.obj = self._patch(self.namespace, self.name, obj)

    def update_cluster_info(self, info: dict) -> None:
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
        self.obj = self._patch(self.namespace, self.name, patch)

    # TODO remove field
    def get_cluster_info(self, field: typing.Optional[str] = None) -> typing.Optional[dict]:
        if self.metadata["annotations"]:
            info = self.metadata["annotations"].get(
                "mysql.oracle.com/cluster-info", None)
            if info:
                info = json.loads(info)
                if field:
                    return info.get(field)
                return info
        return None

    def set_create_time(self, time: datetime.datetime) -> None:
        self._set_status_field("createTime", time.replace(
            microsecond=0).isoformat()+"Z")

    def get_create_time(self) -> datetime.datetime:
        dt = self._get_status_field("createTime").rstrip("Z")
        return datetime.datetime.fromisoformat(dt)

    @property
    def ready(self) -> bool:
        return cast(bool, self.get_create_time())

    def set_last_known_quorum(self, members):
        # TODO
        pass

    def get_last_known_quorum(self):
        # TODO
        return None

    def incremental_recovery_allowed(self) -> typing.Optional[bool]:
        return cast(bool,
                    self.get_cluster_info().get("incrementalRecoveryAllowed"))

    def _add_finalizer(self, fin: str) -> None:
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
        self.obj = self._patch(self.namespace, self.name, patch)

    def _remove_finalizer(self, fin: str) -> None:
        # TODO strategic merge patch not working here??
        #patch = { "metadata": { "$deleteFromPrimitiveList/finalizers": [fin] }}
        patch = {"metadata": {"finalizers": [
            f for f in self.metadata["finalizers"] if f != fin]}}

        self.obj = self._patch(self.namespace, self.name, patch)

    def add_cluster_finalizer(self) -> None:
        self._add_finalizer("mysql.oracle.com/cluster")

    def remove_cluster_finalizer(self, cluster_body: dict = None) -> None:
        self._remove_finalizer("mysql.oracle.com/cluster")
        if cluster_body:
            # modify the JSON data used internally by kopf to update its finalizer list
            cluster_body["metadata"]["finalizers"].remove(
                "mysql.oracle.com/cluster")

    def set_current_version(self, version: str) -> None:
        v = self.status.get("version")
        if v != version:
            patch = {"status": {"version": version}}

            # TODO store the current server/router version + timestamp
            # store previous versions in a version history log
            self.obj = self._patch(self.namespace, self.name, patch)

    # TODO store last known majority and use it for diagnostics when there are
    # unconnectable pods


def get_all_clusters() -> typing.List[InnoDBCluster]:
    objects = cast(dict, api_customobj.list_cluster_custom_object(
        consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL))

    return [InnoDBCluster(o) for o in objects["items"]]


class MySQLPod:
    def __init__(self, pod: client.V1Pod):
        self.pod: client.V1Pod = pod

        self.port = 3306
        self.xport = 33060

        self.admin_account = None

    @overload
    @classmethod
    def from_json(cls, pod: str) -> 'MySQLPod':
        ...

    @overload
    @classmethod
    def from_json(cls, pod: dict) -> 'MySQLPod':
        ...

    @classmethod
    def from_json(cls, pod) -> 'MySQLPod':
        class Wrapper:
            def __init__(self, data):
                self.data = json.dumps(data)

        if not isinstance(pod, str):
            pod = eval(str(pod))

        return MySQLPod(cast(client.V1Pod, api_core.api_client.deserialize(
            Wrapper(pod), client.V1Pod)))

    def __str__(self) -> str:
        return self.name

    def __repr__(self) -> str:
        return f"<MySQLPod {self.name}>"

    @classmethod
    def read(cls, name: str, ns: str) -> 'MySQLPod':
        return MySQLPod(cast(client.V1Pod,
                             api_core.read_namespaced_pod(name, ns)))

    @property
    def metadata(self) -> api_client.V1ObjectMeta:
        return cast(api_client.V1ObjectMeta, self.pod.metadata)

    @property
    def status(self) -> api_client.V1PodStatus:
        return cast(api_client.V1PodStatus, self.pod.status)

    @property
    def phase(self) -> str:
        return cast(str, self.status.phase)

    @property
    def deleting(self) -> bool:
        return self.metadata.deletion_timestamp is not None

    @property
    def spec(self) -> api_client.V1PodSpec:
        return cast(api_client.V1PodSpec, self.pod.spec)

    @property
    def name(self) -> str:
        return cast(str, self.metadata.name)

    @property
    def index(self) -> int:
        return int(self.name.rpartition("-")[-1])

    @property
    def namespace(self) -> str:
        return cast(str, self.metadata.namespace)

    @property
    def cluster_name(self) -> str:
        return self.name.rpartition("-")[0]

    @property
    def address(self) -> str:
        return self.name+"."+cast(str, self.spec.subdomain)

    @property
    def address_fqdn(self) -> str:
        return self.name+"."+cast(str, self.spec.subdomain)+"."+self.namespace+".svc.cluster.local"

    @property
    def endpoint(self) -> str:
        return self.address_fqdn + ":" + str(self.port)

    @property
    def xendpoint(self) -> str:
        return self.address_fqdn + ":" + str(self.xport)

    @property
    def endpoint_co(self) -> dict:
        if not self.admin_account:
            self.admin_account = self.get_cluster().get_admin_account()

        return {"scheme": "mysql",
                "user": self.admin_account[0],
                "password": self.admin_account[1],
                "host": self.address_fqdn,
                "port": self.port}

    @property
    def endpoint_url_safe(self) -> dict:
        if not self.admin_account:
            self.admin_account = self.get_cluster().get_admin_account()

        return {"scheme": "mysql",
                "user": self.admin_account[0],
                "password": "****",
                "host": self.address_fqdn,
                "port": self.port}

    @property
    def xendpoint_co(self) -> dict:
        if not self.admin_account:
            self.admin_account = self.get_cluster().get_admin_account()

        return {"scheme": "mysqlx",
                "user": self.admin_account[0],
                "password": self.admin_account[1],
                "host": self.address_fqdn,
                "port": self.xport}

    def reload(self) -> None:
        self.pod = cast(api_client.V1Pod, api_core.read_namespaced_pod(
            self.name, self.namespace))

    def owner_reference(self, api_version, kind) -> typing.Optional[api_client.V1OwnerReference]:
        for owner in self.metadata.owner_references:
            if owner.api_version == api_version and owner.kind == kind:
                return owner

        return None

    def get_cluster(self) -> typing.Optional[InnoDBCluster]:
        try:
            return InnoDBCluster.read(self.namespace, self.cluster_name)
        except ApiException as e:
            print(
                f"Could not get cluster {self.namespace}/{self.cluster_name}: {e}")
            if e.status == 404:
                return None
            raise

    def check_condition(self, cond_type: str) -> typing.Optional[bool]:
        if self.status and self.status.conditions:
            for c in self.status.conditions:
                if c.type == cond_type:
                    return c.status == "True"

        return None

    def check_containers_ready(self) -> typing.Optional[bool]:
        return self.check_condition("ContainersReady")

    def check_container_ready(self, container_name: str) -> typing.Optional[bool]:
        if self.status.container_statuses:
            for cs in self.status.container_statuses:
                if cs.name == container_name:
                    return cs.ready
        return None

    def get_container_restarts(self, container_name: str) -> typing.Optional[int]:
        if self.status.container_statuses:
            for cs in self.status.container_statuses:
                if cs.name == container_name:
                    return cs.restart_count
        return None

    def get_member_readiness_gate(self, gate: str) -> typing.Optional[bool]:
        return self.check_condition(f"mysql.oracle.com/{gate}")

    def update_member_readiness_gate(self, gate: str, value: bool) -> None:
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

        self.pod = cast(api_client.V1Pod, api_core.patch_namespaced_pod_status(
            self.name, self.namespace, body=patch))

    # TODO remove field
    def get_membership_info(self, field: str = None) -> typing.Optional[dict]:
        if self.metadata.annotations:
            info = self.metadata.annotations.get(
                "mysql.oracle.com/membership-info", None)
            if info:
                info = json.loads(info)
                if info and field:
                    return info.get(field)
                return info
        return None

    def update_membership_status(self, member_id: str, role: str, status: str,
                                 view_id: str, version: str,
                                 joined: bool = False) -> None:
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
        self.pod = cast(api_client.V1Pod, api_core.patch_namespaced_pod(
            self.name, self.namespace, patch))

    def add_member_finalizer(self) -> None:
        self._add_finalizer("mysql.oracle.com/membership")

    def remove_member_finalizer(self, pod_body: dict = None) -> None:
        self._remove_finalizer("mysql.oracle.com/membership", pod_body)

    def _add_finalizer(self, fin: str) -> None:
        """
        Add the named token to the list of finalizers for the Pod.
        The Pod will be blocked from deletion until that token is
        removed from the list (remove_finalizer).
        """
        patch = {"metadata": {"finalizers": [fin]}}
        self.obj = api_core.patch_namespaced_pod(
            self.name, self.namespace, body=patch)

    def _remove_finalizer(self, fin: str, pod_body: dict = None) -> None:
        patch = {"metadata": {"$deleteFromPrimitiveList/finalizers": [fin]}}
        self.obj = api_core.patch_namespaced_pod(
            self.name, self.namespace, body=patch)

        if pod_body:
            # modify the JSON data used internally by kopf to update its finalizer list
            if fin in pod_body["metadata"]["finalizers"]:
                pod_body["metadata"]["finalizers"].remove(fin)
