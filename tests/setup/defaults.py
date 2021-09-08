# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os

# _pull_policy = os.getenv("MYSQL_OPERATOR_IMAGE_PULL_POLICY")
# if _pull_policy:
#     default_image_pull_policy = ImagePullPolicy[_pull_policy]
# else:
#     default_image_pull_policy = ImagePullPolicy.Always
# class ImagePullPolicy(Enum):
#     Never = "Never"
#     IfNotPresent = "IfNotPresent"
#     Always = "Always"


DEFAULT_VERSION_TAG = "8.0.25"

DEFAULT_SERVER_VERSION_TAG = DEFAULT_VERSION_TAG
MIN_SUPPORTED_MYSQL_VERSION = "8.0.24"
MAX_SUPPORTED_MYSQL_VERSION = "8.0.26"

DEFAULT_ROUTER_VERSION_TAG = DEFAULT_VERSION_TAG

# # This is used for the sidecar. The operator version is deploy-operator.yaml
DEFAULT_OPERATOR_VERSION_TAG = "8.0.25-2.0.1"
DEFAULT_OPERATOR_PULL_POLICY = "IfNotPresent"

DEFAULT_IMAGE_REPOSITORY = os.getenv(
    "MYSQL_OPERATOR_DEFAULT_REPOSITORY", default="mysql")

MYSQL_SERVER_IMAGE = "mysql-server"
MYSQL_ROUTER_IMAGE = "mysql-router"
MYSQL_OPERATOR_IMAGE = "mysql-operator"

# # TODO
# MYSQL_SERVER_EE_IMAGE = "enterprise-server"
# MYSQL_ROUTER_EE_IMAGE = "enterprise-router"
# MYSQL_OPERATOR_EE_IMAGE = "mysql-operator-commercial"

MYSQL_SERVER_EE_IMAGE = "mysql-server"
MYSQL_ROUTER_EE_IMAGE = "mysql-router"
MYSQL_OPERATOR_EE_IMAGE = "mysql-operator"
