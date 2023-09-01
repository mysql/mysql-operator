# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import pytest
import copy
from .controller import consts, utils, config, shellutils
from .controller.storage_api import StorageSpec, OCIOSStorageSpec, PVCStorageSpec
from .controller.api_utils import ApiSpecError
from .controller.backup.backup_api import MySQLBackupSpec, BackupProfile
from .controller.backup import backup_objects


@pytest.fixture
def mysql_backup_spec_correct() -> dict:
    return {
        "clusterName": "mycluster",
        "deleteBackupData": False,
        "backupProfile":{
            "name": "mycluster-schedule-oci211117093348",
            "dumpInstance":{
                "storage":{
                    "ociObjectStorage": {
                        "bucketName":"idbcluster_backup_bucket",
                        "credentials":"oracle-cloud-credentials",
                        "prefix":"/mybackup"
                    },
                },
            },
        },
    }

@pytest.mark.xfail(reason="MySQLBackupSpec needs DI before this test could work")
def test_mysql_backup_spec_correct(mysql_backup_spec_correct) -> None:
    test_obj : MySQLBackupSpec = MySQLBackupSpec(None, "my-backup", mysql_backup_spec_correct)

    assert test_obj.cluster == "mycluster"
    assert isinstance(test_obj.backupProfile, BackupProfile)

