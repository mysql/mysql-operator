# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os

# version
VERSION_TAG = "8.0.26"

MIN_SUPPORTED_VERSION = "8.0.24"
MAX_SUPPORTED_VERSION = "8.0.27"


# image
IMAGE_REGISTRY = os.getenv(
    "OPERATOR_TEST_REGISTRY", default=None)

IMAGE_REPOSITORY = os.getenv(
    "OPERATOR_TEST_REPOSITORY", default="mysql")


# operator
OPERATOR_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_IMAGE_NAME", default="mysql-operator")

OPERATOR_EE_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_EE_IMAGE_NAME", default="mysql-operator-commercial")
    # "OPERATOR_TEST_EE_IMAGE_NAME", default="mysql-operator")

OPERATOR_VERSION_TAG = os.getenv(
    "OPERATOR_TEST_VERSION_TAG", default="8.0.26-2.0.2")

OPERATOR_PULL_POLICY = os.getenv(
    "OPERATOR_TEST_PULL_POLICY", default="IfNotPresent")

OPERATOR_GR_IP_WHITELIST = os.getenv(
    "OPERATOR_TEST_GR_IP_WHITELIST", default="172.17.0.0/8")


# server
SERVER_VERSION_TAG = VERSION_TAG
SERVER_IMAGE_NAME = "mysql-server"
SERVER_EE_IMAGE_NAME = "enterprise-server"
# SERVER_EE_IMAGE_NAME = "mysql-server"


# router
ROUTER_VERSION_TAG = VERSION_TAG
ROUTER_IMAGE_NAME = "mysql-router"
ROUTER_EE_IMAGE_NAME = "enterprise-router"
# ROUTER_EE_IMAGE_NAME = "mysql-router"

# oci
OCI_SKIP = os.getenv(
    "OPERATOR_TEST_SKIP_OCI", default=False)

OCI_BACKUP_APIKEY_PATH = os.getenv(
    "OPERATOR_TEST_BACKUP_OCI_APIKEY_PATH", default=None)

OCI_RESTORE_APIKEY_PATH = os.getenv(
    "OPERATOR_TEST_RESTORE_OCI_APIKEY_PATH", default=None)

OCI_BACKUP_BUCKET = os.getenv(
    "OPERATOR_TEST_BACKUP_OCI_BUCKET", default=None)
