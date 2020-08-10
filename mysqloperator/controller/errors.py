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
    def __init__(self, msg, code):
        super().__init__(msg)
        self.code = code
