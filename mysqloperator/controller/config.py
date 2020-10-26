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

debug = False
enable_mysqld_general_log = False
mysql_image_pull_policy = "IfNotPresent"
router_image_pull_policy = "IfNotPresent"
shell_image_pull_policy = "IfNotPresent"


# Constants
OPERATOR_VERSION = "0.1.0"

DEFAULT_BASE_SERVER_ID = 1000
MIN_BASE_SERVER_ID = 1
MAX_BASE_SERVER_ID = 4000000000

DEFAULT_VERSION_TAG = "8.0.21"

DEFAULT_SERVER_VERSION_TAG = DEFAULT_VERSION_TAG
MIN_SUPPORTED_MYSQL_VERSION = "8.0.19"
MAX_SUPPORTED_MYSQL_VERSION = "8.0.21"

DEFAULT_ROUTER_VERSION_TAG = DEFAULT_VERSION_TAG

DEFAULT_SHELL_VERSION_TAG = DEFAULT_VERSION_TAG

MYSQL_SERVER_IMAGE = "akkojima/mysql-server"
MYSQL_ROUTER_IMAGE = "akkojima/mysql-router"
MYSQL_SHELL_IMAGE = "akkojima/mysql-shell"

CLUSTER_ADMIN_USER_NAME = "mysqladmin"
ROUTER_METADATA_USER_NAME = "mysqlrouter"
BACKUP_USER_NAME = "mysqlbackup"


def config_from_env():
    import mysqlsh
    import os

    global debug
    global enable_mysqld_general_log
    global mysql_image_pull_policy
    global router_image_pull_policy
    global MYSQL_SERVER_IMAGE
    global MYSQL_ROUTER_IMAGE

    ROUTER_METADATA_USER = "mysqlrouter"

    level = os.getenv("MYSQL_OPERATOR_DEBUG")
    dev = os.getenv("MYSQL_OPERATOR_DEV")

    if dev:
        if not level:
            level = 1

        mysql_image_pull_policy = "Never"
        router_image_pull_policy = "Never"
        shell_image_pull_policy = "Never"

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


