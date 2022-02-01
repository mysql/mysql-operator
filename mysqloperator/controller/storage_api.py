# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import Optional
from .api_utils import dget_dict, dget_str, dget_int, dget_bool, dget_list, ApiSpecError
from .utils import merge_patch_object, indent
import yaml


class CustomStorageSpec:
    helperImage = None
    beforeScript = None
    afterScript = None
    secretsName = None
    secretsKeys = None  # map: variable -> key


# TODO volume instead of persistentVolumeClaim?
class PVCStorageSpec:
    raw_data = None

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        # /mnt/storage is passed as parameter to the backup_main.py
        patch = f"""
spec:
    securityContext:
      runAsUser: 0
#      allowPrivilegeEscalation: false
#      privileged: false
#      readOnlyRootFilesystem: true
#      runAsNonRoot: false
    containers:
    - name: {container_name}
      env:
      - name: DUMP_MOUNT_PATH
        value: /mnt/storage
      volumeMounts:
      - name: tmp-storage
        mountPath: /mnt/storage
    volumes:
    - name: tmp-storage
      persistentVolumeClaim:
{indent(yaml.safe_dump(self.raw_data), 8)}
"""
        merge_patch_object(pod_spec, yaml.safe_load(patch))

    def parse(self, spec: dict, prefix: str) -> None:
        self.raw_data = spec

    def __eq__(self, other) -> bool:
        # TODO: raw_data could easily break things - single whitespace and it's not the same
        return isinstance(other, PVCStorageSpec) and self.raw_data == other.raw_data


class OCIOSStorageSpec:
    bucketName: str = ""
    prefix: str = ""
    ociCredentials: str = ""

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        # The value for OCI_MOUNT_PATH should be the mountPath of the secrets-volume 
        # OCI_API_KEY_NAME is the only key in the secret which holds the API key
        # The secrets volume is not readOnly because we need to write the config file into it
        patch = f"""
spec:
    securityContext:
      allowPrivilegeEscalation: false
      privileged: false
      readOnlyRootFilesystem: true
      runAsNonRoot: true
      runAsUser: 27
      fsGroup: 27
    containers:
    - name: {container_name}
      env:
      - name: OCI_USER_NAME
        valueFrom:
          secretKeyRef:
            name: {self.ociCredentials}
            key: user
      - name: OCI_FINGERPRINT
        valueFrom:
          secretKeyRef:
            name: {self.ociCredentials}
            key: fingerprint
      - name: OCI_TENANCY
        valueFrom:
          secretKeyRef:
            name: {self.ociCredentials}
            key: tenancy
      - name: OCI_REGION
        valueFrom:
          secretKeyRef:
            name: {self.ociCredentials}
            key: region
      - name: OCI_PASSPHRASE
        valueFrom:
          secretKeyRef:
            name: {self.ociCredentials}
            key: passphrase
      - name: OCI_CONFIG_NAME
        value: "/mysqlsh/oci_config"
      - name: OCI_API_KEY_NAME
        value: "/.oci/privatekey.pem"
      volumeMounts:
      - name: privatekey-volume
        readOnly: true
        mountPath: "/.oci"
    volumes:
    - name: privatekey-volume
      secret:
        secretName: {self.ociCredentials}
        items:
        - key: privatekey
          path: privatekey.pem
          mode: 400
"""
        merge_patch_object(pod_spec, yaml.safe_load(patch))

    def parse(self, spec: dict, prefix: str) -> None:
        self.prefix = dget_str(spec, "prefix", prefix, default_value = "")
        self.bucketName = dget_str(spec, "bucketName", prefix)
        self.ociCredentials = dget_str(spec, "credentials", prefix)

    def __eq__(self, other) -> bool:
        return (isinstance(other, OCIOSStorageSpec) and \
              self.bucketName == other.bucketName and \
              self.prefix == other.prefix and \
              self.ociCredentials == other.ociCredentials)


ALL_STORAGE_SPEC_TYPES = {
    "ociObjectStorage": OCIOSStorageSpec,
    "persistentVolumeClaim": PVCStorageSpec
}


class StorageSpec:
    ociObjectStorage: Optional[OCIOSStorageSpec] = None
    persistentVolumeClaim: Optional[PVCStorageSpec] = None

    def __init__(self, allowed_types: list = list(ALL_STORAGE_SPEC_TYPES.keys())):
        self._allowed_types = {}
        for t in allowed_types:
            self._allowed_types[t] = ALL_STORAGE_SPEC_TYPES[t]

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        if self.ociObjectStorage:
            self.ociObjectStorage.add_to_pod_spec(pod_spec, container_name)
        if self.persistentVolumeClaim:
            self.persistentVolumeClaim.add_to_pod_spec(pod_spec, container_name)

    def parse(self, spec: dict, prefix: str) -> None:
        storage_spec = None
        storage_class = None
        storage_keys = []
        for k, v in self._allowed_types.items():
            tmp = dget_dict(spec, k, prefix, {})
            if tmp:
                storage_spec = tmp
                storage_class = v
                storage_keys.append(k)

        if len(storage_keys) > 1:
            raise ApiSpecError(
                f"Only one of {', '.join(storage_keys)} must be set in {prefix}")
        elif len(storage_keys) == 0:
            raise ApiSpecError(
                f"One of {', '.join(storage_keys)} must be set in {prefix}")

        storage = storage_class()
        storage.parse(storage_spec, prefix + "." + storage_keys[0])
        setattr(self, storage_keys[0], storage)

    def __eq__(self, other) -> bool:
        return (isinstance(other, StorageSpec) and \
              self.ociObjectStorage == other.ociObjectStorage and \
              self.persistentVolumeClaim == other.persistentVolumeClaim)
