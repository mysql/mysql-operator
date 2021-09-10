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
OPERATOR_VERSION = "2.0.2"
OPERATOR_EDITION = Edition.community

MIN_BASE_SERVER_ID = 1
MAX_BASE_SERVER_ID = 4000000000

DEFAULT_VERSION_TAG = "8.0.25"

DEFAULT_SERVER_VERSION_TAG = DEFAULT_VERSION_TAG
MIN_SUPPORTED_MYSQL_VERSION = "8.0.24"
MAX_SUPPORTED_MYSQL_VERSION = "8.0.26"

DEFAULT_ROUTER_VERSION_TAG = DEFAULT_VERSION_TAG

# This is used for the sidecar. The operator version is deploy-operator.yaml
DEFAULT_OPERATOR_VERSION_TAG = "8.0.25-2.0.2"

# TODO - unify those two settings (if we use OCR for community as well we can use the same thing)
DEFAULT_IMAGE_REPOSITORY = os.getenv(
    "MYSQL_OPERATOR_DEFAULT_REPOSITORY", default="mysql")

DEFAULT_IMAGE_REPOSITORY_EE = os.getenv(
    "MYSQL_OPERATOR_DEFAULT_REPOSITORY", default="container-registry.oracle.com/mysql")

MYSQL_SERVER_IMAGE = "mysql-server"
MYSQL_ROUTER_IMAGE = "mysql-router"
MYSQL_OPERATOR_IMAGE = "mysql-operator"

# TODO
MYSQL_SERVER_EE_IMAGE = "enterprise-server"
MYSQL_ROUTER_EE_IMAGE = "enterprise-router"
MYSQL_OPERATOR_EE_IMAGE = "mysql-operator-commercial"

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
