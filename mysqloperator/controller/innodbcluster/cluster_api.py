# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from enum import Enum
import typing
from typing import Optional, List, Tuple, Dict, cast, overload

from kopf._cogs.structs.bodies import Body
from logging import getLogger
from ..k8sobject import K8sInterfaceObject
from .. import utils, config, consts
from ..backup.backup_api import BackupProfile, BackupSchedule
from ..storage_api import StorageSpec
from ..api_utils import Edition, dget_bool, dget_dict, dget_enum, dget_str, dget_int, dget_list, ApiSpecError, ImagePullPolicy
from ..kubeutils import api_core, api_apps, api_customobj, api_policy, api_rbac, api_batch, api_cron_job
from ..kubeutils import client as api_client, ApiException
from ..kubeutils import k8s_cluster_domain
from logging import Logger
import json
import yaml
import datetime
from kubernetes import client


MAX_CLUSTER_NAME_LEN = 28

def escape_value_for_mycnf(value: str) -> str:
    return '"'+value.replace("\\", "\\\\").replace("\"", "\\\"")+'"'

class SecretData:
    secret_name: Optional[str] = None
    key: Optional[str] = None

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
    monitor_spec: dict

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


class CloneInitDBSpec:
    uri: str = ""
    password_secret_name: Optional[str] = None
    root_user: Optional[str] = None

    def parse(self, spec: dict, prefix: str) -> None:
        self.uri = dget_str(spec, "donorUrl", prefix)  # TODO make mandatory
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


class KeyringConfigStorage(Enum):
    CONFIGMAP = 1
    SECRET = 2

class KeyringFileSpec:
    fileName: Optional[str] = None
    readOnly: Optional[bool] = False
    storage: Optional[dict] = None

    def __init__(self, namespace: str, global_manifest_name: str, component_config_configmap_name: str, keyring_mount_path: str):
        self.namespace = namespace
        self.global_manifest_name = global_manifest_name
        self.component_config_configmap_name = component_config_configmap_name
        self.keyring_mount_path = keyring_mount_path

    def parse(self, spec: dict, prefix: str) -> None:
        self.fileName = dget_str(spec, "fileName", prefix)
        self.readOnly = dget_bool(spec, "readOnly", prefix, default_value=False)
        self.storage = dget_dict(spec, "storage", prefix)

    @property
    def is_component(self) -> bool:
        return True

    def add_to_sts_spec(self, statefulset: dict) -> None:
        self.add_conf_to_sts_spec(statefulset)
        self.add_storage_to_sts_spec(statefulset)


    def add_conf_to_sts_spec(self, statefulset: dict) -> None:
        cm_mount_name = "keyringfile-conf"
        mounts = f"""
- name: {cm_mount_name}
  mountPath: "/usr/lib64/mysql/plugin/{self.component_manifest_name}"
  subPath: "{self.component_manifest_name}"     #should be the same as the volume.items.path
"""

        volumes = f"""
- name: {cm_mount_name}
  configMap:
    name: {self.component_config_configmap_name}
    items:
    - key: "{self.component_manifest_name}"
      path: "{self.component_manifest_name}"
"""

        patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
{utils.indent(mounts, 4)}
  containers:
  - name: mysql
    volumeMounts:
{utils.indent(mounts, 4)}
  volumes:
{utils.indent(volumes, 2)}
"""
        utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))

    def add_storage_to_sts_spec(self, statefulset: dict) -> None:
        if not self.storage:
            return

        storage_mount_name = "keyringfile-storage"
        mounts = f"""
- name: {storage_mount_name}
  mountPath: {self.keyring_mount_path}

"""

        patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
{utils.indent(mounts, 6)}
  containers:
  - name: mysql
    volumeMounts:
{utils.indent(mounts, 6)}
"""
        utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))

        statefulset["spec"]["template"]["spec"]["volumes"].append({"name" : storage_mount_name, **self.storage})


    def add_to_global_manifest(self, manifest: dict) -> dict:
        component_name = "file://component_keyring_file"
        if not manifest.get("components"):
            manifest["components"] = f"{component_name}"
        else:
            manifest["components"] = manifest["components"] + f",{component_name}"  # no space allowed after comma

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if storage_type == KeyringConfigStorage.CONFIGMAP:
            data[self.component_manifest_name] = {
                "path": self.fileName,
                "read_only": self.readOnly,
            }

    @property
    def component_manifest_name(self) -> str:
        return "component_keyring_file.cnf"


class KeyringEncryptedFileSpec:
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

    @property
    def is_component(self) -> bool:
        return True

    def add_to_sts_spec(self, statefulset: dict) -> None:
        self.add_conf_to_sts_spec(statefulset)
        self.add_storage_to_sts_spec(statefulset)


    def add_conf_to_sts_spec(self, statefulset: dict) -> None:
        cm_mount_name = "keyringencfile-conf"
        mounts = f"""
- name: {cm_mount_name}
  mountPath: "/usr/lib64/mysql/plugin/{self.component_manifest_name}"
  subPath: "{self.component_manifest_name}"     #should be the same as the volume.items.path
"""

        volumes = f"""
- name: {cm_mount_name}
  secret:
    secretName: {self.component_config_configmap_name}
    items:
    - key: "{self.component_manifest_name}"
      path: "{self.component_manifest_name}"
"""

        patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
{utils.indent(mounts, 4)}
  containers:
  - name: mysql
    volumeMounts:
{utils.indent(mounts, 4)}
  volumes:
{utils.indent(volumes, 2)}
"""
        utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))

    def add_storage_to_sts_spec(self, statefulset: dict) -> None:
        if not self.storage:
            return

        storage_mount_name = "keyringencfile-storage"
        mounts = f"""
- name: {storage_mount_name}
  mountPath: {self.keyring_mount_path}

"""

        patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
{utils.indent(mounts, 6)}
  containers:
  - name: mysql
    volumeMounts:
{utils.indent(mounts, 6)}
"""
        utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))

        statefulset["spec"]["template"]["spec"]["volumes"].append({"name" : storage_mount_name, **self.storage})


    def add_to_global_manifest(self, manifest: dict) -> None:
        component_name = "file://component_keyring_encrypted_file"
        if not manifest.get("components"):
            manifest["components"] = f"{component_name}"
        else:
            manifest["components"] = manifest["components"] + f",{component_name}"  # no space allowed after comma

    def add_component_manifest(self, data: dict, storage_type: KeyringConfigStorage) -> None:
        if storage_type == KeyringConfigStorage.SECRET:
            data[self.component_manifest_name] = {
                "path": self.fileName,
                "read_only": self.readOnly,
                "password": self.password,
            }

    @property
    def component_manifest_name(self) -> str:
        return "component_keyring_encrypted_file.cnf"


class KeyringOciSpec:
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

    @property
    def is_component(self) -> bool:
        return False

    @property
    def component_manifest_name(self) -> str:
        raise Exception("NOT IMPLEMENTED YET")

    @property
    def component_manifest(self) -> dict:
        raise Exception("NOT IMPLEMENTED YET")

    def add_to_sts_spec(self, statefulset: dict):
        patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
    - name: ocikey
      mountPath: /.oci
  containers:
  - name: mysql
    volumeMounts:
    - name: ocikey
      mountPath: /.oci
  volumes:
  - name: ocikey
    secret:
      secretName: {self.keySecret}
"""
        utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))

        if self.caCertificate:
            patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
    - name: oci-keyring-ca
      mountPath: /etc/mysql-keyring-ca
  containers:
  - name: mysql
    volumeMounts:
    - name: oci-keyring-ca
      mountPath: /etc/mysql-keyring-ca
  volumes:
  - name: oci-keyring-ca
    secret:
      secretName: {self.caCertificate}
"""
            utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))

    def add_to_initconf(self, configmap: dict):
        # TODO: move to component ....
        esc = escape_value_for_mycnf

        mycnf = f"""
# Do not edit.
[mysqld]
early-plugin-load=keyring_oci.so
keyring_oci_user={esc(self.user)}
keyring_oci_tenancy={esc(self.tenancy)}
keyring_oci_compartment={esc(self.compartment)}
keyring_oci_virtual_vault={esc(self.virtualVault)}
keyring_oci_master_key={esc(self.masterKey)}
keyring_oci_encryption_endpoint={esc(self.endpointEncryption)}
keyring_oci_management_endpoint={esc(self.endpointManagement)}
keyring_oci_vaults_endpoint={esc(self.endpointVaults)}
keyring_oci_secrets_endpoint={esc(self.endpointSecrets)}
keyring_oci_key_file=/.oci/privatekey
keyring_oci_key_fingerprint={esc(self.keyFingerprint)}
        """

        if self.caCertificate:
            mycnf = f"{mycnf}\nkeyring_oci_ca_certificate=/etc/mysql-keyring-ca/certificate"

        configmap["data"]["03-keyring-oci.cnf"] = mycnf


class KeyringSpec:
    keyringFile: Optional[KeyringFileSpec] = None
    keyringEncryptedFile: Optional[KeyringEncryptedFileSpec] = None
    keyringOci: Optional[KeyringOciSpec] = None
    global_manifest_name: str = 'mysqld.my'
    keyring_mount_path: str = '/keyring'

    def __init__(self, namespace: str, cluster_name: str):
        self.namespace = namespace
        self.cluster_name = cluster_name

    def parse(self, spec: dict, prefix: str) -> None:
        krFile = dget_dict(spec, "file", "spec.keyring", {})
        krEncryptedFile = dget_dict(spec, "encryptedFile", "spec.keyring", {})
        krOci = dget_dict(spec, "oci", "spec.keyring", {})
        if len([x for x in [krFile, krEncryptedFile, krOci] if x]) > 1:
            raise ApiSpecError(
                "Only one of file, encryptedFile or oci may be specified in spec.keyring")
        if not krFile and not krEncryptedFile and not krOci:
            raise ApiSpecError(
                "One of file, encryptedFile or oci must be specified in spec.keyring")

        if krFile:
            self.keyringFile = KeyringFileSpec(self.namespace, self.global_manifest_name, self.component_config_configmap_name, self.keyring_mount_path)
            self.keyringFile.parse(krFile, "spec.keyring.file")
        elif krEncryptedFile:
            self.keyringEncryptedFile = KeyringEncryptedFileSpec(self.namespace, self.global_manifest_name, self.component_config_configmap_name, self.keyring_mount_path)
            self.keyringEncryptedFile.parse(krEncryptedFile, "spec.keyring.encryptedFile")
        elif krOci:
            self.keyringOci = KeyringOciSpec()
            self.keyringOci.parse(krOci, "spec.keyring.oci")

    @property
    def is_component(self) -> bool:
        if self.keyringFile:
            return self.keyringFile.is_component
        elif self.keyringEncryptedFile:
            return self.keyringEncryptedFile.is_component
        elif self.keyringOci:
            return self.keyringOci.is_component
        else:
            return False
            #raise Exception("NEEDS IMPLEMENTATION")

    @property
    def component_config_configmap_name(self) -> str:
        return f"{self.cluster_name}-componentconf"

    @property
    def component_config_secret_name(self) -> str:
        return f"{self.cluster_name}-componentconf"

    def get_component_config_configmap_manifest(self) -> dict:
        if not self.is_component:
            raise Exception("NOT IMPLEMENTED YET")

        data = {
            self.global_manifest_name : {}
        }
        if self.keyringFile:
            self.keyringFile.add_to_global_manifest(data[self.global_manifest_name])
            self.keyringFile.add_component_manifest(data, KeyringConfigStorage.CONFIGMAP)
        elif self.keyringEncryptedFile:
            self.keyringEncryptedFile.add_to_global_manifest(data[self.global_manifest_name])
            self.keyringEncryptedFile.add_component_manifest(data, KeyringConfigStorage.CONFIGMAP)
        else:
            raise Exception("NEEDS IMPLEMENTATION")

        cm =  {
            'apiVersion' : "v1",
            'kind': 'ConfigMap',
            'metadata': {
                'name': self.component_config_configmap_name
            },
            'data' : { k: utils.dict_to_json_string(data[k]) for k in data }
        }
        return cm


    def get_component_config_secret_manifest(self) -> Optional[Dict]:
        if not self.is_component:
            raise Exception("NOT IMPLEMENTED YET")

        data = {
        }
        if self.keyringFile:
            self.keyringFile.add_component_manifest(data, KeyringConfigStorage.SECRET)
        elif self.keyringEncryptedFile:
            self.keyringEncryptedFile.add_component_manifest(data, KeyringConfigStorage.SECRET)
        else:
            raise Exception("NEEDS IMPLEMENTATION")

        if len(data) == 0:
            return None

        cm =  {
            'apiVersion' : "v1",
            'kind': 'Secret',
            'metadata': {
                'name': self.component_config_secret_name
            },
            'data' : { k: utils.b64encode(utils.dict_to_json_string(data[k])) for k in data }
        }
        return cm


    def add_to_sts_spec_component_global_manifest(self, statefulset: dict):
        mounts = f"""
- name: globalcomponentconf
  mountPath: /usr/sbin/{self.global_manifest_name}
  subPath: {self.global_manifest_name}                #should be the same as the volume.items.path
"""

        volumes = f"""
- name: globalcomponentconf
  configMap:
    name: {self.component_config_configmap_name}
    items:
    - key: {self.global_manifest_name}
      path: {self.global_manifest_name}
"""

        patch = f"""
spec:
  initContainers:
  - name: initmysql
    volumeMounts:
{utils.indent(mounts, 6)}
  containers:
  - name: mysql
    volumeMounts:
{utils.indent(mounts, 6)}
  volumes:
{utils.indent(volumes, 4)}
"""
        utils.merge_patch_object(statefulset["spec"]["template"], yaml.safe_load(patch))


    def add_to_sts_spec(self, statefulset: dict):
        if self.is_component:
            self.add_to_sts_spec_component_global_manifest(statefulset)

        if self.keyringFile:
             self.keyringFile.add_to_sts_spec(statefulset)
        elif self.keyringEncryptedFile:
            self.keyringEncryptedFile.add_to_sts_spec(statefulset)
        elif self.keyringOci:
            self.keyringOci.add_to_sts_spec(statefulset)

    def add_to_initconf(self, configmap: dict):
        if self.keyringOci:
            self.keyringOci.add_to_initconf(configmap)


class RouterSpec:
    # number of Router instances (optional)
    instances: int = 1

    # Router version, if user wants to override it (latest by default)
    version: str = None # config.DEFAULT_ROUTER_VERSION_TAG

    podSpec: dict = {}
    podAnnotations: Optional[dict] = None
    podLabels: Optional[dict] = None

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


class InnoDBClusterSpec:
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

    # number of MySQL instances (required)
    instances: int = 1
    # base value for server_id
    baseServerId: int
    # override volumeClaimTemplates for datadir in MySQL pods (optional)
    datadirVolumeClaimTemplate = None
    # additional MySQL configuration options
    mycnf: str = ""
    # override pod template for MySQL (optional)
    podSpec: dict = {}
    podAnnotations: Optional[dict] = None
    podLabels: Optional[dict] = None

    # Initialize DB
    initDB: Optional[InitDB] = None

    router: RouterSpec = RouterSpec()

    metrics: Optional[MetriscSpec] = None

    # TODO resource allocation for server, router and sidecar
    # TODO recommendation is that sidecar has 500MB RAM if MEB is used

    # Backup info
    backupProfiles: List[BackupProfile] = []

    # (currently) non-configurable constants
    mysql_port: int = 3306
    mysql_xport: int = 33060
    mysql_grport: int = 33061
    mysql_metrics_port: int = 9104

    router_rwport: int = 6446
    router_roport: int = 6447
    router_rwxport: int = 6448
    router_roxport: int = 6449
    router_httpport: int = 8443

    def __init__(self, namespace: str, name: str, spec: dict):
        self.namespace = namespace
        self.name = name
        self.backupSchedules: List[BackupSchedule] = []
        self.load(spec)

    def load(self, spec: dict) -> None:
        self.secretName = dget_str(spec, "secretName", "spec")

        if "tlsCASecretName" in spec:
            self.tlsCASecretName = dget_str(spec, "tlsCASecretName", "spec")
        else:
            self.tlsCASecretName = f"{self.name}-ca"

        if "tlsSecretName" in spec:
            self.tlsSecretName = dget_str(spec, "tlsSecretName", "spec")
        else:
            self.tlsSecretName = f"{self.name}-tls"

        if "tlsUseSelfSigned" in spec:
            self.tlsUseSelfSigned = dget_bool(spec, "tlsUseSelfSigned", "spec")

        self.instances = dget_int(spec, "instances", "spec")
        if "version" in spec:
            self.version = dget_str(spec, "version", "spec")

        if "edition" in spec:
            self.edition = dget_enum(
                spec, "edition", "spec", default_value=config.OPERATOR_EDITION,
                enum_type=Edition)

            if "imageRepository" not in spec:
                self.imageRepository = config.DEFAULT_IMAGE_REPOSITORY

        if "imagePullPolicy" in spec:
            self.imagePullPolicy = dget_enum(
                spec, "imagePullPolicy", "spec",
                default_value=config.default_image_pull_policy,
                enum_type=ImagePullPolicy)

        if "imagePullSecrets" in spec:
            self.imagePullSecrets = dget_list(
                spec, "imagePullSecrets", "spec", content_type=dict)

        if "serviceAccountName" in spec:
            self.serviceAccountName = dget_str(spec, "serviceAccountName", "spec")

        if "imageRepository" in spec:
            self.imageRepository = dget_str(spec, "imageRepository", "spec")

        if "podSpec" in spec:  # TODO - replace with something more specific
            self.podSpec = dget_dict(spec, "podSpec", "spec")

        if "podAnnotations" in spec:
            self.podAnnotations = dget_dict(spec, "podAnnotations", "spec")

        if "podLabels" in spec:
            self.podLabels = dget_dict(spec, "podLabels", "spec")

        if "datadirVolumeClaimTemplate" in spec:
            self.datadirVolumeClaimTemplate = spec.get("datadirVolumeClaimTemplate")

        self.keyring = KeyringSpec(self.namespace, self.name)
        if "keyring" in spec:
            self.keyring.parse(dget_dict(spec, "keyring", "spec"), "spec.keyring")

        if "mycnf" in spec:
            self.mycnf = dget_str(spec, "mycnf", "spec")

        # Router Options
        if "router" in spec:
            self.router = RouterSpec()
            self.router.parse(dget_dict(spec, "router", "spec"), "spec.router")
        else:
            self.router = RouterSpec()

        if not self.router.tlsSecretName:
            self.router.tlsSecretName = f"{self.name}-router-tls"

        if "metrics" in spec:
            self.metrics = MetriscSpec()
            self.metrics.parse(dget_dict(spec, "metrics", "spec"), "spec.metrics")

        # Initialization Options
        if "initDB" in spec:
            self.load_initdb(dget_dict(spec, "initDB", "spec"))

        # TODO keep a list of base_server_id in the operator to keep things globally unique?
        if "baseServerId" in spec:
            self.baseServerId = dget_int(spec, "baseServerId", "spec")


        self.backupProfiles = []
        if "backupProfiles" in spec:
            profiles = dget_list(spec, "backupProfiles", "spec", [], content_type=dict)
            for profile in profiles:
                self.backupProfiles.append(self.parse_backup_profile(profile))


        self.backupSchedules = []
        if "backupSchedules" in spec:
            schedules = dget_list(spec, "backupSchedules", "spec", [], content_type=dict)
            for schedule in schedules:
                self.backupSchedules.append(self.parse_backup_schedule(schedule))

    def parse_backup_profile(self, spec: dict) -> BackupProfile:
        profile = BackupProfile()
        profile.parse(spec, "spec.backupProfiles")
        return profile

    def parse_backup_schedule(self, spec: dict) -> BackupSchedule:
        schedule = BackupSchedule(self)
        schedule.parse(spec, "spec.backupSchedules")
        return schedule

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

        # check that the secret exists and it contains rootPassword
        if self.secretName:  # TODO
            pass

        # validate podSpec through the Kubernetes API
        if self.podSpec:
            pass

        if self.tlsSecretName and not self.tlsCASecretName:
            logger.info("spec.tlsSecretName is set but will be ignored because self.tlsCASecretName is not set")

        if self.mycnf:
            if "[mysqld]" not in self.mycnf:
                logger.warning(
                    "spec.mycnf data does not contain a [mysqld] line")

        # TODO ensure that if version is set, then image and routerImage are not
        # TODO should we support upgrading router only?

        # validate version
        if self.version:
            # note: format of the version string is defined in the CRD
            [valid_version, version_error] = utils.version_in_range(self.version)

            if not valid_version:
                raise ApiSpecError(version_error)

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
    def router_image_pull_policy(self) -> str:
        return self.router.podSpec.get("imagePullPolicy", self.imagePullPolicy.value)

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

    @property
    def extra_volumes(self) -> str:
        volumes = []
        same_secret = self.tlsCASecretName == self.tlsSecretName

        if not self.tlsUseSelfSigned:
            volume = f"""
- name: ssldata
  projected:
    sources:
    - secret:
        name: {self.tlsSecretName}
"""
            if not same_secret:
                volume += f"""
    - secret:
        name: {self.tlsCASecretName}
"""

            volumes.append(volume)

        return "\n".join(volumes)

    @property
    def extra_volume_mounts(self) -> str:
        mounts = []
        if not self.tlsUseSelfSigned:
            mounts.append(f"""
- mountPath: /etc/mysql-ssl
  name: ssldata
""")
        return "\n".join(mounts)

    @property
    def extra_sidecar_volume_mounts(self) -> str:
        mounts = []
        if not self.tlsUseSelfSigned:
            mounts.append("""
- mountPath: /etc/mysql-ssl
  name: ssldata
""")
        return "\n".join(mounts)

    @property
    def extra_router_volumes_no_cert(self) -> str:
        volumes = []

        if not self.tlsUseSelfSigned:
            volumes.append(f"""
- name: ssl-ca-data
  projected:
    sources:
    - secret:
        name: {self.tlsCASecretName}
""")

        return "\n".join(volumes)

    @property
    def extra_router_volumes(self) -> str:
        volumes = []

        if not self.tlsUseSelfSigned:
            volumes.append(f"""
- name: ssl-ca-data
  projected:
    sources:
    - secret:
        name: {self.tlsCASecretName}
- name: ssl-key-data
  projected:
    sources:
    - secret:
        name: {self.router.tlsSecretName}
""")

        return "\n".join(volumes)

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
    def metrics_sidecar(self) -> str:
        if not self.metrics or not self.metrics.enable:
            return ""

        metrics = self.metrics

        mounts = ""
        options = []

        if metrics.web_config:
            options += ["--web.config.file=/config/web.config"]
            mounts += """
- name: metrics-web-config
  mountPath: /config
  readOnly: true
"""

        if metrics.tls_secret:
            mounts += """
- name: metrics-tls
  mountPath: /tls
  readOnly: true
"""

        options_yaml = ""
        if options:
            options_yaml = yaml.dump(options)

        return f"""
- name: metrics
  args:
{utils.indent(options_yaml, 4)}
  image: {metrics.image}
  imagePullPolicy: {self.sidecar_image_pull_policy}
  securityContext:
      # These can't go to spec.template.spec.securityContext
      # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodTemplateSpec / https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSpec
      # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#PodSecurityContext - for pods (top level)
      # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#Container
      # See: https://pkg.go.dev/k8s.io/api@v0.26.1/core/v1#SecurityContext - for containers
      allowPrivilegeEscalation: false
      privileged: false
      readOnlyRootFilesystem: true
      capabilities:
      drop:
      - ALL
      # We must use same user id as auth_socket expects
      runAsUser: 2
      runAsGroup: 27
  env:
  - name: DATA_SOURCE_NAME
    value: {metrics.dbuser_name}:@unix(/var/run/mysqld/mysql.sock)/
  ports:
  - containerPort: {self.mysql_metrics_port}
    name: metrics
    protocol: TCP
  volumeMounts:
  - name: rundir
    mountPath: /var/run/mysqld
{utils.indent(mounts, 2)}
"""


    @property
    def metrics_volumes(self) -> str:
        volumes = ""

        if self.metrics and self.metrics.web_config:
            volumes += f"""
- name: metrics-web-config
  configMap:
    name: {self.metrics.web_config}
"""

        if self.metrics and self.metrics.tls_secret:
            volumes += f"""
- name: metrics-tls
  secret:
    name: {self.metrics.tls_secret}
"""


        return volumes

    @property
    def metrics_service_port(self) -> str:
        if not self.metrics or not self.metrics.enable:
            return ""

        return f"""
- name: metrics
  port: {self.mysql_metrics_port}
  targetPort: {self.mysql_metrics_port}
"""

    @property
    def image_pull_secrets(self) -> str:
        if self.imagePullSecrets:
            return f"imagePullSecrets:\n{yaml.safe_dump(self.imagePullSecrets)}"
        return ""

    @property
    def service_account_name(self) -> str:
        saName = f"{self.serviceAccountName}" if self.serviceAccountName else f"{self.name}-sidecar-sa"
        return f"serviceAccountName: {saName}"



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

    def get_ca_and_tls(self) -> Dict:
        if self.parsed_spec.tlsUseSelfSigned:
            return {}

        ca_secret = None
        server_tls_secret = None
        same_secret_for_ca_and_tls = False
        ret = {}
        try:
            server_tls_secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
                                     self.parsed_spec.tlsSecretName, self.namespace))

        except ApiException as e:
            if e.status == 404:
                return {}
            raise

        if "tls.crt" in server_tls_secret.data:
            ret["tls.crt"] = utils.b64decode(server_tls_secret.data["tls.crt"])
        if "tls.key" in server_tls_secret.data:
            ret["tls.key"] = utils.b64decode(server_tls_secret.data["tls.key"])

        if self.parsed_spec.tlsSecretName == self.parsed_spec.tlsCASecretName:
            ca_secret = server_tls_secret
            same_secret_for_ca_and_tls = True
        else:
            try:
                ca_secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
                                 self.parsed_spec.tlsCASecretName, self.namespace))
            except ApiException as e:
                if e.status == 404:
                    return ret
                raise

        ca_file_name = None
        if "ca.pem" in ca_secret.data:
            ca_file_name = "ca.pem"
        elif "ca.crt" in ca_secret.data:
            ca_file_name = "ca.crt"

        ret["CA"] = ca_file_name
        if ca_file_name:
            ret[ca_file_name] = utils.b64decode(ca_secret.data[ca_file_name])
            ret['same_secret_for_ca_and_tls'] = same_secret_for_ca_and_tls

        # When using HELM a secret should exist, when using bare manifests the secret might
        # not exist (not mentioned directly or using the default name) and so it is not mounted
        # in the router pod, thus not passed to the router.
        try:
            router_tls_secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(
                                     self.parsed_spec.router.tlsSecretName, self.namespace))
            ret["router_tls.crt"] = utils.b64decode(router_tls_secret.data["tls.crt"])
            ret["router_tls.key"] = utils.b64decode(router_tls_secret.data["tls.key"])
        except ApiException as e:
            if e.status != 404:
                raise

        return ret

    def get_admin_account(self) -> Tuple[str, str]:
        secrets = self.get_private_secrets()

        return (utils.b64decode(secrets.data["clusterAdminUsername"]),
                utils.b64decode(secrets.data["clusterAdminPassword"]))

    def get_service_account(self) -> api_client.V1ServiceAccount:
        return cast(api_client.V1ServiceAccount,
                    api_core.read_namespaced_service_account(f"{self.name}-sidecar-sa", self.namespace))

    def get_role_binding(self) -> api_client.V1RoleBinding:
        return cast(api_client.V1RoleBinding,
                    api_rbac.read_namespaced_role_binding(f"{self.name}-sidecar-rb", self.namespace))


    def get_configmap(self, cm_name: str) -> typing.Optional[api_client.V1ConfigMap]:
        try:
            cm = cast(api_client.V1ConfigMap,
                      api_core.read_namespaced_config_map(f"{cm_name}", self.namespace))
            getLogger().info(f"ConfigMap {cm_name} found in {self.namespace}")
            return cm
        except ApiException as e:
            getLogger().info(f"ConfigMap {cm_name} NOT found in {self.namespace}")
            if e.status == 404:
                return None
            raise

    def get_secret(self, s_name: str) -> typing.Optional[api_client.V1Secret]:
        try:
            cm = cast(api_client.V1Secret,
                      api_core.read_namespaced_secret(f"{s_name}", self.namespace))
            getLogger().info(f"Secret {s_name} found in {self.namespace}")
            return cm
        except ApiException as e:
            getLogger().info(f"Secret {s_name} NOT found in {self.namespace}")
            if e.status == 404:
                return None
            raise

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

    def get_metrics_monitor(self) :#-> typing.Optional[api_customobj.___]:
        try:
            return api_customobj.get_namespaced_custom_object(
                "monitoring.coreos.com", "v1", self.namespace, "servicemonitors", self.name)
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
        logger.info(f"\tRouter instances:\t{self.parsed_spec.router.instances}")
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


def get_all_clusters(ns: str = None) -> typing.List[InnoDBCluster]:
    if ns is None:
        objects = cast(dict, api_customobj.list_cluster_custom_object(
            consts.GROUP, consts.VERSION, consts.INNODBCLUSTER_PLURAL))
    else:
        objects = cast(dict, api_customobj.list_namespaced_custom_object(
            consts.GROUP, consts.VERSION, ns, consts.INNODBCLUSTER_PLURAL))
    return [InnoDBCluster(o) for o in objects["items"]]


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
        return self.name.rpartition("-")[0]

    @property
    def address(self) -> str:
        return self.name+"."+cast(str, self.spec.subdomain)

    @property
    def address_fqdn(self) -> str:
        return self.name+"."+cast(str, self.spec.subdomain)+"."+self.namespace+".svc."+k8s_cluster_domain(self.logger)

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
