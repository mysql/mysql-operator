# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import pytest
import copy
from .controller import consts, utils, config, shellutils
from .controller.storage_api import StorageSpec, OCIOSStorageSpec, PVCStorageSpec
from .controller.api_utils import ApiSpecError
from .controller.backup.backup_api import Snapshot, DumpInstance
from .controller.backup import backup_objects

#from .controller.innodbcluster.cluster_api import InnoDBCluster
#import logging

@pytest.fixture
def oci_os_correct() -> dict:
    return {
        'ociObjectStorage': {
            'bucketName': 'idbcluster_backup',
            'credentials': 'oci-credentials',
            'prefix': '/',
        }
    }

@pytest.fixture
def storage_correct(oci_os_correct: dict) -> dict:
    return {
        'storage': oci_os_correct,
    }


@pytest.fixture
def oci_os_no_prefix() -> dict:
    return {
        'ociObjectStorage': {
            'bucketName': 'idbcluster_backup',
            'credentials': 'oci-credentials',
        }
    }

@pytest.fixture
def storage_no_prefix(oci_os_no_prefix: dict) -> dict:
    return {
        'storage': oci_os_no_prefix,
    }


@pytest.fixture
def oci_os_no_bucket() -> dict:
    return {
        'ociObjectStorage': {
            'credentials': 'oci-credentials',
            'prefix': '/',
        }
    }

@pytest.fixture
def storage_no_bucket(oci_os_no_bucket: dict) -> dict:
    return {
        'storage': oci_os_no_bucket
    }


@pytest.fixture
def oci_os_no_credentials() -> dict:
    return {
        'ociObjectStorage': {
            'bucketName': 'idbcluster_backup',
            'prefix' : '/'
        }
    }

@pytest.fixture
def storage_no_credentials(oci_os_no_credentials: dict) -> dict:
    return {
        'storage': oci_os_no_credentials,
    }



@pytest.fixture
def pod_spec_correct_input() -> dict:
    return {
        "spec":{
            "containers":[
                {
                    "name": "operator-backup-job",
                    "image": "example.com/mysql/community-operator:8.0.26-2.0.2",
                    "imagePullPolicy": "IfNotPresent",
                    "command":[
                        "mysqlsh",
                        "--pym",
                        "mysqloperator",
                        "backup",
                        "execute-backup",
                        "example-ns",
                        "mycluster-schedule-ref211116104040",
                        "mycluster-schedule-ref211116104040",
                        "/mnt/storage"
                    ]
                }
            ],
            "restartPolicy": "Never",
            "terminationGracePeriodSeconds": 60,
            "serviceAccountName": "mycluster-sa"
        }
    }

@pytest.fixture
def pod_spec_correct_output() -> dict:
    return {
        "spec":{
            "containers":[
                {
                    "name":"operator-backup-job",
                    "image":"example.com/mysql/community-operator:8.0.26-2.0.2",
                    "imagePullPolicy":"IfNotPresent",
                    "command":[
                    "mysqlsh",
                    "--pym",
                    "mysqloperator",
                    "backup",
                    "execute-backup",
                    "example-ns",
                    "mycluster-schedule-ref211116104040",
                    "mycluster-schedule-ref211116104040",
                    "/mnt/storage"
                    ]
                },
                {
                    "name":"container-name",
                    "env":[
                    {
                        "name":"OCI_USER_NAME",
                        "valueFrom":{
                            "secretKeyRef":{
                                "name":"oci-credentials",
                                "key":"user"
                            }
                        }
                    },
                    {
                        "name":"OCI_FINGERPRINT",
                        "valueFrom":{
                            "secretKeyRef":{
                                "name":"oci-credentials",
                                "key":"fingerprint"
                            }
                        }
                    },
                    {
                        "name":"OCI_TENANCY",
                        "valueFrom":{
                            "secretKeyRef":{
                                "name":"oci-credentials",
                                "key":"tenancy"
                            }
                        }
                    },
                    {
                        "name":"OCI_REGION",
                        "valueFrom":{
                            "secretKeyRef":{
                                "name":"oci-credentials",
                                "key":"region"
                            }
                        }
                    },
                    {
                        "name":"OCI_PASSPHRASE",
                        "valueFrom":{
                            "secretKeyRef":{
                                "name":"oci-credentials",
                                "key":"passphrase"
                            }
                        }
                    },
                    {
                        "name":"OCI_CONFIG_NAME",
                        "value":"/mysqlsh/oci_config"
                    },
                    {
                        "name":"OCI_API_KEY_NAME",
                        "value":"/.oci/privatekey.pem"
                    }
                    ],
                    "volumeMounts":[
                    {
                        "name":"privatekey-volume",
                        "readOnly":True,
                        "mountPath":"/.oci"
                    }
                    ]
                }
            ],
            'securityContext': {
                'allowPrivilegeEscalation': False,
                'fsGroup': 27,
                'privileged': False,
                'readOnlyRootFilesystem': True,
                'runAsNonRoot': True,
                'runAsUser': 27
            },
            "restartPolicy":"Never",
            "terminationGracePeriodSeconds":60,
            "serviceAccountName":"mycluster-sa",
            "volumes":[
                {
                    "name":"privatekey-volume",
                    "secret":{
                    "secretName":"oci-credentials",
                    "items":[
                        {
                            "key":"privatekey",
                            "path":"privatekey.pem",
                            "mode":400
                        }
                    ]
                    }
                }
            ]
        }
    }

@pytest.fixture
def object_factory() -> list:
    return [Snapshot(), DumpInstance()]


def test_parse_correct(object_factory, storage_correct) -> None:
    for object_template in object_factory:
        test_obj = copy.deepcopy(object_template)
        test_obj.parse(storage_correct, "test")

        assert isinstance(test_obj.storage, StorageSpec)
        assert test_obj.storage.persistentVolumeClaim is None
        assert test_obj.storage.ociObjectStorage is not None
        assert isinstance(test_obj.storage.ociObjectStorage, OCIOSStorageSpec)
        assert test_obj.storage.ociObjectStorage.bucketName == 'idbcluster_backup'
        assert test_obj.storage.ociObjectStorage.ociCredentials == 'oci-credentials'
        assert test_obj.storage.ociObjectStorage.prefix == '/'


def test_parse_no_prefix(object_factory, storage_no_prefix) -> None:
    for object_template in object_factory:
        test_obj = copy.deepcopy(object_template)
        test_obj.parse(storage_no_prefix, "test")

        assert isinstance(test_obj.storage, StorageSpec)
        assert test_obj.storage.persistentVolumeClaim is None
        assert test_obj.storage.ociObjectStorage is not None
        assert isinstance(test_obj.storage.ociObjectStorage, OCIOSStorageSpec)
        assert test_obj.storage.ociObjectStorage.bucketName == 'idbcluster_backup'
        assert test_obj.storage.ociObjectStorage.ociCredentials == 'oci-credentials'
        assert test_obj.storage.ociObjectStorage.prefix == ''


def test_parse_no_bucket(object_factory, storage_no_bucket) -> None:
    for object_template in object_factory:
        test_obj = copy.deepcopy(object_template)
        with pytest.raises(ApiSpecError, match="test.storage.ociObjectStorage.bucketName is mandatory, but is not set"):
            test_obj.parse(storage_no_bucket, "test")


def test_parse_no_credentials(object_factory, storage_no_credentials) -> None:
    for object_template in object_factory:
        test_obj = copy.deepcopy(object_template)
        with pytest.raises(ApiSpecError, match="test.storage.ociObjectStorage.credentials is mandatory, but is not set"):
            test_obj.parse(storage_no_credentials, "test")


def test_equality(object_factory, storage_correct) -> None:
    for object_template in object_factory:
        test_obj_left = copy.deepcopy(object_template)
        test_obj_left.parse(storage_correct, "left")

        test_obj_right = copy.deepcopy(object_template)
        test_obj_right.parse(storage_correct, "right")

        assert test_obj_left == test_obj_right


def test_non_equality(object_factory, storage_correct, storage_no_prefix) -> None:
    for object_template in object_factory:
        test_obj_left = copy.deepcopy(object_template)
        test_obj_left.parse(storage_correct, "left")

        test_obj_right = copy.deepcopy(object_template)
        test_obj_right.parse(storage_no_prefix, "right")

        assert test_obj_left != test_obj_right


def test_add_to_pod_spec(object_factory, storage_correct, pod_spec_correct_input, pod_spec_correct_output) -> None:
    for object_template in object_factory:
        pod_spec = copy.deepcopy(pod_spec_correct_input)
        test_obj = copy.deepcopy(object_template)
        test_obj.parse(storage_correct, "test")
        test_obj.add_to_pod_spec(pod_spec, "container-name")

        assert pod_spec == pod_spec_correct_output
