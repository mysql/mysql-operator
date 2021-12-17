# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
from os import execl
from typing import Optional, cast
from .. import consts
from .. api_utils import dget_dict, dget_str, dget_int, dget_bool, dget_list, ApiSpecError
from .. kubeutils import api_core, api_apps, api_customobj, ApiException
from .. storage_api import StorageSpec
from .. innodbcluster import cluster_api


class Snapshot:
    def __init__(self):
        self.storage: Optional[StorageSpec] = None

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        self.storage.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str) -> None:
        storage = dget_dict(spec, "storage", prefix)
        self.storage = StorageSpec(
            ["ociObjectStorage", "persistentVolumeClaim"])
        self.storage.parse(storage, prefix+".storage")

    def __eq__(self, other : 'Snapshot') -> bool:
        assert isinstance(other, Snapshot)
        return (self.storage == other.storage)


class DumpInstance:
    def __init__(self):
        self.dumpOptions: dict = {}  # dict with options for dumpInstance()
        self.storage: Optional[StorageSpec] = None  # StorageSpec
        self.options = {}

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        self.storage.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str) -> None:
        self.options = dget_dict(spec, "dumpOptions", prefix, {})

        storage = dget_dict(spec, "storage", prefix)
        self.storage = StorageSpec()
        self.storage.parse(storage, prefix+".storage")

    def __eq__(self, other : 'DumpInstance') -> bool:
        assert isinstance(other, DumpInstance)
        return (self.dumpOptions == other.dumpOptions and \
                self.storage == other.storage)


class BackupProfile:
    def __init__(self):
        self.name: str = ""
        self.dumpInstance: Optional[DumpInstance] = None
        self.snapshot: Optional[Snapshot] = None

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        assert self.snapshot or self.dumpInstance
        if self.snapshot:
            return self.snapshot.add_to_pod_spec(pod_spec, container_name)
        if self.dumpInstance:
            return self.dumpInstance.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str, name_required: bool = True) -> None:
        self.name = dget_str(spec, "name", prefix, default_value= None if name_required else "")
        prefix += "." + self.name
        method_spec = dget_dict(spec, "dumpInstance", prefix, {})
        if method_spec:
            self.dumpInstance = DumpInstance()
            self.dumpInstance.parse(method_spec, prefix+".dumpInstance")
        method_spec = dget_dict(spec, "snapshot", prefix, {})
        if method_spec:
            self.snapshot = Snapshot()
            self.snapshot.parse(method_spec, prefix+".snapshot")

        if self.dumpInstance and self.snapshot:
            raise ApiSpecError(
                f"Only one of dumpInstance or snapshot may be set in {prefix}")

        if not self.dumpInstance and not self.snapshot:
            raise ApiSpecError(
                f"One of dumpInstance or snapshot must be set in a {prefix}")

    def __eq__(self, other: 'BackupProfile') -> bool:
        assert isinstance(other, BackupProfile)
        return (self.name == other.name and \
                self.dumpInstance == other.dumpInstance and \
                self.snapshot == other.snapshot)


class BackupSchedule:
    def __init__(self, cluster_spec):
        self.cluster_spec: cluster_api.InnoDBClusterSpec = cluster_spec

        self.name: str = ""
        self.backupProfileName: Optional[str] = None
        self.backupProfile: Optional[BackupProfile] = None
        self.schedule: str = ""
        self.enabled: bool = False
        self.deleteBackupData: bool = False # unused

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        assert self.backupProfile
        if self.backupProfile:
            return self.backupProfile.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str) -> None:
        self.name = dget_str(spec, "name", prefix, default_value= "")

        self.deleteBackupData = dget_bool(spec, "deleteBackupData", prefix, default_value=False)

        self.enabled = dget_bool(spec, "enabled", prefix, default_value=False)

        self.backupProfileName = dget_str(spec, "backupProfileName", prefix, default_value= "")

        self.schedule = dget_str(spec, "schedule", prefix)
        if not self.schedule:
            raise ApiSpecError(f"schedule not set in in a {prefix}")

        backup_profile = dget_dict(spec, "backupProfile", prefix, {})
        if self.backupProfileName and backup_profile:
            print(f"Only one of backupProfileName or backupProfile must be set in {prefix}")
            raise ApiSpecError(f"Only one of backupProfileName or backupProfile must be set in {prefix}")

        if not self.backupProfileName and not backup_profile:
            print(f"One of backupProfileName or backupProfile must be set in {prefix}")
            raise ApiSpecError(f"One of backupProfileName or backupProfile must be set in {prefix}")

        if backup_profile:
            self.backupProfile = BackupProfile()
            self.backupProfile.parse(backup_profile, prefix + ".backupProfile", name_required= False)
        else:
            self.backupProfile = self.cluster_spec.get_backup_profile(self.backupProfileName)

            if not self.backupProfile:
                print(f"Invalid backupProfileName '{self.backupProfileName}' in cluster {self.cluster_spec.namespace}/{self.cluster_spec.clusterName}")
                raise ApiSpecError(f"Invalid backupProfileName '{self.backupProfileName}' in cluster {self.cluster_spec.namespace}/{self.cluster_spec.clusterName}")

    def __eq__(self, other : 'BackupSchedule') -> bool:
        assert isinstance(other, BackupSchedule)
        eq = (self.cluster_spec.namespace == other.cluster_spec.namespace and \
                self.cluster_spec.name == other.cluster_spec.name and \
                self.name == other.name and \
                self.backupProfileName == other.backupProfileName and \
                self.backupProfile == other.backupProfile and \
                self.schedule == other.schedule and \
                self.deleteBackupData == other.deleteBackupData and \
                self.enabled == other.enabled)
        return eq


class MySQLBackupSpec:
    def __init__(self, namespace: str, name: str, spec: dict):
        self.namespace = namespace
        self.name = name

        self.clusterName: str = ""
        self.backupProfileName: str = ""
        self.backupProfile = None
        self.deleteBackupData: bool = False # unused
        self.addTimestampToBackupDirectory: bool = True
        self.operator_image: str = ""
        self.operator_image_pull_policy: str = ""
        self.image_pull_secrets: Optional[str] = None
        self.service_account_name: Optional[str] = None
        self.parse(spec)

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        assert self.backupProfile
        return self.backupProfile.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict) -> Optional[ApiSpecError]:
        self.clusterName = dget_str(spec, "clusterName", "spec")
        self.backupProfileName = dget_str(spec, "backupProfileName", "spec", default_value="")
        self.backupProfile = self.parse_backup_profile(dget_dict(spec, "backupProfile", "spec", {}), "spec.backupProfile")
        self.deleteBackupData = dget_bool(spec, "deleteBackupData", "spec", default_value=False)
        self.addTimestampToBackupDirectory = dget_bool(spec, "addTimestampToBackupDirectory", "spec", default_value=True)

        if self.backupProfileName and self.backupProfile:
            raise ApiSpecError("Only one of spec.backupProfileName or spec.backupProfile must be set")
        if not self.backupProfileName and not self.backupProfile:
            raise ApiSpecError("One of spec.backupProfileName or spec.backupProfile must be set")

        try:
            cluster = cluster_api.InnoDBCluster.read(self.namespace, self.clusterName)
        except ApiException as e:
            if e.status == 404:
                return ApiSpecError(f"Invalid clusterName {self.namespace}/{self.clusterName}")
            raise

        self.operator_image = cluster.parsed_spec.operator_image
        self.operator_image_pull_policy = cluster.parsed_spec.operator_image_pull_policy
        self.image_pull_secrets = cluster.parsed_spec.image_pull_secrets
        self.service_account_name = cluster.parsed_spec.service_account_name

        if self.backupProfileName:
            self.backupProfile = cluster.parsed_spec.get_backup_profile(self.backupProfileName)
            if not self.backupProfile:
                err_msg = f"Invalid backupProfileName '{self.backupProfileName}' in cluster {self.namespace}/{self.clusterName}"
                raise ApiSpecError(err_msg)

        return None

    def parse_backup_profile(self, profile: dict, prefix: str) -> Optional[BackupProfile]:
        if profile:
            profile_object = BackupProfile()
            profile_object.parse(profile, prefix)
            return profile_object
        return None


class MySQLBackup:
    def __init__(self, backup: dict):
        self.obj: dict = backup

        # self.namespace and self.name here will call the getters, which in turn will
        # look into self.obj['metadata']
        self.parsed_spec = MySQLBackupSpec(
            self.namespace, self.name, self.spec)

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"

    def __repr__(self) -> str:
        return f"<MySQLBackup {self.name}>"

    def get_cluster(self):
        try:
            cluster = cluster_api.InnoDBCluster.read(self.namespace, self.cluster_name)
        except ApiException as e:
            if e.status == 404:
                return ApiSpecError(f"Invalid clusterName {self.namespace}/{self.cluster_name}")
            raise
        return cluster

    @classmethod
    def read(cls, name: str, namespace: str) -> 'MySQLBackup':
        return MySQLBackup(cast(dict, api_customobj.get_namespaced_custom_object(
            consts.GROUP, consts.VERSION, namespace, consts.MYSQLBACKUP_PLURAL, name)))

    @classmethod
    def create(cls, namespace: str, body: dict) -> Optional[dict]:
        try:
            return cast(dict, api_customobj.create_namespaced_custom_object(
                consts.GROUP, consts.VERSION, namespace, consts.MYSQLBACKUP_PLURAL, body))
        except ApiException as exc:
            print(f"Exception {exc} when calling create_namespaced_custom_object({consts.GROUP}, {consts.VERSION}, {namespace}, {consts.MYSQLBACKUP_PLURAL} body={body}")
            return None
        assert 0 # "Uncaught exception/wrong code flow"

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
    def cluster_name(self) -> str:
        return self.parsed_spec.clusterName

    def get_profile(self) -> BackupProfile:
        if self.parsed_spec.backupProfile:
            return self.parsed_spec.backupProfile

        cluster = self.get_cluster()
        profile = cluster.parsed_spec.get_backup_profile(self.parsed_spec.backupProfileName)
        if not profile:
            raise Exception(
                f"Unknown backup profile {self.parsed_spec.backupProfileName} in cluster {self.namespace}/{self.parsed_spec.clusterName}")

        return profile

    def set_started(self, backup_name: str, start_time: str) -> None:
        patch = {"status": {
            "status": "Running",
            "startTime": start_time,
            "output": backup_name
        }}
        self.obj = cast(dict, api_customobj.patch_namespaced_custom_object_status(
            consts.GROUP, consts.VERSION, self.namespace, consts.MYSQLBACKUP_PLURAL, self.name, body=patch))

    def set_succeeded(self, backup_name: str, start_time: str, end_time: str, info: dict) -> None:
        import dateutil.parser as dtp

        elapsed = dtp.isoparse(end_time) - dtp.isoparse(start_time)
        hours, seconds = divmod(elapsed.total_seconds(), 3600)
        minutes, seconds = divmod(seconds, 60)

        patch = {"status": {
            "status": "Completed",
            "startTime": start_time,
            "completionTime": end_time,
            "elapsedTime": f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}",
            "output": backup_name
        }}
        patch["status"].update(info)
        self.obj = cast(dict, api_customobj.patch_namespaced_custom_object_status(
            consts.GROUP, consts.VERSION, self.namespace, consts.MYSQLBACKUP_PLURAL, self.name, body=patch))

    def set_failed(self, backup_name: str, start_time: str, end_time: str, error: Exception) -> None:
        import dateutil.parser as dtp

        elapsed = dtp.isoparse(end_time) - dtp.isoparse(start_time)
        hours, seconds = divmod(elapsed.total_seconds(), 3600)
        minutes, seconds = divmod(seconds, 60)

        patch = {"status": {
            "status": "Error",
            "startTime": start_time,
            "completionTime": end_time,
            "elapsedTime": f"{int(hours):02}:{int(minutes):02}:{int(seconds):02}",
            "message": str(error),
            "output": backup_name
        }}
        self.obj = cast(dict, api_customobj.patch_namespaced_custom_object_status(
            consts.GROUP, consts.VERSION, self.namespace, consts.MYSQLBACKUP_PLURAL, self.name, body=patch))
