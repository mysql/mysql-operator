# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os

# version
VERSION_TAG = "8.4.0"

MIN_SUPPORTED_VERSION = "8.0.27"
MAX_SUPPORTED_VERSION = "8.4.0"

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
    "OPERATOR_TEST_VERSION_TAG", default="8.4.0-2.1.3")

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

FLUENTD_IMAGE_NAME= os.getenv(
    "OPERATOR_TEST_FLUENTD_IMAGE_NAME", default="fluent/fluentd-kubernetes-daemonset:v1.16-debian-s3-amd64-1")

# metrics sidecar
METRICS_IMAGE_NAME = os.getenv(
    "OPERATOR_TEST_METRICS_IMAGE_NAME", default="prom/mysqld-exporter:v0.14.0")


# k8s
K8S_CLUSTER_NAME = os.getenv(
    "OPERATOR_TEST_K8S_CLUSTER_NAME", default="ote-mysql")

K8S_CLUSTER_DOMAIN_ALIAS = os.getenv(
    "OPERATOR_TEST_K8S_CLUSTER_DOMAIN_ALIAS")
