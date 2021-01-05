# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
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


from mysqloperator.controller.api_utils import Edition, ImagePullPolicy
import os

debug = False
enable_mysqld_general_log = False

_pull_policy = os.getenv("MYSQL_OPERATOR_IMAGE_PULL_POLICY")
if _pull_policy:
    default_image_pull_policy = ImagePullPolicy[_pull_policy]
else:
    default_image_pull_policy = ImagePullPolicy.Always


# Constants
OPERATOR_VERSION = "0.1.0"
OPERATOR_EDITION = Edition.community

MIN_BASE_SERVER_ID = 1
MAX_BASE_SERVER_ID = 4000000000

DEFAULT_VERSION_TAG = "8.0.21"

DEFAULT_SERVER_VERSION_TAG = DEFAULT_VERSION_TAG
MIN_SUPPORTED_MYSQL_VERSION = "8.0.21"
MAX_SUPPORTED_MYSQL_VERSION = "8.0.22"

DEFAULT_ROUTER_VERSION_TAG = DEFAULT_VERSION_TAG

DEFAULT_SHELL_VERSION_TAG = DEFAULT_VERSION_TAG

DEFAULT_IMAGE_REPOSITORY = os.getenv(
    "MYSQL_OPERATOR_DEFAULT_REPOSITORY", default="mysql")

MYSQL_SERVER_IMAGE = "mysql-server"
MYSQL_ROUTER_IMAGE = "mysql-router"
MYSQL_SHELL_IMAGE = "mysql-shell"

MYSQL_SERVER_EE_IMAGE = "mysql-server"  # TODO
MYSQL_ROUTER_EE_IMAGE = "mysql-router"
MYSQL_SHELL_EE_IMAGE = "mysql-shell-commercial"

CLUSTER_ADMIN_USER_NAME = "mysqladmin"
ROUTER_METADATA_USER_NAME = "mysqlrouter"
BACKUP_USER_NAME = "mysqlbackup"


def config_from_env() -> None:
    import mysqlsh

    global debug
    global enable_mysqld_general_log
    global default_image_pull_policy

    level = os.getenv("MYSQL_OPERATOR_DEBUG")

    if level:
        level = int(level)
        if level > 0:
            debug = level
            enable_mysqld_general_log = True

            if level > 4:
                mysqlsh.globals.shell.options.logLevel = 8
                mysqlsh.globals.shell.options.verbose = 3
                mysqlsh.globals.shell.options["dba.logSql"] = 2
            elif level > 3:
                mysqlsh.globals.shell.options.logLevel = 7
                mysqlsh.globals.shell.options.verbose = 2
                mysqlsh.globals.shell.options["dba.logSql"] = 2
            elif level > 1:
                mysqlsh.globals.shell.options.logLevel = 7
                mysqlsh.globals.shell.options.verbose = 1
                mysqlsh.globals.shell.options["dba.logSql"] = 1
            else:
                mysqlsh.globals.shell.options.logLevel = 6
