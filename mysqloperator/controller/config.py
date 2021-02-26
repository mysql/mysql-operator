# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


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
