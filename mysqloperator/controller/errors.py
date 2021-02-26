# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import kopf

# Shell Error Codes (TODO move to mysqlsh.ErrorCode)
SHERR_DBA_BADARG_INSTANCE_NOT_MANAGED = 51300
SHERR_DBA_BADARG_INSTANCE_NOT_ONLINE = 51314
SHERR_DBA_BADARG_INSTANCE_ALREADY_IN_GR = 51315
SHERR_DBA_MEMBER_METADATA_MISSING = 51104
SHERR_DBA_GROUP_HAS_NO_QUORUM = 51011

# TODO review this error see if should go in dba_errors.h
SHERR_DBA_GROUP_REBOOT_NEEDED = "NEED_REBOOT"


class PermanentErrorWithCode(kopf.PermanentError):
    def __init__(self, msg: str, code: int):
        super().__init__(msg)
        self.code = code
