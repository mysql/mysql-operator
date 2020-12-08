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

from .. import consts
from ..api_utils import dget_dict, dget_str, dget_int, dget_bool, dget_list, ApiSpecError
from ..kubeutils import api_core, api_apps, api_customobj, ApiException
from ..storage_api import StorageSpec
from typing import Optional, cast


class Snapshot:
    storage: Optional[StorageSpec] = None

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        self.storage.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str) -> None:
        storage = dget_dict(spec, "storage", prefix)
        self.storage = StorageSpec(
            ["ociObjectStorage", "persistentVolumeClaim"])
        self.storage.parse(storage, prefix+".storage")


class DumpInstance:
    dumpOptions: dict = {}  # dict with options for dumpInstance()
    storage: Optional[StorageSpec] = None  # StorageSpec

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        self.storage.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str) -> None:
        self.options = dget_dict(spec, "dumpOptions", prefix, {})

        storage = dget_dict(spec, "storage", prefix)
        self.storage = StorageSpec()
        self.storage.parse(storage, prefix+".storage")


class BackupProfile:
    name: str = ""
    dumpInstance: Optional[DumpInstance] = None
    snapshot: Optional[Snapshot] = None

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        if self.snapshot:
            return self.snapshot.add_to_pod_spec(pod_spec, container_name)
        if self.dumpInstance:
            return self.dumpInstance.add_to_pod_spec(pod_spec, container_name)
        assert 0

    def parse(self, spec: dict, prefix: str) -> None:
        self.name = dget_str(spec, "name", prefix)
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


class BackupSchedule:
    name: str = ""
    backupProfileName: str = ""
    schedule = None


class MySQLBackupSpec:
    clusterName: str = ""
    backupProfileName: str = ""
    backupProfile = None
    deleteBackupData: bool = False

    def __init__(self, namespace: str, name: str, spec: dict):
        self.namespace = namespace
        self.name = name
        self.parse(spec)

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        return self.backupProfile.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict) -> Optional[ApiSpecError]:
        self.clusterName = dget_str(spec, "clusterName", "spec")
        self.backupProfileName = dget_str(
            spec, "backupProfileName", "spec", "")
        self.backupProfile = self.parse_backup_profile(
            dget_dict(spec, "backupProfile", "spec", {}), "spec.backupProfile")
        self.deleteBackupData = dget_bool(
            spec, "deleteBackupData", "spec", False)

        if self.backupProfileName and self.backupProfile:
            raise ApiSpecError(
                f"Only one of spec.backupProfileName or spec.backupProfile must be set")
        if not self.backupProfileName and not self.backupProfile:
            raise ApiSpecError(
                f"One of spec.backupProfileName or spec.backupProfile must be set")

        try:
            from ..innodbcluster.cluster_api import InnoDBCluster

            cluster = InnoDBCluster.read(self.clusterName, self.namespace)
        except ApiException as e:
            if e.status == 404:
                return ApiSpecError(f"Invalid clusterName {self.namespace}/{self.clusterName}")
            raise

        if self.backupProfileName:
            self.backupProfile = cluster.parsed_spec.get_backup_profile(
                self.backupProfileName)
            if not self.backupProfile:
                return ApiSpecError(f"Invalid backupProfileName '{self.backupProfileName}' in cluster {self.namespace}/{self.clusterName}")

    def parse_backup_profile(self, profile: dict, prefix: str) -> Optional[BackupProfile]:
        # TODO
        if profile:
            p = BackupProfile()
            p.parse(profile, prefix)
            return p
        return None


class MySQLBackup:
    def __init__(self, backup: dict):
        self.obj: dict = backup

        self.parsed_spec = MySQLBackupSpec(
            self.namespace, self.name, self.spec)

    def __str__(self) -> str:
        return f"{self.namespace}/{self.name}"

    def __repr__(self) -> str:
        return f"<MySQLBackup {self.name}>"

    @classmethod
    def read(cls, name: str, namespace: str) -> 'MySQLBackup':
        return MySQLBackup(cast(dict, api_customobj.get_namespaced_custom_object(
            consts.GROUP, consts.VERSION, namespace, consts.MYSQLBACKUP_PLURAL, name)))

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

    def get_profile(self):
        pass

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
