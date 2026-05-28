# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from abc import ABC, abstractmethod
from enum import Enum
from pathlib import PurePosixPath
import typing, abc
from typing import Optional, Union, List, Tuple, Dict, Callable, Any, cast, overload

import kopf
from kopf._cogs.structs.bodies import Body
from logging import getLogger, Logger

from .. import fqdn
from ..finalizers import remove_finalizer_from_body, remove_finalizer_with_json_patch
from ..k8sobject import K8sInterfaceObject
from .. import utils, config, consts
from ..backup.backup_api import BackupProfile, BackupSchedule
from ..backup.meb.meb_options import RESTORE_EXTRA_OPTIONS, MebOptionError, validate_extra_options
from ..storage_api import StorageSpec
from ..api_utils import Edition, dget_bool, dget_dict, dget_enum, dget_str, dget_int, dget_float, dget_list, ApiSpecError, ImagePullPolicy
from ..kubeutils import api_core, api_apps, api_customobj, api_policy, api_rbac, api_batch, api_cron_job
from ..kubeutils import client as api_client, ApiException
from ..kubeutils import k8s_cluster_domain
from .logs.logs_api import LogsSpec
from .logs.logs_types_api import ConfigMapMountBase, get_object_name, patch_sts_spec_template_complex_attribute, patch_container_attribute
from .logs.logs_collector_fluentd_api import get_container_name
import json
import yaml
import datetime
from cryptography import x509
from kubernetes import client


AddToInitconfHandler = Callable[[dict, str, Logger], None]
RemoveFromStsHandler = Callable[[Union[dict, api_client.V1StatefulSet], Logger], None]
AddToStsHandler = Callable[[Union[dict, 'InnoDBClusterObjectModifier', api_client.V1StatefulSet], Logger], None]
GetConfigMapHandler = Callable[[str, Logger], Optional[list[tuple[str, Optional[dict]]]]]
GetSecretsHandler   = Callable[[str, Logger], Optional[list[tuple[str, Optional[dict]]]]]
AddToSvcHandler = Callable[[Union[dict, api_client.V1Service], Logger], None]
GetSvcMonitorHandler = Callable[[Logger], tuple[str, Optional[dict]]]

MAX_CLUSTER_NAME_LEN = 28

def escape_value_for_mycnf(value: str) -> str:
    return '"'+value.replace("\\", "\\\\").replace("\"", "\\\"")+'"'

class StsModifier:
    def __init__(self, name: str):
        self.name = name
        self.initcontainers: List[Union[dict, api_client.V1Container]] = []
        self.containers: List[Union[dict, api_client.V1Container]] = []
        self.volumes: List[Union[dict, api_client.V1Volume]] = []
        self.volume_mounts:List[Union[dict, api_client.V1Container]] = []

    def add_initcontainer(self, container: dict):
        """Add an init container to the StatefulSet.

        Args:
            container: A dictionary defining the init container configuration with the following
                structure:
                {
                    "name": str,                 # Init container name
                    "image": str,                # Container image
                    "command": list[str],        # Command to execute
                    "env": list,                 # Environment variables
                    "volumeMounts": list[dict],  # Volume mounts specific to this init container (optional)
                    "resources": dict            # Resource limits and requests (optional)
                    ...
                }
                Each volume mount in "volumeMounts" (if included) contains:
                    {
                        "name": str,  # Volume mount name
                        "mountPath": str,  # Path where the volume is mounted
                        "subPath": str  # Sub-path within the volume
                    }
                The init container may include its own volume mounts in the "volumeMounts" field.
                However, if a volume is to be mounted across multiple containers (init or main),
                it should be defined using `add_volume_mount()` instead of here. Init containers are
                added to the StatefulSet before volume mounts from `add_volume_mount()` are processed.

        Example:
            ```python
            {
                "name": "....",
                "image": "...",
                "command": ["/bin/sh", "-c", "mysql --initialize"],
                "env": [],
                "volumeMounts": [],
            }
            ```
        """
        self.initcontainers.append(container)

    def add_container(self, container: dict):
        """Add an container to the StatefulSet.

        Args:
            container: A dictionary defining container configuration with the following
                structure:
                {
                    "name": str,                 # Init container name
                    "image": str,                # Container image
                    "command": list[str],        # Command to execute
                    "env": list,                 # Environment variables
                    "volumeMounts": list[dict],  # Volume mounts specific to this init container (optional)
                    "resources": dict            # Resource limits and requests (optional)
                    ...
                }
                Each volume mount in "volumeMounts" (if included) contains:
                    {
                        "name": str,  # Volume mount name
                        "mountPath": str,  # Path where the volume is mounted
                        "subPath": str  # Sub-path within the volume
                    }
                The init container may include its own volume mounts in the "volumeMounts" field.
                However, if a volume is to be mounted across multiple containers (init or main),
                it should be defined using `add_volume_mount()` instead of here. Init containers are
                added to the StatefulSet before volume mounts from `add_volume_mount()` are processed.

        Example:
            ```python
            {
                "name": "....",
                "image": "...",
                "command": ["/bin/sh", "-c", "mysql --initialize"],
                "env": [],
                "volumeMounts": [],
            }
            ```
        """
        self.containers.append(container)

    def add_volume(self, volume: dict):
        self.volumes.append(volume)

    def add_volume_mount(self, volume_mount: dict):
        """Add a volume mount to the list of volume mounts.

        Args:
            volume_mount: A dictionary containing volume mount configuration with the following structure:
                {
                    "initContainers": list[str],  # Names of init containers using the volume mount
                    "containers": list[str],      # Names of main containers using the volume mount
                    "mounts": list[dict]          # Volume mounts details
                }
                Each mount in "mounts" includes:
                    {
                        "name": str,      # Volume mount name
                        "mountPath": str, # Path where the volume is mounted
                        "subPath": str    # Sub-path within the volume
                    }
            Volume mounts can be specified either within the "mounts" field of this configuration
            or separately via other mechanisms (e.g., add_container or add_initcontainer).
        Example:
            ```python
            {
                "initContainers": ["initmysql", "sleepyinit"],
                "containers": [],
                "mounts": [
                    {
                        "name": "myvolume",
                        "mountPath": "/path/to/filexyz",
                        "subPath": "filexyz"
                    }
                ]
            }
            ```
        """
        self.volume_mounts.append(volume_mount)

    def _modify_sts_xcontainer_volume_mounts_spec(self, sts: Union[dict, api_client.V1StatefulSet],
                                       patcher: 'InnoDBClusterObjectModifier',
                                       volume_mounts_containers: List[str],
                                       volume_mounts: List[Union[dict, api_client.V1VolumeMount]],
                                       init: bool,
                                       add: bool,
                                       logger: Logger) -> None:
        """Adds (or removes based on `add`) volume_mounts in all containers found in volume_mounts_containers"""
        containers = []
        for vmc in volume_mounts_containers:
            containers.append(
                {
                    "name": vmc,
                    # volumeMounts or volume_mounts?
                    "volumeMounts": volume_mounts
                }
            )
        patch = {
            "initContainers" if init else "containers" : containers
        }

        patch_container_attribute(sts, patcher, patch, init, "volumeMounts", add, logger)

    def _modify_sts_volumes_spec(self,
                                 sts: Union[dict, api_client.V1StatefulSet],
                                 patcher: 'InnoDBClusterObjectModifier',
                                 volumes: List[Union[dict, api_client.V1Volume]],
                                 add: bool,
                                 logger: Logger) -> None:
        patch = {
            "volumes" : volumes
        }
        patch_sts_spec_template_complex_attribute(sts, patcher, patch, "volumes", add, logger)

    def _add_or_remove_sts_containers(self, sts: Union[dict, api_client.V1StatefulSet],
                                     patcher: 'InnoDBClusterObjectModifier',
                                     containers: List[Union[dict, api_client.V1Container]],
                                     init: bool,
                                     add: bool,
                                     logger: Logger) -> None:
        key = "initContainers" if init else "containers"
        patch = {
            key : containers
        }
        patch_sts_spec_template_complex_attribute(sts, patcher, patch, key, add, logger)

    def modify_sts_spec(self,
                        sts: Union[dict, api_client.V1StatefulSet],
                        patcher: 'InnoDBClusterObjectModifier',
                        add: bool,
                        logger: Logger) -> None:

        logger.info(f"{self.name} : will {'add' if add else 'remove'} containers={self.containers}")
        logger.info(f"{self.name} : will {'add' if add else 'remove'} volumes={self.volumes}")
        logger.info(f"{self.name} : will {'add' if add else 'remove'} volume_mounts={self.volume_mounts}")

        # First we add or remove the full-blown containers. Then we can attach additional volumeMounts to them, if they don't have them
        if self.containers is not None and len(self.containers):
            init = False
            self._add_or_remove_sts_containers(sts, patcher, self.containers, init, add, logger)

        if self.initcontainers is not None and len(self.initcontainers):
            init = True
            self._add_or_remove_sts_containers(sts, patcher, self.initcontainers, init, add, logger)

        if self.volumes is not None and len(self.volumes):
            self._modify_sts_volumes_spec(sts, patcher, self.volumes, add, logger)

        # our container exists already, this is why we modify volume_mounts directly
        if self.volume_mounts is not None and len(self.volume_mounts):
            for vm in self.volume_mounts:
                if "initContainers" in vm and len(vm["initContainers"]):
                    init = True
                    self._modify_sts_xcontainer_volume_mounts_spec(sts, patcher, vm["initContainers"], vm["mounts"], init, add, logger)
                if "containers" in vm and len(vm["containers"]):
                    init = False
                    self._modify_sts_xcontainer_volume_mounts_spec(sts, patcher, vm["containers"], vm["mounts"], init, add, logger)


    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        def cb(sts: Union[dict,api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
            add = True
            self.modify_sts_spec(sts, patcher, add, logger)
        return cb

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        def cb(sts: Union[dict,api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
            add = False
            self.modify_sts_spec(sts, patcher, add, logger)
        return cb


class SecretData:
    secret_name: Optional[str] = None
    key: Optional[str] = None

class MetricsConfigMap(ConfigMapMountBase):
    def __init__(self):
        super().__init__("metrics", "metrics.cnf", "/tmp/metrics")

    def parse(self, spec: dict, prefix: str) -> None:
        pass

    def validate(self) -> None:
        pass


class MetriscSpec:
    enable: bool = False
    image: str = ""
    options: Optional[list] = None
    web_config: Optional[str] = None
    tls_secret: Optional[str] = None
    dbuser_name: str = "mysqlmetrics"
    dbuser_grants: list = ['PROCESS', 'REPLICATION CLIENT', 'SELECT']
    dbuser_max_connections: int = 3
    monitor: bool = False
    monitor_spec: dict = {}
    port = 9104
    config: MetricsConfigMap = MetricsConfigMap()
    container_name = "metrics"
    svc_port_name = "metrics"

    def __init__(self, namespace: str, cluster_name: str):
        self.namespace = namespace
        self.cluster_name = cluster_name
        self.cm_name = f"{self.cluster_name}-metricsconf"

    def parse(self, spec: dict, prefix: str) -> None:
        self.enable = dget_bool(spec, "enable", prefix)
        self.image = dget_str(spec, "image", prefix)
        self.options = dget_list(spec, "options", prefix, default_value=[],
                                 content_type=str)

        self.web_config = dget_str(spec, "webConfig", prefix, default_value="")
        self.tls_secret = dget_str(spec, "tlsSecret", prefix, default_value="")

        self.monitor = dget_bool(spec, "monitor", prefix,
                                 default_value=False)

        self.monitor_spec = dget_dict(spec, "monitorSpec", prefix,
                                      default_value={})

        user_spec = dget_dict(spec, "dbUser", prefix, default_value={})
        user_prefix = prefix+".dbUser"
        self.dbuser_name = dget_str(user_spec, "name", user_prefix,
                                    default_value="mysqlmetrics")
        self.dbuser_grants = dget_list(user_spec, "options", user_prefix,
                                       default_value=['PROCESS', 'REPLICATION CLIENT', 'SELECT'],
                                       content_type=str)
        self.dbuser_max_connections = dget_int(user_spec, "maxConnections",
                                               user_prefix, default_value=3)

        self.options.append("--config.my-cnf=/tmp/metrics/metrics.cnf") # see __init__

    def validate(self) -> None:
        pass

    def _add_container_to_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', add: bool, logger: Logger) -> None:
        options = self.options
        if self.web_config:
            options += ["--web.config.file=/config/web.config"]

        mounts = [
            {
                "name": "rundir",
                "mountPath": "/var/run/mysqld",
            }
        ]
        if self.web_config:
            mounts.append(
                {
                    "name": f"{self.container_name}-web-config",
                    "mountPath" : "/config",
                    "readOnly": True,
                }
            )

        if self.tls_secret:
            mounts.append(
                {
                    "name": f"{self.container_name}-tls",
                    "mountPath" : "/tls",
                    "readOnly": True,
                }
            )

        patch = {
            "containers" : [
                {
                    "name": self.container_name,
                    "image": self.image,
                    "imagePullPolicy": "IfNotPresent", # TODO: should be self.sidecar_image_pull_policy
                    "args": options,
                    # These can't go to spec.template.spec.securityContext
                    # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
                    # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
                    # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
                    # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
                    "securityContext": {
                        "allowPrivilegeEscalation": False,
                        "privileged": False,
                        "readOnlyRootFilesystem": True,
                        "capabilities": {
                            "drop": ["ALL"]
                        },
                        # We must use same user id as auth_socket expects
                        "runAsUser": 2,
                        "runAsGroup": 27,
                    },
                    "env": [
                        # For BC with pre-0.15.0. 0.15.0+ will use the configuration file and skip the env totally
                        {
                            "name": "DATA_SOURCE_NAME",
                            "value": f"{self.dbuser_name}:@unix(/var/run/mysqld/mysql.sock)/"
                        }
                    ],
                    "ports": [
                        {
                            "name": self.container_name,
                            "containerPort": self.port,
                            "protocol": "TCP",
                        }
                    ],
                    "volumeMounts": mounts,
                }
            ]
        }

        patch_sts_spec_template_complex_attribute(sts, patcher, patch, "containers", add, logger)

    def _add_volumes_to_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', add: bool, logger: Logger) -> None:
        volumes = []
        # if there is web_config and we have to add it - add it
        # if there don't have to add, the patch won't be added but still be used to clean up from remnants of previous add
        if (self.web_config and add) or (not add):
            volumes.append(
                {
                    "name": f"{self.container_name}-web-config",
                    "configMap": {
                        "name" : self.web_config,
                        "defaultMode": 0o444,
                    }
                }
            )
        if (self.tls_secret and add) or (not add):
            volumes.append(
                {
                    "name": f"{self.container_name}-tls",
                    "secret": {
                        "name" : self.tls_secret,
                        "defaultMode": 0o444,
                    }
                }
            )

        patch = {
            "volumes" : volumes
        }

        patch_sts_spec_template_complex_attribute(sts, patcher, patch, "volumes", add, logger)

    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        def cb(sts: Union[dict, api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
            self._add_container_to_sts_spec(sts, patcher, self.enable, logger)
            self._add_volumes_to_sts_spec(sts, patcher, self.enable, logger)
            self.config.add_to_sts_spec(sts, patcher, self.container_name, self.cm_name, self.enable, logger)
        return cb

    def get_secrets_cb(self) -> Optional[GetSecretsHandler]:
        return None

    def get_configmaps_cb(self) -> Optional[GetConfigMapHandler]:
        def cb(prefix: str, logger: Logger) -> Optional[List[Tuple[str, Optional[Tuple[str, Optional[Dict]]]]]]:
            if not self.enable:
                return [(self.cm_name, None)]

            config = f"""[client]
user={self.dbuser_name}
socket=unix:///var/run/mysqld/mysql.sock
"""

            return [
                (
                    self.cm_name,
                    {
                        'apiVersion' : "v1",
                        'kind': 'ConfigMap',
                        'metadata': {
                            'name': self.cm_name,
                        },
                        'data' : {
                            self.config.config_file_name: config
                        }
                    }
                )
            ]

        return cb

    def get_add_to_svc_cb(self) -> Optional[AddToSvcHandler]:
        def cb(svc: Union[dict, api_client.V1Service], logger: Logger) -> None:
            patch = {
                "ports" : [
                    {
                        "name" : self.svc_port_name,
                        "port": self.port, # ToDo : should be cluster.mysql_metrics_port
                        "targetPort": self.port, # ToDo : should be cluster.mysql_metrics_port
                    }
                ]
            }

            svc_name = "n/a"
            my_port_names = [port["name"] for port in patch["ports"]]
            if isinstance(svc, dict):
                svc_name = svc["metadata"]["name"]
                # first filter out the old `port`
                svc["spec"]["ports"] = [port for port in svc["spec"]["ports"] if port and get_object_name(port) not in my_port_names]
                # Then add, if needed
                if self.enable:
                    utils.merge_patch_object(svc["spec"], patch)
            elif isinstance(svc, api_client.V1Service):
                svc_name = svc.metadata.name
                # first filter out the old `port`
                svc.spec.ports = [port for port in svc.spec.ports if port and get_object_name(port) not in my_port_names]
                # Then add, if needed
                if self.enable:
                    svc.spec.ports += patch["ports"]
            print(f"\t\t\t{'A' if self.enable else 'Not a'}dding port {self.svc_port_name} to Service {svc_name}")

        return cb

    # Can't include the type for the MonitoringCoreosComV1Api.ServiceMonitor
    def get_svc_monitor_cb(self) -> Optional[GetSvcMonitorHandler]:
        def cb(logger: Logger) -> dict:
            monitor = None
            requested = self.enable and self.monitor
            monitor_name = self.cluster_name

            if requested:
                monitor = f"""
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: {monitor_name}
spec:
  selector:
    matchLabels:
      mysql.oracle.com/cluster: {self.cluster_name}
      tier: mysql
  endpoints:
  - port: metrics
    path: /metrics
"""
                monitor = yaml.safe_load(monitor)

            return (monitor_name, monitor)

        return cb

class CloneInitDBSpec:
    uri: str = ""
    password_secret_name: Optional[str] = None
    root_user: Optional[str] = None

    def parse(self, spec: dict, prefix: str) -> None:
        self.uri = dget_str(spec, "donorUrl", prefix)
        self.root_user = dget_str(
            spec, "rootUser", prefix, default_value="root")
        key_ref = dget_dict(spec, "secretKeyRef", prefix)
        self.password_secret_name = dget_str(
            key_ref, "name", prefix+".secretKeyRef")

    def get_password(self, ns: str) -> str:
        secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
            self.password_secret_name, ns))

        return utils.b64decode(secret.data["rootPassword"])

class ClusterSetInitDBSpec:
    uri: str = ""
    password_secret_name: Optional[str] = None
    root_user: Optional[str] = None

    def parse(self, spec: dict, prefix: str) -> None:
        self.uri = dget_str(spec, "targetUrl", prefix)
        self.root_user = dget_str(
            spec, "rootUser", prefix, default_value="root")
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
    loadOptions: dict = {}

    def parse(self, spec: dict, prefix: str) -> None:
        # path can be "" if we're loading from a bucket
        self.path = dget_str(spec, "path", prefix, default_value="")

        self.storage = StorageSpec()
        self.storage.parse(
            dget_dict(spec, "storage", prefix), prefix+".storage")

        self.loadOptions = dget_dict(spec, "options", prefix, default_value={})

class MebInitDBSpec:
    s3_region: Optional[str] = None
    s3_bucket: Optional[str] = None
    s3_object_key_prefix: Optional[str] = None
    s3_credentials: Optional[str] = None
    s3_host: Optional[str] = None

    oci_credentials: Optional[str] = None

    full_backup: Optional[str] = None
    incremental_backups: Optional[List[str]] = []

    pitr_backup_file: Optional[str] = None
    pitr_binlog_name: Optional[str] = None
    pitr_gtid_purge: Optional[str] = None
    pitr_end_term: Optional[str] = None
    pitr_end_value: Optional[str] = None
    extra_options: Optional[List[str]] = []

    def parse(self, spec: dict, prefix: str) -> None:
        # TODO other storage types ....
        storagespec = dget_dict(spec, "storage", prefix)

        if "ociObjectStorage" in storagespec:
            ocistorage = dget_dict(storagespec, "ociObjectStorage", prefix+".storage")
            self.oci_credentials = dget_str(ocistorage, "credentials", prefix+".storage.ociObjectStorage")

        if "s3" in storagespec:
            s3storage = dget_dict(storagespec, "s3", prefix+".storage")
            self.s3_region = dget_str(s3storage, "region", prefix+".storage.s3")
            self.s3_bucket= dget_str(s3storage, "bucket", prefix+".storage.s3")
            self.s3_object_key_prefix = dget_str(s3storage, "objectKeyPrefix", prefix+".storage.s3")
            self.s3_credentials = dget_str(s3storage, "credentials", prefix+".storage.s3")
            self.s3_host = dget_str(s3storage, "host", prefix+".storage", default_value="")

        if (not self.oci_credentials and not self.s3_bucket) or (self.oci_credentials and self.s3_bucket):
            raise kopf.TemporaryError("Need one of either s3 or ociObjectStorage for MEB Restore")

        self.full_backup = dget_str(spec, "fullBackup", prefix)
        self.incremental_backups = dget_list(spec, "incrementalBackups", prefix, default_value = [])
        self.extra_options = dget_list(
            spec, "extraOptions", prefix, default_value=[], content_type=str)
        try:
            validate_extra_options(
                self.extra_options, RESTORE_EXTRA_OPTIONS,
                prefix+".extraOptions")
        except MebOptionError as exc:
            raise ApiSpecError(str(exc)) from exc

        if "pitr" in spec:
            pitr_spec = dget_dict(spec, "pitr", prefix)
            self.pitr_backup_file = dget_str(pitr_spec, "backupFile", prefix+".pitr", default_value="")
            self.pitr_binlog_name = dget_str(pitr_spec, "binlogName", prefix+".pitr", default_value="") # cluster.name)
            self.pitr_gtid_purge = dget_str(pitr_spec, "gtidPurge", prefix+".pitr", default_value="")

            if "end" in pitr_spec:
                pitr_end_spec = dget_dict(pitr_spec, "end", prefix+".pitr.end")

                if "afterGtids" in pitr_end_spec:
                    self.pitr_end_term = "SQL_AFTER_GTIDS"
                    self.pitr_end_value = dget_str(pitr_end_spec, "afterGtids",
                                                   prefix+".pitr.end.afterGtids")
                elif "beforeGtids" in pitr_end_spec:
                    self.pitr_end_term = "SQL_BEFORE_GTIDS"
                    self.pitr_end_value = dget_str(pitr_end_spec, "beforeGtids",
                                                   prefix+".pitr.end.beforeGtids")



class SQLInitDB:
    storage = None  # TODO type


class InitDB:
    clone: Optional[CloneInitDBSpec] = None
    snapshot: Optional[SnapshotInitDBSpec] = None
    dump: Optional[DumpInitDBSpec] = None
    cluster_set: Optional[ClusterSetInitDBSpec] = None
    meb: Optional[MebInitDBSpec] = None

    def parse(self, spec: dict, prefix: str) -> None:
        dump = dget_dict(spec, "dump", "spec.initDB", {})
        clone = dget_dict(spec, "clone", "spec.initDB", {})
        snapshot = dget_dict(spec, "snapshot", "spec.initDB", {})
        meb = dget_dict(spec, "meb", "spec.initDB", {})
        cluster_set = dget_dict(spec, "clusterSet", "spec.initDB", {})

        if len([x for x in [dump, clone, snapshot, meb, cluster_set] if x]) > 1:
            raise ApiSpecError(
                "Only one of dump, snapshot, meb, clone, or clsuterSet may be specified in spec.initDB")
        if not dump and not clone and not snapshot and not meb and not cluster_set:
            raise ApiSpecError(
                "One of dump, snapshot, meb, clone, or clusterSet may be specified in spec.initDB")

        if clone:
            self.clone = CloneInitDBSpec()
            self.clone.parse(clone, "spec.initDB.clone")
        elif dump:
            self.dump = DumpInitDBSpec()
            self.dump.parse(dump, "spec.initDB.dump")
        elif snapshot:
            self.snapshot = SnapshotInitDBSpec()
            self.snapshot.parse(snapshot, "spec.initDB.snapshot")
        elif meb:
            self.meb = MebInitDBSpec()
            self.meb.parse(meb, "spec.initDB.meb")
        elif cluster_set:
            self.cluster_set = ClusterSetInitDBSpec()
            self.cluster_set.parse(cluster_set, "spec.initDB.clusterSet")


class KeyringConfigStorage(Enum):
    CONFIGMAP = 1
    SECRET = 2

class KeyringSpecBase(ABC):
    @abstractmethod
    def parse(self, spec: dict, prefix: str) -> None:
        ...

    @abstractmethod
    def post_parse(self) -> None:
        ...

    @property
    @abstractmethod
    def name(self):
        ...

    @property
    @abstractmethod
    def is_component(self) -> bool:
        ...

    def add_to_global_manifest(self, manifest: dict) -> None:
        component_name = f"file://component_keyring_{self.name}"
        if not manifest.get("components"):
            manifest["components"] = f"{component_name}"
        else:
            manifest["components"] = manifest["components"] + f",{component_name}"  # no space allowed after comma

    @abstractmethod
    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        ...

    @property
    def component_manifest_name(self) -> str:
        return f"component_keyring_{self.name}.cnf"

    @property
    @abstractmethod
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        ...

    @staticmethod
    def k8s_safe(s: str) -> str:
        return s.replace("_", "-").replace(".", "-")


class KeyringNoneSpec(KeyringSpecBase):
    def parse(self, spec: dict, prefix: str) -> None:
        ...

    @property
    def name(self):
        return "none"

    @property
    def is_component(self) -> bool:
        return True


    def post_parse(self) -> None:
        return

    def add_to_global_manifest(self, manifest: dict) -> None:
        ...

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        ...

    @property
    def component_manifest_name(self) -> str:
        return "invalid-component-manifest-name-call"

    @property
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        return KeyringConfigStorage.CONFIGMAP


class KeyringKmipSpec(KeyringSpecBase):
    container_name = "mysql"
    configuration_secret_name = "n/a"
    cache_keys = False
    server = None
    standy_servers = None

    def __init__(self, namespace: str, global_manifest_name: str, component_config_configmap_name: str, keyring_mount_path: str, cluster_name: str):
        self.namespace = namespace
        self.global_manifest_name = global_manifest_name
        self.component_config_configmap_name = component_config_configmap_name
        self.keyring_mount_path = keyring_mount_path
        self.cluster_name = cluster_name
        self.config_file_mount_path = self.keyring_mount_path

        self.sts_modifier = StsModifier("KeyringKmipSpec")

    @property
    def name(self) -> str:
        return "kmip"

    def parse(self, spec: dict, prefix: str) -> None:
        self.configuration_secret_name = dget_str(spec, "configuration", prefix)
        self.cache_keys = dget_bool(spec, "cacheKeys", prefix, default_value=False)
        self.server = dget_str(spec, "server", prefix)
        self.standy_servers = dget_list(spec, "standbyServer", prefix, default_value=[], content_type=str)

    def post_parse(self) -> None:
        # This is boilerplate code to mount the local component configuration
        cm_mount_name = f"keyring{self.k8s_safe(self.name)}-componentconf"

        config_storage = "configMap" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secret"
        config_storage_name = "name" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secretName"
        self.sts_modifier.add_volume({
            "name": cm_mount_name,
            config_storage: {
                config_storage_name : self.component_config_configmap_name,
                "items": [
                    {
                        "key" : self.component_manifest_name,
                        "path": self.component_manifest_name
                    }
                ]
            }
        })

        self.sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name": cm_mount_name,
                    "mountPath": f"/usr/lib64/mysql/plugin/{self.component_manifest_name}",
                    "subPath": self.component_manifest_name     #should be the same as the volume.configMap.items.path
                }
            ]
        })
        # Boilerplate for local component conf ends here

        # This boilerplate could be extracted to the class Keyring to always mount the local component conf for every keyring
        # that is of type component. This way only add_component_manifest() will need to be touched in most cases unless
        # user provided files need to be mounted into the initmysql and mysql containers. If so, this should happen hereafter.

        configuration_secret_mount_name = f"keyring{self.k8s_safe(self.name)}-configuration"
        self.sts_modifier.add_volume({
            "name": configuration_secret_mount_name,
            "secret": {
                "secretName": self.configuration_secret_name,
            }
        })

        self.sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name": configuration_secret_mount_name,
                    # component kmip will look for a ssl/ subdir. In the secret there is not prefix, we don't want it, so we need to mount one level down with ssl in the name
                    "mountPath": self.keyring_mount_path + "/ssl",
                }
            ]
        })
        # We don't have additional configuration beyound the one in the local component conf, thus
        # we don't need to add a volume and volume mounts here. get_configmaps_cb() and get_secrets_cb() are empty anyway

    @property
    def is_component(self) -> bool:
        return True

    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        return self.sts_modifier.get_add_to_sts_cb()

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        return self.sts_modifier.get_remove_from_sts_cb()

    def get_configmaps_cb(self) -> Optional[GetConfigMapHandler]:
        # No additional configuration in configmaps beyong the one in the local component manifest created by add_component_manifest()
        return None

    def get_secrets_cb(self) -> Optional[GetSecretsHandler]:
        # No additional configuration in secrets beyond the one in the local component manifest created by add_component_manifest()
        return None

    def get_add_to_initconf_cb(self):
        # Components don't use initconf, that is cnf files under /etc/my.cnf.d, which are not dynamic are copied by initconf
        # while configmaps are dynamically remapped by k8s without pod restart
        return None

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if storage_type == self.component_manifest_storage_type:
            data[self.component_manifest_name] = {
                "kmip_configuration_directory": self.keyring_mount_path, #actually only ssl/ in self.keyring_mount_path will be used by the kmip component
                "cache_keys": self.cache_keys,
                "server": self.server,
                "standby_server": self.standy_servers
            }

    @property
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        return KeyringConfigStorage.CONFIGMAP

    def upgrade_to_component(self, cluster: 'InnoDBCluster', sts: api_client.V1StatefulSet, patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> Optional[tuple[dict, dict]]:
        pass


class KeyringFileSpec(KeyringSpecBase):
    fileName: Optional[str] = None
    readOnly: Optional[bool] = False
    storage: Optional[dict] = None

    def __init__(self, namespace: str, global_manifest_name: str, component_config_configmap_name: str, keyring_mount_path: str):
        self.namespace = namespace
        self.global_manifest_name = global_manifest_name
        self.component_config_configmap_name = component_config_configmap_name
        self.keyring_mount_path = keyring_mount_path
        self.sts_modifier = StsModifier("KeyringFileSpec")

    @property
    def name(self) -> str:
        return "file"

    @property
    def is_component(self) -> bool:
        return True

    def parse(self, spec: dict, prefix: str) -> None:
        self.fileName = dget_str(spec, "fileName", prefix)
        self.readOnly = dget_bool(spec, "readOnly", prefix, default_value=False)
        self.storage = dget_dict(spec, "storage", prefix)

    def post_parse(self) -> None:
        # This is boilerplate code to mount the local component configuration
        cm_mount_name = f"keyring{self.k8s_safe(self.name)}-componentconf"

        config_storage = "configMap" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secret"
        config_storage_name = "name" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secretName"
        self.sts_modifier.add_volume({
            "name": cm_mount_name,
            config_storage: {
                config_storage_name : self.component_config_configmap_name,
                "items": [
                    {
                        "key" : self.component_manifest_name,
                        "path": self.component_manifest_name
                    }
                ]
            }
        })

        self.sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name" : cm_mount_name,
                    "mountPath": f"/usr/lib64/mysql/plugin/{self.component_manifest_name}",
                    "subPath": self.component_manifest_name     #should be the same as the volume.configMap.items.path
                }
            ]
        })


        if self.storage:
            storage_mount_name = f"keyring{self.k8s_safe(self.name)}-storage"
            self.sts_modifier.add_volume({
                "name": storage_mount_name,
                **self.storage
            })
            self.sts_modifier.add_volume_mount({
                "initContainers": ["initmysql"],
                "containers": ["mysql"],
                "mounts": [
                    {
                        "name" : storage_mount_name,
                        "mountPath": self.keyring_mount_path,
                    }
                ]
            })


    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        return self.sts_modifier.get_add_to_sts_cb()

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        return self.sts_modifier.get_remove_from_sts_cb()

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if storage_type == self.component_manifest_storage_type:
            data[self.component_manifest_name] = {
                "path": str(PurePosixPath(self.keyring_mount_path)
                            .joinpath(self.fileName)),
                "read_only": self.readOnly,
            }

    @property
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        return KeyringConfigStorage.CONFIGMAP


class KeyringEncryptedFileSpec(KeyringSpecBase):
    # TODO: Storage must be mounted
    fileName: Optional[str] = None
    readOnly: Optional[bool] = False
    password: Optional[str] = None
    storage: Optional[dict] = None

    def __init__(self, namespace: str, global_manifest_name: str, component_config_configmap_name: str, keyring_mount_path: str):
        self.namespace = namespace
        self.global_manifest_name = global_manifest_name
        self.component_config_configmap_name = component_config_configmap_name
        self.keyring_mount_path = keyring_mount_path
        self.sts_modifier = StsModifier("KeyringEncryptedFileSpec")

    @property
    def name(self) -> str:
        return "encrypted_file"

    @property
    def is_component(self) -> bool:
        return True

    def parse(self, spec: dict, prefix: str) -> None:
        def get_password_from_secret(secret_name: str) -> str:
            expected_key = 'keyring_password'
            try:
                password = cast(api_client.V1Secret,
                                api_core.read_namespaced_secret(secret_name, self.namespace))

                if not expected_key in password.data:
                   raise ApiSpecError(f"Secret {secret_name} has no key {expected_key}")
                password = password.data[expected_key]

                if not password:
                    raise ApiSpecError(f"Secret {secret_name}'s {expected_key} is empty")

                return utils.b64decode(password)
            except ApiException as e:
                if e.status == 404:
                    raise ApiSpecError(f"Secret {secret_name} is missing")
                raise


        self.fileName = dget_str(spec, "fileName", prefix)
        self.readOnly = dget_bool(spec, "readOnly", prefix, default_value=False)
        self.password = get_password_from_secret(dget_str(spec, "password", prefix))

        self.storage = dget_dict(spec, "storage", prefix)

    def post_parse(self) -> None:
        # This is boilerplate code to mount the local component configuration
        cm_mount_name = f"keyring{self.k8s_safe(self.name)}-componentconf"

        config_storage = "configMap" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secret"
        config_storage_name = "name" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secretName"
        self.sts_modifier.add_volume({
            "name": cm_mount_name,
            config_storage: {
                config_storage_name : self.component_config_configmap_name,
                "items": [
                    {
                        "key" : self.component_manifest_name,
                        "path": self.component_manifest_name
                    }
                ]
            }
        })

        self.sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name": cm_mount_name,
                    "mountPath": f"/usr/lib64/mysql/plugin/{self.component_manifest_name}",
                    "subPath": self.component_manifest_name     #should be the same as the volume.configMap.items.path
                }
            ]
        })

        if self.storage:
            storage_mount_name = f"keyring{self.k8s_safe(self.name)}-storage"
            self.sts_modifier.add_volume({
                "name": storage_mount_name,
                **self.storage
            })
            self.sts_modifier.add_volume_mount({
                "initContainers": ["initmysql"],
                "containers": ["mysql"],
                "mounts": [
                    {
                        "name" : storage_mount_name,
                        "mountPath": self.keyring_mount_path,
                    }
                ]
            })

    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        return self.sts_modifier.get_add_to_sts_cb()

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        return self.sts_modifier.get_remove_from_sts_cb()

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if storage_type == self.component_manifest_storage_type:
            data[self.component_manifest_name] = {
                "path": str(PurePosixPath(self.keyring_mount_path)
                            .joinpath(self.fileName)),
                "read_only": self.readOnly,
                "password": self.password,
            }

    @property
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        return KeyringConfigStorage.SECRET


class KeyringHashicorpVaultSpec(KeyringSpecBase):
    authMode: Optional[str] = None
    authPath: Optional[str] = None
    authSecret: Optional[str] = None
    caCertificate: Optional[str] = None
    caching: Optional[bool] = None
    serverUrl: Optional[str] = None
    storePath: Optional[str] = None
    tokenSecret: Optional[str] = None

    def __init__(self, namespace: str, global_manifest_name: str, component_config_configmap_name: str, keyring_mount_path: str, cluster_name: str):
        self.namespace = namespace
        self.global_manifest_name = global_manifest_name
        self.component_config_configmap_name = component_config_configmap_name
        self.keyring_mount_path = keyring_mount_path
        self.cluster_name = cluster_name
        self.sts_modifier = StsModifier("KeyringHashicorpVaultSpec")

    @property
    def name(self) -> str:
        return "hashicorp"

    @property
    def is_component(self) -> bool:
        return True

    def parse(self, spec: dict, prefix: str) -> None:
        self.authMode = dget_str(spec, "authMode", prefix, default_value="approle")
        self.caCertificate = dget_str(spec, "caCertificate", prefix, default_value="")
        self.caching = dget_bool(spec, "caching", prefix, default_value=False)
        self.serverUrl = dget_str(spec, "serverUrl", prefix)
        self.storePath = dget_str(spec, "storePath", prefix)

        auth = dget_dict(spec, "auth", prefix)
        if "approle" in auth and "token" not in auth:
            self.authMode = "approle"
            approle = dget_dict(auth, "approl", prefix+".auth")
            self.authPath = dget_str(approle, "authPath", prefix+".auth.approle", default_value="/v1/auth/approle/login")
            self.authSecret = dget_str(approle, "authSecret", prefix+".auth.approle")
        elif "token" in auth and not "approle" in auth:
            self.authMode = "token"
            token = dget_dict(auth, "token", prefix+".auth")
            self.tokenSecret = dget_str(token, "tokenSecret", prefix+".auth.token")
        else:
            raise kopf.TemporaryError(f"Either 'approle' or 'token' must be set in {prefix}.auth")

    def post_parse(self) -> None:
        cm_mount_name = "keyringhashicorp-conf"
        self.sts_modifier.add_volume({
            "name": cm_mount_name,
            "configMap": {
                "name" : self.component_config_configmap_name,
                "items": [
                    {
                        "key" : self.component_manifest_name,
                        "path": self.component_manifest_name
                    }
                ]
            }
        })
        self.sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name" : cm_mount_name,
                    "mountPath": f"/usr/lib64/mysql/plugin/{self.component_manifest_name}",
                    "subPath": self.component_manifest_name     #should be the same as the volume.configMap.items.path
                }
            ]
        })

        if self.tokenSecret:
            self.sts_modifier.add_volume({
                "name": "keyring-hashicorp-token",
                "secret": {
                    "secretName" : self.tokenSecret
                }
            })
            self.sts_modifier.add_volume_mount({
                "initContainers": ["initmysql"],
                "containers": ["mysql"],
                "mounts": [
                    {
                        "name" : "keyring-hashicorp-token",
                        "mountPath": "/etc/mysql-keyring-token",
                    }
                ]
            })


        if self.caCertificate:
            self.sts_modifier.add_volume({
                "name": "keyring-hashicorp-ca",
                "configMap": {
                    "name" : self.caCertificate,
                }
            })
            self.sts_modifier.add_volume_mount({
                "initContainers": ["initmysql"],
                "containers": ["mysql"],
                "mounts": [
                    {
                        "name" : "keyring-hashicorp-ca",
                        "mountPath": "/etc/mysql-keyring-ca",
                    }
                ]
            })

    @property
    def component_manifest_name(self) -> str:
        return f"component_keyring_{self.name}.cnf"

    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        return self.sts_modifier.get_add_to_sts_cb()

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        return self.sts_modifier.get_remove_from_sts_cb()

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if (self.authMode == "approle" and storage_type == KeyringConfigStorage.SECRET) or \
                (self.authMode == "token" and storage_type == KeyringConfigStorage.CONFIGMAP):
            data[self.component_manifest_name] = {
                "auth_mode": self.authMode,
                "caching": "ON" if self.caching else "OFF",
                "server_url": self.serverUrl,
                "store_path": self.storePath
            }

            if self.authMode == "approle":
                opts = data[self.component_manifest_name]
                try:
                    secret = cast(api_client.V1Secret,
                                  api_core.read_namespaced_secret(self.authSecret, self.namespace))
                except ApiException as exc:
                    raise kopf.TemporaryError(f"Failed loading authentication secret {self.authSecret}: {exc}")

                opts["auth_path"] = self.authPath

                for ele in ("role_id", "secret_id"):
                    if "role_id.txt" not in secret.data:
                        raise kopf.TemporaryError(f"HashiCorp Vault Authentication Secret {self.namespace}/{self.authSecret} got no element '{ele}.txt'")

                    opts[ele] = secret.data[ele+".txt"]
            elif self.authMode == "token":
                data[self.component_manifest_name]["token_path"] = "/etc/mysql-keyring-token/token.txt"
            else:
                # This shouldn't happen and be caught during parse and the if above should prevent reaching here
                raise kopf.TemporaryError("authMode must be either 'approle' or 'token' in authMode")

            if self.caCertificate:
               data[self.component_manifest_name]["ca_path"] = "/etc/mysql-keyring-ca/ca.pem"

    @property
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        if self.authMode == "approle":
            return KeyringConfigStorage.SECRET
        elif self.authMode == "token":
            return KeyringConfigStorage.CONFIGMAP
        else:
            # should never happen, should be caught before while parsing
            raise kopf.TemporaryError("Invalid auth mode")


class KeyringOciSpec(KeyringSpecBase):
    user: Optional[str] = None
    keySecret: Optional[str] = None
    keyFingerprint: Optional[str] = None
    tenancy: Optional[str] = None
    compartment: Optional[str] = None
    virtualVault: Optional[str] = None
    masterKey: Optional[str] = None
    caCertificate: Optional[str] = None
    endpointEncryption: Optional[str] = None
    endpointManagement: Optional[str] = None
    endpointVaults: Optional[str] = None
    endpointSecrets: Optional[str] = None

    def __init__(self, namespace: str, global_manifest_name: str, component_config_configmap_name: str, keyring_mount_path: str, cluster_name: str):
        self.namespace = namespace
        self.global_manifest_name = global_manifest_name
        self.component_config_configmap_name = component_config_configmap_name
        self.keyring_mount_path = keyring_mount_path
        self.cluster_name = cluster_name
        self.sts_modifier = StsModifier("KeyringOciSpec")

    @property
    def name(self) -> str:
        return "oci"

    @property
    def is_component(self) -> bool:
        return True

    def parse(self, spec: dict, prefix: str) -> None:
        self.user = dget_str(spec, "user", prefix)
        self.keySecret = dget_str(spec, "keySecret", prefix)
        self.keyFingerprint = dget_str(spec, "keyFingerprint", prefix)
        self.tenancy= dget_str(spec, "tenancy", prefix)
        self.compartment = dget_str(spec, "compartment", prefix)
        self.virtualVault = dget_str(spec, "virtualVault", prefix)
        self.masterKey = dget_str(spec, "masterKey", prefix)
        self.caCertificate = dget_str(spec, "caCertificate", prefix, default_value="")
        endpoints = dget_dict(spec, "endpoints", prefix, {})
        if endpoints:
            self.endpointEncryption = dget_str(endpoints, "encryption", prefix)
            self.endpointManagement = dget_str(endpoints, "management", prefix)
            self.endpointVaults = dget_str(endpoints, "vaults", prefix)
            self.endpointSecrets = dget_str(endpoints, "secrets", prefix)

    def post_parse(self) -> None:
        self.sts_modifier.add_volume({
            "name": "ocikey",
            "secret": {
                "secretName" : self.keySecret,
            }
        })
        # This is boilerplate code to mount the local component configuration
        cm_mount_name = f"keyring{self.k8s_safe(self.name)}-componentconf"

        config_storage = "configMap" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secret"
        config_storage_name = "name" if self.component_manifest_storage_type == KeyringConfigStorage.CONFIGMAP else "secretName"
        self.sts_modifier.add_volume({
            "name": cm_mount_name,
            config_storage: {
                config_storage_name : self.component_config_configmap_name,
                "items": [
                    {
                        "key" : self.component_manifest_name,
                        "path": self.component_manifest_name
                    }
                ]
            }
        })

        self.sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name" : "ocikey",
                    "mountPath": "/.oci"
                },
                {
                    "name" : cm_mount_name,
                    "mountPath": f"/usr/lib64/mysql/plugin/{self.component_manifest_name}",
                    "subPath": self.component_manifest_name     #should be the same as the volume.configMap.items.path
                }
            ]
        })

        if self.caCertificate:
            self.sts_modifier.add_volume({
                "name": "oci-keyring-ca",
                "secret": {
                    "secretName" : self.caCertificate,
                }
            })
            self.sts_modifier.add_volume_mount({
                "initContainers": ["initmysql"],
                "containers": ["mysql"],
                "mounts": [
                    {
                        "name" : "oci-keyring-ca",
                        "mountPath": "/etc/mysql-keyring-ca",
                    }
                ]
            })

    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        return self.sts_modifier.get_add_to_sts_cb()

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        return self.sts_modifier.get_remove_from_sts_cb()

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if storage_type == KeyringConfigStorage.CONFIGMAP:
            data[self.component_manifest_name] = {
                "user": self.user,
                "tenancy": self.tenancy,
                "compartment": self.compartment,
                "virtual_vault": self.virtualVault,
                "master_key": self.masterKey,
                "encryption_endpoint": self.endpointEncryption,
                "management_endpoint": self.endpointManagement,
                "vaults_endpoint": self.endpointVaults,
                "secrets_endpoint": self.endpointSecrets,
                "key_file": "/.oci/privatekey",
                "key_fingerprint": self.keyFingerprint
            }

            if self.caCertificate:
               data[self.component_manifest_name]["keyring_oci_ca_certificate"] = "/etc/mysql-keyring-ca/certificate"

    # TODO [compat8.3.0] remove this when compatibility pre 8.3.0 isn't needed anymore
    def upgrade_to_component(self, cluster: 'InnoDBCluster', sts: api_client.V1StatefulSet, patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> Optional[tuple[dict, dict]]:
        logger.info("Upgrading keyring OCI from plugin to component")
        # numbering could be different because of other subsystems
        if not (initconf:= cluster.get_initconf(cluster.parsed_spec)):
            raise kopf.TemporaryError("Can't find initconf while trying to upgrade keyring OCI to component")

        for key in initconf.data:
            if 'keyring-oci' in key:
                logger.info("Found keyring OCI initconf configuration. Will remove it from initconf!")
                initconf_patch = [{"op": "remove", "path": f"/data/{key}"}]
                def on_apie_404_handler(exc: ApiException, logger: Logger):
                    if exc.status == 404:
                        logger.warning("Object not found! Exception: {exc}")
                        return
                    raise exc

                patcher.patch_configmap(self.namespace, f"{self.cluster_name}-initconf", initconf_patch, on_apie_404_handler)
                return
        logger.info("No keyring OCI initconf configuration found. No need to update initconf!")

    @property
    def component_manifest_storage_type(self) -> KeyringConfigStorage:
        return KeyringConfigStorage.CONFIGMAP


# TODO: merge this with KeyringSpecBase
class KeyringSpec:
    keyring: Optional[KeyringSpecBase] = KeyringNoneSpec()
    global_manifest_name: str = 'mysqld.my'
    keyring_mount_path: str = '/keyring'

    def __init__(self, namespace: str, cluster_name: str):
        self.namespace = namespace
        self.cluster_name = cluster_name

        self.kr_sts_modifier = StsModifier("KeyringSpec")

    def parse(self, spec: dict, prefix: str) -> None:
        krFile = dget_dict(spec, "file", prefix, {})
        krEncryptedFile = dget_dict(spec, "encryptedFile", prefix, {})
        krHashicorp = dget_dict(spec, "hashicorp", prefix, {})
        krOci = dget_dict(spec, "oci", prefix, {})
        krKmip = dget_dict(spec, "kmip", prefix, {})

        specified_krings = [x for x in [krFile, krEncryptedFile, krHashicorp, krOci, krKmip] if x]

        if len(specified_krings) > 1:
            raise ApiSpecError("Only one of file, encryptedFile, hashicorp, oci or kmip may be specified in spec.keyring")
        elif len(specified_krings) == 0:
            raise ApiSpecError("One of file, encryptedFile, hashicorp, oci or kmip must be specified in spec.keyring")

        if krFile:
            self.keyring = KeyringFileSpec(self.namespace, self.global_manifest_name, self.component_config_configmap_name, self.keyring_mount_path)
            self.keyring.parse(krFile, "spec.keyring.file")
        elif krEncryptedFile:
            self.keyring = KeyringEncryptedFileSpec(self.namespace, self.global_manifest_name, self.component_config_configmap_name, self.keyring_mount_path)
            self.keyring.parse(krEncryptedFile, "spec.keyring.encryptedFile")
        elif krHashicorp:
            self.keyring = KeyringHashicorpVaultSpec(self.namespace, self.global_manifest_name, self.component_config_secret_name, self.keyring_mount_path, self.cluster_name )
            self.keyring.parse(krHashicorp, "spec.keyring.hashicorp")
        elif krOci:
            self.keyring = KeyringOciSpec(self.namespace, self.global_manifest_name, self.component_config_configmap_name, self.keyring_mount_path, self.cluster_name)
            self.keyring.parse(krOci, "spec.keyring.oci")
        elif krKmip:
            self.keyring = KeyringKmipSpec(self.namespace, self.global_manifest_name, self.component_config_configmap_name, self.keyring_mount_path, self.cluster_name)
            self.keyring.parse(krKmip, "spec.keyring.kmip")
        else:
            self.keyring = KeyringNoneSpec()

        self.keyring.post_parse()
        self.post_parse()

    def post_parse(self) -> None:
        if isinstance(self.keyring, KeyringNoneSpec) or not self.keyring.is_component:
            # this is slight misuse of a NullObject type ...
            return
        self.kr_sts_modifier.add_volume({
            "name": "globalcomponentconf",
            "configMap": {
                "name" : self.component_config_configmap_name,
                "items": [
                    {
                        "key" : self.global_manifest_name,
                        "path": self.global_manifest_name
                    }
                ]
            }
        })

        self.kr_sts_modifier.add_volume_mount({
            "initContainers": ["initmysql"],
            "containers": ["mysql"],
            "mounts": [
                {
                    "name" : "globalcomponentconf",
                    "mountPath": f"/usr/sbin/{self.global_manifest_name}",
                    "subPath": self.global_manifest_name     #should be the same as the volume.configMap.items.path
                }
            ]
        })

    @property
    def name(self) -> str:
        return self.keyring.name

    @property
    def component_config_configmap_name(self) -> str:
        return f"{self.cluster_name}-componentconf"

    @property
    def component_config_secret_name(self) -> str:
        return f"{self.cluster_name}-componentconf"

    def _get_keyring_cb_if_exists(self, cb_name) -> Optional[Callable]:
        if hasattr(self.keyring, cb_name) and callable(cb := getattr(self.keyring, cb_name)):
            return cb()

        return None

    def get_add_to_initconf_cb(self) -> Optional[Callable[[Dict, str, Logger], None]]:
        return self._get_keyring_cb_if_exists("get_add_to_initconf_cb")

    def get_add_to_sts_cb(self) -> Optional[AddToStsHandler]:
        def cb(sts: Union[dict,api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
            self.kr_sts_modifier.get_add_to_sts_cb()(sts, patcher, logger)

            cb = self._get_keyring_cb_if_exists("get_add_to_sts_cb")
            if cb:
                cb(sts, patcher, logger)

        return cb

    def get_remove_from_sts_cb(self) -> Optional[RemoveFromStsHandler]:
        def cb(sts: Union[dict,api_client.V1StatefulSet], patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> None:
            self.kr_sts_modifier.get_remove_from_sts_cb()(sts, patcher, logger)

            cb = self._get_keyring_cb_if_exists("get_remove_from_sts_cb")
            if cb:
                cb(sts, patcher, logger)

        return cb

    def get_configmaps_cb(self) -> Optional['GetConfigMapHandler']:
        def cb(prefix: str, logger: Logger) -> Optional[List[Tuple[str, Optional[Dict]]]]:
            configmaps = []
            if self.keyring.is_component:
                data = {
                    self.global_manifest_name : {}
                }

                self.keyring.add_to_global_manifest(data[self.global_manifest_name])
                self.keyring.add_component_manifest(data, KeyringConfigStorage.CONFIGMAP)

                configmaps.append(
                    (
                        self.component_config_configmap_name,
                        {
                            'apiVersion' : "v1",
                            'kind': 'ConfigMap',
                            'metadata': {
                                'name': self.component_config_configmap_name,
                            },
                            'data' : { k: utils.dict_to_json_string(data[k]) for k in data }
                        }
                    )
                )

            cb = self._get_keyring_cb_if_exists("get_configmaps_cb")
            if cb and (more_configmaps := cb(prefix, logger)):
                configmaps += more_configmaps

            return configmaps

        return cb

    def get_secrets_cb(self) -> Optional['GetConfigMapHandler']:
        def cb(prefix: str, logger: Logger) -> Optional[List[Tuple[str, Optional[Dict]]]]:
            secrets = []

            if self.keyring.is_component:
                data = {}
                self.keyring.add_component_manifest(data, KeyringConfigStorage.SECRET)

                if len(data) != 0:
                    secrets.append(
                        (
                            self.component_config_configmap_name,
                            {
                                'apiVersion' : "v1",
                                'kind': 'Secret',
                                'metadata': {
                                    'name': self.component_config_secret_name
                                },
                                'data' : { k: utils.b64encode(utils.dict_to_json_string(data[k])) for k in data }
                            }
                        )
                    )

            cb = self._get_keyring_cb_if_exists("get_secrets_cb")
            if cb and (more_secrets := cb(prefix, logger)):
                secrets += more_secrets

            return secrets

        return cb

    def add_to_sts_spec(self, statefulset: dict):
        # TODO: This is now for OCI only, as it is more complicated
        if self.keyring:
            if hasattr(self.keyring, "add_to_sts_spec") and callable(getattr(self.keyring, "add_to_sts_spec")):
                self.keyring.add_to_sts_spec(statefulset)

    # TODO [compat8.3.0] remove this when compatibility pre 8.3.0 isn't needed anymore
    def upgrade_to_component(self, cluster: 'InnoDBCluster', sts: api_client.V1StatefulSet, patcher: 'InnoDBClusterObjectModifier', logger: Logger) -> Optional[tuple[dict, dict]]:
        # only exists for OCI keyring
        if self.keyring:
            if hasattr(self.keyring, "upgrade_to_component") and callable(getattr(self.keyring, "upgrade_to_component")):
                return self.keyring.upgrade_to_component(cluster, sts, patcher, logger)


class RouterSpec:
    # number of Router instances (optional)
    instances: int = 1

    # Router version, if user wants to override it (latest by default)
    version: str = None # config.DEFAULT_ROUTER_VERSION_TAG

    podSpec: dict = {}
    podAnnotations: Optional[dict] = None
    podLabels: Optional[dict] = None

    bootstrapOptions: list =  []
    options: list = []

    routingOptions: dict = {}

    tlsSecretName: str = ""

    def parse(self, spec: dict, prefix: str) -> None:
        if "instances" in spec:
            self.instances = dget_int(spec, "instances", prefix)

        if "version" in spec:
            self.version = dget_str(spec, "version", prefix)

        if "tlsSecretName" in spec:
            self.tlsSecretName = dget_str(spec, "tlsSecretName", prefix)

        if "podSpec" in spec:  # TODO - replace with something more specific
            self.podSpec = dget_dict(spec, "podSpec", prefix)

        if "podAnnotations" in spec:
            self.podAnnotations = dget_dict(spec, "podAnnotations", prefix)

        if "podLabels" in spec:
            self.podLabels = dget_dict(spec, "podLabels", prefix)

        if "bootstrapOptions" in spec:
            self.bootstrapOptions = dget_list(spec, "bootstrapOptions", prefix)

        if "options" in spec:
            self.options = dget_list(spec, "options", prefix)

        if "routingOptions" in spec:
            self.routingOptions = dget_dict(spec, "routingOptions", prefix)


class InstanceServiceSpec:
    annotations: dict = {}
    labels: dict = {}

    def parse(self, spec: dict, prefix: str) -> None:
        if "annotations" in spec:
            self.annotations = dget_dict(spec, "annotations", prefix)

        if "labels" in spec:
            self.labels = dget_dict(spec, "labels", prefix)


class ServiceSpec:
    type: str = "ClusterIP"
    annotations: dict = {}
    labels: dict = {}
    defaultPort: str = "mysql-rw"


    def parse(self, spec: dict, prefix: str) -> None:
        if "type" in spec:
            self.type = dget_str(spec, "type", prefix)

        if "annotations" in spec:
            self.annotations = dget_dict(spec, "annotations", prefix)

        if "labels" in spec:
            self.labels = dget_dict(spec, "labels", prefix)

        if "defaultPort" in spec:
            self.defaultPort = dget_str(spec, "defaultPort", prefix)

    def get_default_port_number(self, spec: 'InnoDBClusterSpec') -> int:
        ports = {
            "mysql-ro": spec.router_roport,
            "mysql-rw": spec.router_rwport,
            "mysql-rw-split": spec.router_rwsplitport
        }
        return ports[self.defaultPort]


class DataDirPermissionsSpec:
    setRightsUsingInitContainer: bool = True
    fsGroupChangePolicy: Optional[str] = ""

    def parse(self, spec: dict, prefix: str) -> None:
        if "setRightsUsingInitContainer" in spec:
            self.setRightsUsingInitContainer = dget_bool(spec, "setRightsUsingInitContainer", prefix)

        if "fsGroupChangePolicy" in spec:
            self.fsGroupChangePolicy = dget_str(spec, "fsGroupChangePolicy", prefix)


class MebSpec:
    tlsSecretName: str = ""

    def parse(self, spec: dict, prefix: str) -> None:
        if "tlsSecretName" in spec:
            self.tlsSecretName = dget_str(spec, "tlsSecretName", prefix)


# Must correspond to the names in the CRD
class InnoDBClusterSpecProperties(Enum):
    SERVICE = "service"
    INSTANCE_SERVICE = "instanceService"
    LOGS = "logs"
    ROUTER = "router"
    BACKUP_PROFILES = "backupProfiles"
    BACKUP_SCHEDULES = "backupSchedules"
    METRICS = "metrics"
    INITDB = "initDB"
    KEYRING = "keyring"
    MEB = "meb"


class AbstractServerSetSpec(abc.ABC):
    # name of user-provided secret containing root password (optional)
    secretName: Optional[str] = None

    # name of secret with CA for SSL
    tlsCASecretName: str = ""
    # name of secret with certificate and private key (server and router)
    tlsSecretName: str = ""
    # whether to allow use of self-signed TLS certificates
    tlsUseSelfSigned: bool = False

    # MySQL server version
    version: str = config.DEFAULT_VERSION_TAG
    # Sidecar version: used for initconf, sidecar, batchjob (backup)
    sidecarVersion: str = config.DEFAULT_OPERATOR_VERSION_TAG

    edition: Edition = config.OPERATOR_EDITION

    imagePullPolicy: ImagePullPolicy = config.default_image_pull_policy
    imagePullSecrets: Optional[List[dict]] = None
    imageRepository: str = config.DEFAULT_IMAGE_REPOSITORY

    serviceAccountName: Optional[str] = None
    sidecarRoleBindingName: Optional[str] = None

    # number of MySQL instances (required)
    instances: int = 1
    # base value for server_id
    baseServerId: int
    # override volumeClaimTemplates for datadir in MySQL pods (optional)
    datadirVolumeClaimTemplate = None
    dataDirPermissions: Optional[DataDirPermissionsSpec] = DataDirPermissionsSpec()
    meb: Optional[MebSpec] = MebSpec()

    # additional MySQL configuration options
    mycnf: str = ""
    # override pod template for MySQL (optional)
    podSpec: dict = {}
    podAnnotations: Optional[dict] = None
    podLabels: Optional[dict] = None
    backupSchedules: List[BackupSchedule] = []


    metrics: Optional[MetriscSpec] = None
    logs: Optional[LogsSpec] = None

    # (currently) non-configurable constants
    mysql_port: int = 3306
    mysql_xport: int = 33060
    mysql_grport: int = 33061
    mysql_metrics_port: int = 9104

    router_rwport: int = 6446
    router_roport: int = 6447
    router_rwxport: int = 6448
    router_roxport: int = 6449
    router_rwsplitport: int = 6450

    router_httpport: int = 8443

    serviceFqdnTemplate = None

    # TODO resource allocation for server, router and sidecar
    # TODO recommendation is that sidecar has 500MB RAM if MEB is used

    def __init__(self, namespace: str, name: str, cluster_name: str, spec: dict):
        self.namespace = namespace
        self.name = name
        self.cluster_name = cluster_name
        self.backupSchedules: List[BackupSchedule] = []

    #@abc .abstracmethod
    def load(self, spec: dict) -> None:
        ...

    def _load(self, spec_root: dict, spec_specific: dict, where_specific: str) -> None:
        # initialize now or all instances will share the same list initialized before the ctor
        self.add_to_initconf_cbs: dict[str, List[AddToInitconfHandler]] = {}
        self.remove_from_sts_cbs: dict[str, List[RemoveFromStsHandler]] = {}
        self.add_to_sts_cbs: dict[str, List[AddToStsHandler]] = {}
        self.get_configmaps_cbs: dict[str, List[GetConfigMapHandler]] = {}
        self.get_secrets_cbs: dict[str, List[GetSecretsHandler]] = {}
        self.get_add_to_svc_cbs: dict[str, List[AddToSvcHandler]] = {}
        self.get_svc_monitor_cbs: dict[str, List[GetSvcMonitorHandler]] = {}

        self.secretName = dget_str(spec_root, "secretName", "spec")

        if "tlsCASecretName" in spec_root:
            self.tlsCASecretName = dget_str(spec_root, "tlsCASecretName", "spec")
        else:
            self.tlsCASecretName = f"{self.name}-ca"

        if "tlsSecretName" in spec_root:
            self.tlsSecretName = dget_str(spec_root, "tlsSecretName", "spec")
        else:
            self.tlsSecretName = f"{self.name}-tls"

        if "tlsUseSelfSigned" in spec_root:
            self.tlsUseSelfSigned = dget_bool(spec_root, "tlsUseSelfSigned", "spec")

        self.meb = MebSpec()
        section = InnoDBClusterSpecProperties.MEB.value
        if section in spec_root:
            self.meb.parse(dget_dict(spec_root, section, "spec"), f"spec.{section}")

        self.instances = dget_int(spec_specific, "instances", where_specific)
        if "version" in spec_specific:
            self.version = dget_str(spec_specific, "version", where_specific)

        if "edition" in spec_root:
            self.edition = dget_enum(
                spec_root, "edition", "spec", default_value=config.OPERATOR_EDITION,
                enum_type=Edition)

            if "imageRepository" not in spec_root:
                self.imageRepository = config.DEFAULT_IMAGE_REPOSITORY

        if "imagePullPolicy" in spec_root:
            self.imagePullPolicy = dget_enum(
                spec_root, "imagePullPolicy", "spec",
                default_value=config.default_image_pull_policy,
                enum_type=ImagePullPolicy)

        if "imagePullSecrets" in spec_root:
            self.imagePullSecrets = dget_list(
                spec_root, "imagePullSecrets", "spec", content_type=dict)

        self.serviceAccountName = dget_str(spec_root, "serviceAccountName", "spec", default_value=f"{self.name}-sidecar-sa")
        self.sidecarRoleBindingName = f"{self.name}-sidecar-rb"
        self.switchoverServiceAccountName = f"{self.name}-switchover-sa"
        self.switchoverRoleBindingName = f"{self.name}-switchover-rb"

        if "imageRepository" in spec_root:
            self.imageRepository = dget_str(spec_root, "imageRepository", "spec")

        if "podSpec" in spec_specific:  # TODO - replace with something more specific
            self.podSpec = dget_dict(spec_specific, "podSpec", where_specific)

        if "podAnnotations" in spec_specific:
            self.podAnnotations = dget_dict(spec_specific, "podAnnotations", where_specific)

        if "podLabels" in spec_specific:
            self.podLabels = dget_dict(spec_specific, "podLabels", where_specific)

        if "datadirVolumeClaimTemplate" in spec_specific:
            self.datadirVolumeClaimTemplate = dget_dict(spec_specific, "datadirVolumeClaimTemplate", where_specific)

        self.keyring = KeyringSpec(self.namespace, self.name)
        if (section:= InnoDBClusterSpecProperties.KEYRING.value) in spec_root:
            self.keyring.parse(dget_dict(spec_root, section, "spec"), f"spec.{section}")
            if cb := self.keyring.get_configmaps_cb():
                self.get_configmaps_cbs[InnoDBClusterSpecProperties.KEYRING.value] = [cb]

            if cb := self.keyring.get_secrets_cb():
                self.get_secrets_cbs[InnoDBClusterSpecProperties.KEYRING.value] = [cb]

            if cb := self.keyring.get_add_to_initconf_cb():
                self.add_to_initconf_cbs[InnoDBClusterSpecProperties.KEYRING.value] = [cb]

            if cb := self.keyring.get_remove_from_sts_cb():
                self.remove_from_sts_cbs[InnoDBClusterSpecProperties.KEYRING.value] = [cb]

            if cb := self.keyring.get_add_to_sts_cb():
                self.add_to_sts_cbs[InnoDBClusterSpecProperties.KEYRING.value] = [cb]

        if "mycnf" in spec_root:
            self.mycnf = dget_str(spec_root, "mycnf", "spec")

        self.metrics = MetriscSpec(self.namespace, self.cluster_name)
        if "metrics" in spec_root:
            self.metrics.parse(dget_dict(spec_root, "metrics", "spec"), "spec.metrics")

        if cb := self.metrics.get_configmaps_cb():
            self.get_configmaps_cbs[InnoDBClusterSpecProperties.METRICS.value] = [cb]

        if cb := self.metrics.get_secrets_cb():
            self.get_secrets_cbs[InnoDBClusterSpecProperties.METRICS.value] = [cb]

        if cb := self.metrics.get_add_to_sts_cb():
            self.add_to_sts_cbs[InnoDBClusterSpecProperties.METRICS.value] = [cb]

        if cb := self.metrics.get_add_to_svc_cb():
            self.get_add_to_svc_cbs[InnoDBClusterSpecProperties.METRICS.value] = [cb]

        if cb := self.metrics.get_svc_monitor_cb():
            self.get_svc_monitor_cbs[InnoDBClusterSpecProperties.METRICS.value] = [cb]

        self.logs = LogsSpec(self.namespace, self.cluster_name)
        if (section:= InnoDBClusterSpecProperties.LOGS.value) in spec_root:
            self.logs.parse(dget_dict(spec_root, section, "spec"), f"spec.{section}", getLogger())

            if cb := self.logs.get_configmaps_cb():
                self.get_configmaps_cbs[InnoDBClusterSpecProperties.LOGS.value] = [cb]

            if cb := self.logs.get_secrets_cb():
                self.get_secrets_cbs[InnoDBClusterSpecProperties.LOGS.value] = [cb]

            if cb := self.logs.get_add_to_initconf_cb():
                self.add_to_initconf_cbs[InnoDBClusterSpecProperties.LOGS.value] = [cb]

            if cb := self.logs.get_remove_from_sts_cb():
                self.remove_from_sts_cbs[InnoDBClusterSpecProperties.LOGS.value] = [cb]

            if cb := self.logs.get_add_to_sts_cb():
                self.add_to_sts_cbs[InnoDBClusterSpecProperties.LOGS.value] = [cb]

        # Initialization Options
        section = InnoDBClusterSpecProperties.INITDB.value
        if section in spec_root:
            self.load_initdb(dget_dict(spec_root, section, "spec"))

        # TODO keep a list of base_server_id in the operator to keep things globally unique?
        if "baseServerId" in spec_specific:
            self.baseServerId = dget_int(spec_specific, "baseServerId", "spec")

        self.serviceFqdnTemplate = None
        if "serviceFqdnTemplate" in spec_root:
            self.serviceFqdnTemplate = dget_str(spec_root, "serviceFqdnTemplate",
                                                "spec",
                                                default_value="{service}.{namespace}.svc.{domain}")

        self.dataDirPermissions = DataDirPermissionsSpec()
        section = "datadirPermissions"
        if section in spec_root:
            self.dataDirPermissions.parse(dget_dict(spec_root, section, "spec"), f"spec.{section}")

        self.instanceService = InstanceServiceSpec()
        section = InnoDBClusterSpecProperties.INSTANCE_SERVICE.value
        if section in spec_root:
            self.instanceService.parse(dget_dict(spec_root, section, "spec"), f"spec.{section}")


    def print_backup_schedules(self) -> None:
        for schedule in self.backupSchedules:
            print(f"schedule={schedule}")

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

        if (not self.baseServerId or
                self.baseServerId < config.MIN_BASE_SERVER_ID or
                self.baseServerId > config.MAX_BASE_SERVER_ID):
            raise ApiSpecError(
                f"spec.baseServerId is {self.baseServerId} but must be between "
                f"{config.MIN_BASE_SERVER_ID} and {config.MAX_BASE_SERVER_ID}")

        # TODO validate against downgrades, invalid version jumps

        # validate podSpec through the Kubernetes API
        if self.podSpec:
            pass

        if self.tlsSecretName and not self.tlsCASecretName:
            logger.info("spec.tlsSecretName is set but will be ignored because self.tlsCASecretName is not set")

        if any(getattr(profile, 'meb', None) for profile in self.backupProfiles) and not self.tlsUseSelfSigned:
            if not self.meb.tlsSecretName:
                raise ApiSpecError("spec.meb.tlsSecretName must be set when using MEB backups with tlsUseSelfSigned=false")
            try:
                api_core.read_namespaced_secret(self.meb.tlsSecretName, self.namespace)
            except ApiException as e:
                if e.status == 404:
                    raise ApiSpecError(f'Secret "{self.meb.tlsSecretName}" NOT found')
                raise

        # TODO ensure that if version is set, then image and routerImage are not
        # TODO should we support upgrading router only?

        # validate version
        if self.version:
            # note: format of the version string is defined in the CRD
            [valid_version, version_error] = utils.version_in_range(self.version)

            if not valid_version:
                raise ApiSpecError(version_error)

        self.logs.validate()

    def format_image(self, image, version):
        if self.imageRepository:
            return f"{self.imageRepository}/{image}:{version}"
        return f"{image}:{version}"

    @property
    def mysql_image(self) -> str:
        # server image version is the one given by the user or latest by default
        image = config.MYSQL_SERVER_IMAGE if self.edition == Edition.community else config.MYSQL_SERVER_EE_IMAGE
        return self.format_image(image, self.version)

    @property
    def operator_image(self) -> str:
        # version is the same as ours (operator)
        if self.edition == Edition.community:
            image = config.MYSQL_OPERATOR_IMAGE
        else:
            image = config.MYSQL_OPERATOR_EE_IMAGE

        return self.format_image(image, self.sidecarVersion)


    @property
    def mysql_image_pull_policy(self) -> str:
        return self.imagePullPolicy.value

    @property
    def sidecar_image_pull_policy(self) -> str:
        return self.imagePullPolicy.value

    @property
    def operator_image_pull_policy(self) -> str:
        return self.imagePullPolicy.value

    @property
    def extra_env(self) -> str:
        if config.debug:
            return f"""
- name: MYSQL_OPERATOR_DEBUG
  value: "{config.debug}"
"""
        else:
            return ""

    def get_extra_volumes(self, ca_and_tls) -> str:
        volumes = ""

        if not self.tlsUseSelfSigned:
            ca_file_name = ca_and_tls["CA"]

            volumes = f"""
- name: ssl-ca-data
  projected:
    sources:
    - secret:
        name: {self.tlsCASecretName}
- name: ssl-key-data
  projected:
    sources:
    - secret:
        name: {self.tlsSecretName}
"""

        return volumes

    @property
    def extra_volume_mounts(self) -> str:
        mounts = []
        if not self.tlsUseSelfSigned:
            mounts.append("""
- mountPath: /etc/mysql-ssl/ca
  name: ssl-ca-data""")
            mounts.append("""
- mountPath: /etc/mysql-ssl/key
  name: ssl-key-data""")

        return "\n".join(mounts)

    @property
    def extra_sidecar_volume_mounts(self) -> str:
        mounts = []
        if not self.tlsUseSelfSigned:
            mounts.append("""
- mountPath: /etc/mysql-ssl/ca
  name: ssl-ca-data""")
            mounts.append("""
- mountPath: /etc/mysql-ssl/key
  name: ssl-key-data""")

        return "\n".join(mounts)

    @property
    def image_pull_secrets(self) -> str:
        if self.imagePullSecrets:
            return f"imagePullSecrets:\n{yaml.safe_dump(self.imagePullSecrets)}"
        return ""

class ReadReplicaSpec(AbstractServerSetSpec):
    def __init__(self, namespace: str, cluster_name: str, spec_root: dict,
                 spec_specific: dict, where_specific: str):
        name = f"{cluster_name}-{dget_str(spec_specific, 'name', where_specific)}"
        super().__init__(namespace, name, cluster_name, spec_root)
        self.load(spec_root, spec_specific, where_specific)

    def load(self, spec_root: dict, spec_specific: dict, where_specific: str):
        self._load(spec_root, spec_specific, where_specific)

    @property
    def headless_service_name(self) -> str:
        #self.name is actually "{cluster_name}-{rr_name}"
        return f"{self.name}-instances"

class InnoDBClusterSpec(AbstractServerSetSpec):
    service: ServiceSpec = ServiceSpec()

    # Initialize DB
    initDB: Optional[InitDB] = None

    router: RouterSpec = RouterSpec()

    # Backup info
    backupProfiles: List[BackupProfile] = []

    # ReadReplica
    readReplicas: List[ReadReplicaSpec] = []

    def __init__(self, namespace: str, name: str, spec: dict):
        super().__init__(namespace, name, name, spec)
        self.load(spec)

    def load(self, spec: dict) -> None:
        self._load(spec, spec, "spec")

        self.service = ServiceSpec()
        section = InnoDBClusterSpecProperties.SERVICE.value
        if section in spec:
            self.service.parse(dget_dict(spec, section, "spec"), f"spec.{section}")

        # Router Options
        self.router = RouterSpec()
        section = InnoDBClusterSpecProperties.ROUTER.value
        if section in spec:
            self.router.parse(dget_dict(spec, section, "spec"), f"spec.{section}")

        if not self.router.tlsSecretName:
            self.router.tlsSecretName = f"{self.name}-router-tls"

        self.backupProfiles = []
        section = InnoDBClusterSpecProperties.BACKUP_PROFILES.value
        if section in spec:
            profiles = dget_list(spec, section, "spec", [], content_type=dict)
            for profile in profiles:
                self.backupProfiles.append(self.parse_backup_profile(profile))

        self.backupSchedules = []
        section = InnoDBClusterSpecProperties.BACKUP_SCHEDULES.value
        if section in spec:
            schedules = dget_list(spec, section, "spec", [], content_type=dict)
            for schedule in schedules:
                self.backupSchedules.append(self.parse_backup_schedule(schedule))

        self.readReplicas = []
        section = "readReplicas"
        if section in spec:
            read_replicas = dget_list(spec, section, "spec", [], content_type=dict)
            i = 0
            for replica in read_replicas:
                self.readReplicas.append(self.parse_read_replica(spec, replica, f"spec.{section}[{i}]"))
                i += 1


    def validate(self, logger: Logger) -> None:
        super().validate(logger)

        # check that the secret exists and it contains rootPassword
        if self.secretName:  # TODO
            pass

        if self.mycnf:
            if "[mysqld]" not in self.mycnf:
                logger.warning(
                    "spec.mycnf data does not contain a [mysqld] line")

    def parse_backup_profile(self, spec: dict) -> BackupProfile:
        profile = BackupProfile()
        profile.parse(spec, "spec.backupProfiles")
        return profile

    def parse_backup_schedule(self, spec: dict) -> BackupSchedule:
        schedule = BackupSchedule(self)
        schedule.parse(spec, "spec.backupSchedules")
        return schedule

    def parse_read_replica(self, spec_root: dict, spec_specific: dict, where_specific: str):
        replica = ReadReplicaSpec(self.namespace, self.name, spec_root,
                                  spec_specific, where_specific)
        return replica

    def print_backup_schedules(self) -> None:
        for schedule in self.backupSchedules:
            print(f"schedule={schedule}")

    def load_initdb(self, spec: dict) -> None:
        self.initDB = InitDB()
        self.initDB.parse(spec, "spec.initDB")

    def get_backup_profile(self, name: str) -> Optional[BackupProfile]:
        if self.backupProfiles:
            for profile in self.backupProfiles:
                if profile.name == name:
                    return profile
        return None

    def get_read_replica(self, name: str) -> Optional[ReadReplicaSpec]:
        if self.readReplicas:
            for rr in self.readReplicas:
                if rr.name ==  rr.cluster_name + '-' + name:
                    return rr
        return None

    def get_extra_router_volumes_no_cert(self, ca_and_tls) -> str:
        volumes = ""

        if not self.tlsUseSelfSigned:
            ca_file_name = ca_and_tls["CA"]
            volumes += f"""
- name: ssl-ca-data
  projected:
    sources:
    - secret:
        name: {self.tlsCASecretName}
"""

        return volumes

    def get_extra_router_volumes(self, ca_and_tls) -> str:
        volumes = self.get_extra_router_volumes_no_cert(ca_and_tls)

        if not self.tlsUseSelfSigned:
            volumes += f"""
- name: ssl-key-data
  projected:
    sources:
    - secret:
        name: {self.router.tlsSecretName}
"""

        return volumes

    @property
    def extra_router_volume_mounts_no_cert(self) -> str:
        mounts = []
        if not self.tlsUseSelfSigned:
            mounts.append(f"""
- mountPath: /router-ssl/ca/
  name: ssl-ca-data
""")

        return "\n".join(mounts)


    @property
    def extra_router_volume_mounts(self) -> str:
        mounts = []
        if not self.tlsUseSelfSigned:
            mounts.append(f"""
- mountPath: /router-ssl/ca/
  name: ssl-ca-data
- mountPath: /router-ssl/key/
  name: ssl-key-data
""")

        return "\n".join(mounts)

    @property
    def router_image(self) -> str:
        if self.router.version:
            version = self.router.version
        elif self.version:
            version = self.version
        else:
            version = config.DEFAULT_ROUTER_VERSION_TAG

        image = config.MYSQL_ROUTER_IMAGE if self.edition == Edition.community else config.MYSQL_ROUTER_EE_IMAGE

        return self.format_image(image, version)

    @property
    def router_image_pull_policy(self) -> str:
        return self.router.podSpec.get("imagePullPolicy", self.imagePullPolicy.value)

    @property
    def service_fqdn_template(self) -> Optional[str]:
        return self.serviceFqdnTemplate

    @property
    def headless_service_name(self) -> str:
        return f"{self.name}-instances"


class InnoDBCluster(K8sInterfaceObject):
    def __init__(self, cluster: Body) -> None:
        super().__init__()

        self.obj: Body = cluster
        self._parsed_spec: Optional[InnoDBClusterSpec] = None

    def __str__(self):
        return f"{self.namespace}/{self.name}"

    def __repr__(self):
        return f"<InnoDBCluster {self.name}>"

    @classmethod
    def _get(cls, ns: str, name: str) -> Body:
        try:
            ret = cast(Body,
                        api_customobj.get_namespaced_custom_object(
                            consts.GROUP, consts.VERSION, ns,
                            consts.INNODBCLUSTER_PLURAL, name))
        except ApiException as e:
            raise e

        return ret

    @classmethod
    def _patch(cls, ns: str, name: str, patch: dict) -> Body:
        return cast(Body, api_customobj.patch_namespaced_custom_object(
            consts.GROUP, consts.VERSION, ns,
            consts.INNODBCLUSTER_PLURAL, name, body=patch))

    @classmethod
    def _patch_json(cls, ns: str, name: str, patch: list[dict[str, Any]]) -> Body:
        # TODO: Recheck this helper whenever the kubernetes Python client is upgraded.
        # For kubernetes==35.0.0 the higher-level
        # CustomObjectsApi.patch_namespaced_custom_object() still hardcodes
        # merge-patch semantics for CRs and does not let us select
        # application/json-patch+json, so removing one finalizer entry safely
        # requires the lower-level call_api() path below. If the client grows a
        # supported high-level JSON Patch API in a newer version, prefer that
        # and drop this custom request code.
        header_params = {
            "Accept": "application/json",
            "Content-Type": "application/json-patch+json",
        }
        return cast(Body, api_customobj.api_client.call_api(
            "/apis/{group}/{version}/namespaces/{namespace}/{plural}/{name}",
            "PATCH",
            path_params={
                "group": consts.GROUP,
                "version": consts.VERSION,
                "namespace": ns,
                "plural": consts.INNODBCLUSTER_PLURAL,
                "name": name,
            },
            query_params=[],
            header_params=header_params,
            body=patch,
            response_type="object",
            auth_settings=["BearerToken"],
            _return_http_data_only=True,
        ))

    @classmethod
    def _patch_status(cls, ns: str, name: str, patch: dict) -> Body:
        return cast(Body, api_customobj.patch_namespaced_custom_object_status(
            consts.GROUP, consts.VERSION, ns,
            consts.INNODBCLUSTER_PLURAL, name, body=patch))

    @classmethod
    def read(cls, ns: str, name: str) -> 'InnoDBCluster':
        return InnoDBCluster(cls._get(ns, name))

    @property
    def metadata(self) -> dict:
        return self.obj["metadata"]

    @property
    def annotations(self) -> dict:
        return self.metadata["annotations"]

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

    def self_ref(self, field_path: Optional[str] = None) -> dict:
        ref = {
            "apiVersion": consts.API_VERSION,
            "kind": consts.INNODBCLUSTER_KIND,
            "name": self.name,
            "namespace": self.namespace,
            "resourceVersion": self.metadata["resourceVersion"],
            "uid": self.uid
        }
        if field_path:
            ref["fieldPath"] = field_path
        return ref

    @property
    def parsed_spec(self) -> InnoDBClusterSpec:
        if not self._parsed_spec:
            self.parse_spec()
            assert self._parsed_spec

        return self._parsed_spec

    def parse_spec(self) -> None:
        self._parsed_spec = InnoDBClusterSpec(self.namespace, self.name, self.spec)

    def validate_spec(self, logger) -> None:
        self.parsed_spec.validate(logger)
        # If CA and TLS secrets are missing in non self signed mode, this will throw
        self.get_ca_and_tls(raise_on_missing_secret=True)

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

    def get_routers(self) -> typing.List[str]:
        # get all pods that belong to the same container
        objects = cast(api_client.V1PodList, api_core.list_namespaced_pod(
            self.namespace, label_selector=f"component=mysqlrouter,mysql.oracle.com/cluster={self.name}"))

        pods = [o.metadata.name for o in objects.items]

        return pods

    def get_service(self) -> typing.Optional[api_client.V1Service]:
        try:
            return cast(api_client.V1Service,
                        api_core.read_namespaced_service(self.parsed_spec.headless_service_name, self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_read_replica_service(self, rr: ReadReplicaSpec) -> typing.Optional[api_client.V1Service]:
        try:
            return cast(api_client.V1Service,
                        api_core.read_namespaced_service(rr.headless_service_name, self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    # As of K8s 1.21 this is no more beta.
    # Thus, eventually this needs to be upgraded to V1PodDisruptionBudget and api_policy to PolicyV1Api
    def get_disruption_budget(self) -> typing.Optional[api_client.V1PodDisruptionBudget]:
        try:
            return cast(api_client.V1PodDisruptionBudget,
                        api_policy.read_namespaced_pod_disruption_budget(self.name + "-pdb", self.namespace))
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

    def get_read_replica_stateful_set(self, name: str) -> typing.Optional[api_client.V1StatefulSet]:
        try:
            return cast(api_client.V1StatefulSet,
                        api_apps.read_namespaced_stateful_set(name, self.namespace))
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

    def get_router_deployment(self) -> typing.Optional[api_client.V1Deployment]:
        try:
            return cast(api_client.V1Deployment,
                        api_apps.read_namespaced_deployment(self.name+"-router", self.namespace))
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_cron_job(self, schedule_name: str) -> typing.Callable:
        def get_cron_job_inner() -> typing.Optional[api_client.V1CronJob]:
            try:
                return cast(api_client.V1CronJob,
                            api_cron_job.read_namespaced_cron_job(schedule_name, self.namespace))
            except ApiException as e:
                if e.status == 404:
                    return None
                raise

        return get_cron_job_inner

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

    def get_ca_and_tls(self, raise_on_missing_secret = False) -> Dict:
        if self.parsed_spec.tlsUseSelfSigned:
            return {}

        ca_secret = None
        server_tls_secret = None
        ret = {}
        try:
            secret_name = self.parsed_spec.tlsCASecretName
            ca_secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(secret_name, self.namespace))

            secret_name = self.parsed_spec.tlsSecretName
            server_tls_secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(secret_name, self.namespace))

        except ApiException as e:
            print(f"Secret {secret_name} NOT found")
            if e.status == 404:
                if raise_on_missing_secret == True:
                    raise ApiSpecError(f'Secret "{secret_name}" NOT found')
                return None
            raise

        if "tls.crt" in server_tls_secret.data:
            ret["tls.crt"] = utils.b64decode(server_tls_secret.data["tls.crt"])
        if "tls.key" in server_tls_secret.data:
            ret["tls.key"] = utils.b64decode(server_tls_secret.data["tls.key"])

        ca_file_name = None
        if "ca.pem" in ca_secret.data:
            ca_file_name = "ca.pem"
        elif "ca.crt" in ca_secret.data:
            ca_file_name = "ca.crt"

        ret["CA"] = ca_file_name
        if ca_file_name:
            ret[ca_file_name] = utils.b64decode(ca_secret.data[ca_file_name])

        ret["ca_additional_mounts"] = []
        for item_key in list(set(ca_secret.data.keys()) - set(["tls.key", "tls.crt", ca_file_name])):
            ret[item_key] = utils.b64decode(ca_secret.data[item_key])
            ret["ca_additional_mounts"].append(item_key)

        # When using HELM a secret should exist, when using bare manifests the secret might
        # not exist (not mentioned directly or using the default name) and so it is not mounted
        # in the router pod, thus not passed to the router.
        try:
            router_tls_secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
                                     self.parsed_spec.router.tlsSecretName, self.namespace))
            ret["router_tls.crt"] = utils.b64decode(router_tls_secret.data["tls.crt"])
            ret["router_tls.key"] = utils.b64decode(router_tls_secret.data["tls.key"])

            ret["router_additional_mounts"] = []
            for item_key in list(set(router_tls_secret.data.keys()) - set(["tls.key", "tls.crt", ca_file_name])):
                ret[f"router_{item_key}"] = utils.b64decode(ca_secret.data[item_key])
                ret["router_additional_mounts"].append(item_key)

        except ApiException as e:
            if e.status != 404:
                raise

        return ret

    def get_tls_issuer_and_subject_rdns(self) -> Dict[str, str]:
        ca_and_tls = self.get_ca_and_tls()
        tls_cert = x509.load_pem_x509_certificate(ca_and_tls["tls.crt"].encode('ascii'))
        # See RF 4514
        # 2.1.  Converting the RDNSequence

        # If the RDNSequence is an empty sequence, the result is the empty or
        # zero-length string.
        #
        # Otherwise, the output consists of the string encodings of each
        # RelativeDistinguishedName in the RDNSequence (according to Section
        # 2.2), starting with the last element of the sequence and moving
        # backwards toward the first.

        # The encodings of adjoining RelativeDistinguishedNames are separated
        # by a comma (',' U+002C) character.
        # ---
        # The fields come in reverse order of what we need, so [::-1] reverses it again after
        # splitting and before joining with a `/`
        issuer_rdns = "/" + "/".join(tls_cert.issuer.rfc4514_string().split(",")[::-1])
        subject_rdns = "/" + "/".join(tls_cert.subject.rfc4514_string().split(",")[::-1])
        return {
           "issuer" : issuer_rdns,
           "subject" : subject_rdns
        }

    def get_admin_account(self) -> Tuple[str, str]:
        secrets = self.get_private_secrets()

        return (utils.b64decode(secrets.data["clusterAdminUsername"]),
                utils.b64decode(secrets.data["clusterAdminPassword"]))

    @classmethod
    def get_service_account_sidecar(cls, spec: AbstractServerSetSpec) -> api_client.V1ServiceAccount:
        return cast(api_client.V1ServiceAccount,
                    api_core.read_namespaced_service_account(spec.serviceAccountName, spec.namespace))

    @classmethod
    def get_service_account_switchover(cls, spec: AbstractServerSetSpec) -> api_client.V1ServiceAccount:
        return cast(api_client.V1ServiceAccount,
                    api_core.read_namespaced_service_account(spec.switchoverServiceAccountName, spec.namespace))

    @classmethod
    def get_role_binding_sidecar(cls, spec: AbstractServerSetSpec) -> api_client.V1RoleBinding:
        return cast(api_client.V1RoleBinding,
                    api_rbac.read_namespaced_role_binding(spec.sidecarRoleBindingName, spec.namespace))

    @classmethod
    def get_role_binding_switchover(cls, spec: AbstractServerSetSpec) -> api_client.V1RoleBinding:
        return cast(api_client.V1RoleBinding,
                    api_rbac.read_namespaced_role_binding(spec.switchoverRoleBindingName, spec.namespace))

    def delete_configmap(self, cm_name: str) -> typing.Optional[api_client.V1Status]:
        try:
            body = api_client.V1DeleteOptions(grace_period_seconds=0)
            status = cast(api_client.V1Status,
                          api_core.delete_namespaced_config_map(cm_name, self.namespace, body=body))
            return status
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_configmap(self, cm_name: str) -> typing.Optional[api_client.V1ConfigMap]:
        try:
            cm = cast(api_client.V1ConfigMap,
                      api_core.read_namespaced_config_map(cm_name, self.namespace))
            return cm
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_secret(self, s_name: str) -> typing.Optional[api_client.V1Secret]:
        try:
            cm = cast(api_client.V1Secret,
                      api_core.read_namespaced_secret(s_name, self.namespace))
            return cm
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    @classmethod
    def get_initconf(cls, spec: AbstractServerSetSpec) -> typing.Optional[api_client.V1ConfigMap]:
        try:
            return cast(api_client.V1ConfigMap,
                        api_core.read_namespaced_config_map(f"{spec.name}-initconf", spec.namespace))
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

    def get_metrics_monitor(self) :#-> typing.Optional[api_customobj.___]:
        try:
            return api_customobj.get_namespaced_custom_object(
                "monitoring.coreos.com", "v1", self.namespace, "servicemonitors", self.name)
        except ApiException as e:
            if e.status == 404:
                return None
            raise

    def get_service_monitor(self, name: str) :#-> typing.Optional[api_customobj.___]:
        try:
            return api_customobj.get_namespaced_custom_object(
                "monitoring.coreos.com", "v1", self.namespace, "servicemonitors", name)
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
        self.obj = self._patch_status(self.namespace, self.name, patch)

    def set_cluster_status(self, cluster_status) -> None:
        self._set_status_field("cluster", cluster_status)

    def get_cluster_status(self, field=None):  # TODO -> dict, remove field
        status = self._get_status_field("cluster")
        if status and field:
            return status.get(field)
        return status

    def set_status(self, status) -> None:
        obj = cast(dict, self._get(self.namespace, self.name))

        if "status" not in obj:
            obj["status"] = status
        else:
            obj["status"] = utils.merge_patch_object(obj["status"], status)
        self.obj = self._patch_status(self.namespace, self.name, obj)

    def update_cluster_fqdn(self) -> None:
        fqdn_template = fqdn.idc_service_fqdn_template(self.parsed_spec)
        patch = {
            "metadata": {
                "annotations": {
                    fqdn.FQDN_ANNOTATION_NAME: fqdn_template
                }
            }
        }
        self.obj = self._patch(self.namespace, self.name, patch)

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
        if self.annotations:
            info = self.annotations.get("mysql.oracle.com/cluster-info", None)
            if info:
                info = json.loads(info)
                if field:
                    return info.get(field)
                return info
        return None

    def set_create_time(self, time: datetime.datetime) -> None:
        self._set_status_field("createTime", time.replace(
            microsecond=0).isoformat()+"Z")

    def get_create_time(self) -> Optional[datetime.datetime]:
        dt = self._get_status_field("createTime")
        if dt:
            return datetime.datetime.fromisoformat(dt.rstrip("Z"))
        return None

    @property
    def ready(self) -> bool:
        return cast(bool, self.get_create_time())

    def set_last_known_quorum(self, members):
        # TODO
        pass

    def get_last_known_quorum(self):
        # TODO
        return None

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

    def _remove_finalizer(self, fin: str, cluster_body: Body = None) -> None:
        self.obj = remove_finalizer_with_json_patch(
            lambda: self._get(self.namespace, self.name),
            lambda patch: self._patch_json(self.namespace, self.name, patch),
            fin,
        )
        remove_finalizer_from_body(cluster_body, fin)

    def add_cluster_finalizer(self) -> None:
        self._add_finalizer("mysql.oracle.com/cluster")

    def remove_cluster_finalizer(self, cluster_body: dict = None) -> None:
        self._remove_finalizer("mysql.oracle.com/cluster", cluster_body)

    def set_operator_version(self, version: str) -> None:
        v = self.operator_version
        if v != version:
            patch = {"metadata": {"annotations": {"mysql.oracle.com/mysql-operator-version": version}}}

            # TODO store the current server/router version + timestamp
            # store previous versions in a version history log
            self.obj = self._patch(self.namespace, self.name, patch)

    @property
    def operator_version(self) -> Optional[str]:
        return self.metadata.get("mysql.oracle.com/mysql-operator-version")

    def set_current_version(self, version: str) -> None:
        v = self.status.get("version")
        if v != version:
            patch = {"status": {"version": version}}

            # TODO store the current server/router version + timestamp
            # store previous versions in a version history log
            self.obj = self._patch_status(self.namespace, self.name, patch)

    # TODO store last known majority and use it for diagnostics when there are
    # unconnectable pods

    def tls_has_crl(self) -> bool:
        if self.parsed_spec.tlsUseSelfSigned:
            return False
        # XXX TODO fixme
        return False

    def router_tls_exists(self) -> bool:
        if self.parsed_spec.tlsUseSelfSigned:
            return False
        try:
            api_core.read_namespaced_secret(self.parsed_spec.router.tlsSecretName, self.namespace)
        except ApiException as e:
            if e.status == 404:
                return False
            raise
        return True

    def log_cluster_info(self, logger: Logger) -> None:
        logger.info(f"InnoDB Cluster {self.namespace}/{self.name} Edition({self.parsed_spec.edition}) Edition")
        logger.info(f"\tServer Image:\t{self.parsed_spec.mysql_image} / {self.parsed_spec.mysql_image_pull_policy}")
        logger.info(f"\tRouter Image:\t{self.parsed_spec.router_image} / {self.parsed_spec.router_image_pull_policy}")
        logger.info(f"\tSidecar Image:\t{self.parsed_spec.operator_image} / {self.parsed_spec.operator_image_pull_policy}")
        logger.info(f"\tImagePullPolicy:\t{self.parsed_spec.imagePullPolicy}")
        logger.info(f"\tImageRepository:\t{self.parsed_spec.imageRepository}")
        logger.info(f"\tBase ServerId:\t{self.parsed_spec.baseServerId}")
        logger.info(f"\tLog collection:\t{self.parsed_spec.logs.collect}")
        logger.info(f"\tRouter instances:\t{self.parsed_spec.router.instances}")
        if self.parsed_spec.logs.collect:
            logger.info(f"\tLog collector image:\t{self.parsed_spec.logs.collector.image_name}")
        if self.parsed_spec.metrics:
            logger.info(f"\tMetrics enabled:\t{self.parsed_spec.metrics.enable}")
            logger.info(f"\tMetrics monitor:\t{self.parsed_spec.metrics.monitor}")
            if self.parsed_spec.metrics.enable:
                logger.info(f"\tMetrics image:\t{self.parsed_spec.metrics.image}")
        logger.info(f"\tKeyring: \t\t{self.parsed_spec.keyring.name}")
        logger.info(f"\tBackup profiles:\t{len(self.parsed_spec.backupProfiles)}")
        logger.info(f"\tBackup schedules:\t{len(self.parsed_spec.backupSchedules)}")
        self.log_tls_info(logger)

    def log_tls_info(self, logger: Logger) -> None:
        logger.info(f"\tServer.TLS.useSelfSigned:\t{self.parsed_spec.tlsUseSelfSigned}")
        if not self.parsed_spec.tlsUseSelfSigned:
            logger.info(f"\tServer.TLS.tlsCASecretName:\t{self.parsed_spec.tlsCASecretName}")
            logger.info(f"\tServer.TLS.tlsSecretName:\t{self.parsed_spec.tlsSecretName}")
            logger.info(f"\tTLS.keys                :\t{list(self.get_ca_and_tls().keys())}")
            router_tls_exists = self.router_tls_exists()
            logger.info(f"\tRouter.TLS exists       :\t{router_tls_exists}")
            if router_tls_exists:
                logger.info(f"\tRouter.TLS.tlsSecretName:\t{self.parsed_spec.router.tlsSecretName}")
def get_all_clusters(ns: Union[None, str, list[str]] = None) -> typing.List[InnoDBCluster]:
    if ns is None:
        try:
            resp = api_customobj.list_cluster_custom_object(
                consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL
            )
            return [InnoDBCluster(item) for item in resp["items"]]
        except ApiException as e:
            if e.status == 404:
                return []
            raise

    if isinstance(ns, str):
        ns = [ns] if ns else []

    ret = []
    for n in dict.fromkeys(ns):
        try:
            resp = api_customobj.list_namespaced_custom_object(
                consts.GROUP, consts.VERSION, n, consts.INNODBCLUSTER_PLURAL
            )
            ret += [InnoDBCluster(item) for item in resp["items"]]
        except ApiException as e:
            if e.status == 404:
                continue
            raise
    return ret


class MySQLPod(K8sInterfaceObject):
    logger: Optional[Logger] = None

    def __init__(self, pod: client.V1Pod):
        super().__init__()

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
    def from_json(cls, pod: Body) -> 'MySQLPod':
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

    def self_ref(self, field_path: Optional[str] = None) -> dict:
        ref = {
            "apiVersion": self.pod.api_version,
            "kind": self.pod.kind,
            "name": self.name,
            "namespace": self.namespace,
            "resourceVersion": self.metadata.resource_version,
            "uid": self.metadata.uid
        }
        if field_path:
            ref["fieldPath"] = field_path
        return ref

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
        return self.pod.metadata.labels["mysql.oracle.com/cluster"]

    @property
    def containers(self) -> list[str]:
        return [container.name for container in self.spec.containers]

    @property
    def instance_type(self) -> str:
        if "mysql.oracle.com/instance-type" in self.pod.metadata.labels:
            return self.pod.metadata.labels["mysql.oracle.com/instance-type"]
        else:
            # With old clusters the label may be missing
            return "group-member"

    @property
    def read_replica_name(self) -> str:
        return self.pod.metadata.labels["mysql.oracle.com/read-replica"]

    @property
    def address(self) -> str:
        return self.name+"."+cast(str, self.spec.subdomain)

    @property
    def address_fqdn(self) -> str:
        return fqdn.pod_fqdn(self, self.logger)

    @property
    def pod_ip_address(self) -> str:
        return self.pod.status.pod_ip

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

    def check_container_status_any_reason(self, container_names: Optional[list[str]], reasons: list[str]) -> Optional[bool]:
        if self.status.phase in ["Running", "Pending"] and self.status.container_statuses:
            c_names = container_names if (container_names is not None and len(container_names)) else self.containers
            #container_status: Optional[api_client.V1ContainerStatus] = None
            for container_status in self.status.container_statuses:
                cstatus = cast(api_client.V1ContainerStatus, container_status)
                if (cstatus.name in c_names and cstatus.state and cstatus.state.waiting):
                    if cstatus.state.waiting.reason in reasons:
                        return True
            return False
        return None

    def get_container_status_reason(self, container_name: str) -> typing.Optional[Union[str, bool]]:
        if self.status.phase in ["Running", "Pending"] and self.status.container_statuses:
            for container_status in self.status.container_statuses:
                cstatus = cast(api_client.V1ContainerStatus, container_status)
                if (cstatus.name == container_name and cstatus.state and cstatus.state.waiting):
                    return cstatus.state.waiting.reason
            return False
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
            info = self.metadata.annotations.get("mysql.oracle.com/membership-info", None)
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

    def remove_member_finalizer(self, pod_body: Body = None) -> None:
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

    def _remove_finalizer(self, fin: str, pod_body: Body = None) -> None:
        patch = {"metadata": {"$deleteFromPrimitiveList/finalizers": [fin]}}
        self.obj = api_core.patch_namespaced_pod(
            self.name, self.namespace, body=patch)

        if pod_body:
            # modify the JSON data used internally by kopf to update its finalizer list
            if fin in pod_body["metadata"]["finalizers"]:
                pod_body["metadata"]["finalizers"].remove(fin)
