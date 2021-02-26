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
        patch = f"""
spec:
    containers:
    - name: {container_name}
      volumeMounts:
      - name: tmp-storage
        mountPath: /mnt/storage
    volumes:
    - name: tmp-storage
{indent(yaml.safe_dump(self.raw_data), 6)}
"""
        merge_patch_object(pod_spec, yaml.safe_load(patch))

    def parse(self, spec: dict, prefix: str) -> None:
        self.raw_data = spec


class OCIOSStorageSpec:
    bucketName: str = ""
    prefix: str = ""
    apiKeySecretName: str = ""

    def add_to_pod_spec(self, pod_spec: dict, container_name: str) -> None:
        patch = f"""
spec:
    containers:
    - name: {container_name}
      volumeMounts:
      - name: secrets-volume
        readOnly: true
        mountPath: "/.oci"
    volumes:
    - name: secrets-volume
      secret:
        defaultMode: 400
        secretName: {self.apiKeySecretName}
"""
        merge_patch_object(pod_spec, yaml.safe_load(patch))

    def parse(self, spec: dict, prefix: str) -> None:
        self.bucketName = dget_str(spec, "bucketName", prefix)

        self.apiKeySecretName = dget_str(spec, "apiKeySecretName", prefix)


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
            self.persistentVolumeClaim.add_to_pod_spec(
                pod_spec, container_name)

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
