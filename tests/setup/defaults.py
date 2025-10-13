# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os

# version
VERSION_TAG = "9.6.0"

MIN_SUPPORTED_VERSION = "8.0.28"
MAX_SUPPORTED_VERSION = "9.6.0"

# Some tests won't work if jumping from MIN_SUPPORTED_VERSION to MAX_SUPPORTED_VERSION
# The result will be
# [ERROR] [MY-014060] [Server] Invalid MySQL server upgrade: Cannot upgrade from 80028 to 90500. Upgrade to next major version is only allowed from the last LTS release, which version 80028 is not.
CURRENT_LTS_VERSION="8.4.5"

# image
IMAGE_REGISTRY = os.getenv(
    "OPERATOR_TEST_REGISTRY", default=None)

IMAGE_REPOSITORY = os.getenv(
    "OPERATOR_TEST_REPOSITORY", default="mysql")


# operator
OPERATOR_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_IMAGE_NAME", default="community-operator")

OPERATOR_EE_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_EE_IMAGE_NAME", default="enterprise-operator")

OPERATOR_VERSION_TAG = os.getenv(
    "OPERATOR_TEST_VERSION_TAG", default="9.6.0-2.2.7")

OPERATOR_OLD_VERSION_TAG = os.getenv(
    "OPERATOR_TEST_OLD_VERSION_TAG", default="8.0.31-2.0.7")

OPERATOR_PULL_POLICY = os.getenv(
    "OPERATOR_TEST_PULL_POLICY", default="IfNotPresent")


# server
SERVER_VERSION_TAG = VERSION_TAG
SERVER_IMAGE_NAME = "community-server"
SERVER_EE_IMAGE_NAME = "enterprise-server"


# router
ROUTER_VERSION_TAG = VERSION_TAG
ROUTER_IMAGE_NAME = "community-router"
ROUTER_EE_IMAGE_NAME = "enterprise-router"


# enterprise
ENTERPRISE_SKIP = os.getenv(
    "OPERATOR_TEST_SKIP_ENTERPRISE", default=False)

AUDIT_LOG_SKIP = os.getenv(
    "OPERATOR_TEST_SKIP_AUDIT_LOG", default=False)

# oci
OCI_SKIP = os.getenv(
    "OPERATOR_TEST_SKIP_OCI", default=False)

OCI_CONFIG_PATH = os.getenv(
    "OPERATOR_TEST_OCI_CONFIG_PATH", default=None)

OCI_BUCKET_NAME = os.getenv(
    "OPERATOR_TEST_OCI_BUCKET", default=None)

OCI_VAULT_CONFIG_PATH = os.getenv(
    "OPERATOR_TEST_VAULT_CONFIG_PATH", default=None)

OCI_S3_ENDPOINT = os.getenv(
    "OPERATOR_TEST_OCI_S3_ENDPOINT", default=None)

OCI_S3_PROFILE = os.getenv(
    "OPERATOR_TEST_OCI_S3_PROFILE", default=None)

OCI_S3_CONFIG_PATH = os.getenv(
    "OPERATOR_TEST_OCI_S3_CONFIG_PATH", default=None)

OCI_S3_CREDENTIALS_PATH = os.getenv(
    "OPERATOR_TEST_OCI_S3_CREDENTIALS_PATH", default=None)

# s3 compatible storage, non oci
S3_SKIP = os.getenv(
    "OPERATOR_TEST_SKIP_S3", default=False)

S3_BUCKET_NAME = os.getenv(
    "OPERATOR_TEST_S3_BUCKET", default=None)

S3_ENDPOINT = os.getenv(
    "OPERATOR_TEST_S3_ENDPOINT", default=None)

S3_PROFILE = os.getenv(
    "OPERATOR_TEST_S3_PROFILE", default=None)

S3_CONFIG_PATH = os.getenv(
    "OPERATOR_TEST_S3_CONFIG_PATH", default=None)

S3_CREDENTIALS_PATH = os.getenv(
    "OPERATOR_TEST_S3_CREDENTIALS_PATH", default=None)

# azure backup
AZURE_SKIP = os.getenv(
    "OPERATOR_TEST_SKIP_AZURE", default=False)

AZURE_STORAGE_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_AZURE_STORAGE_IMAGE_NAME", default="mcr.microsoft.com/azure-storage/azurite")

AZURE_CLI_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_AZURE_CLI_IMAGE_NAME", default="mcr.microsoft.com/azure-cli")

AZURE_CONFIG_FILE = os.getenv(
    "OPERATOR_TEST_AZURE_CONFIG_FILE", default=None)

AZURE_CONTAINER_NAME = os.getenv(
    "OPERATOR_TEST_AZURE_CONTAINER_NAME", default=None)

# KMIP
KMIP_OKVCLIENT_ORA_PATH = os.getenv(
    "OPERATOR_TEST_KMIP_OKVCLIENT_ORA_PATH", default=None)
KMIP_TLS_PATH = os.getenv(
    "OPERATOR_TEST_KMIP_TLS_PATH", default=None)


FLUENTD_IMAGE_NAME= os.getenv(
    "OPERATOR_TEST_FLUENTD_IMAGE_NAME", default="fluent/fluentd-kubernetes-daemonset:v1.16-debian-s3-amd64-1")

# metrics sidecar
METRICS_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_METRICS_IMAGE_NAME", default="prom/mysqld-exporter:v0.14.0")

OPERATOR_UPGRADE_RUN_ALL_TESTS = os.getenv(
    "OPERATOR_UPGRADE_RUN_ALL_TESTS", default=False)

# k8s
K8S_CLUSTER_NAME = os.getenv(
    "OPERATOR_TEST_K8S_CLUSTER_NAME", default="ote-mysql")

K8S_CLUSTER_DOMAIN_ALIAS = os.getenv(
    "OPERATOR_TEST_K8S_CLUSTER_DOMAIN_ALIAS")
