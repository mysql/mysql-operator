# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import pathlib
from utils import auxutil
from setup import defaults
import tempfile

class Config:
    # k8s environment
    env = None
    env_binary_path = None
    kubectl_path = None

    k8s_cluster = defaults.K8S_CLUSTER_NAME
    k8s_context = None

    # versions
    version_tag = defaults.VERSION_TAG

    min_supported_version = defaults.MIN_SUPPORTED_VERSION
    max_supported_version = defaults.MAX_SUPPORTED_VERSION

    # registry
    image_registry = defaults.IMAGE_REGISTRY
    image_repository = defaults.IMAGE_REPOSITORY
    image_registry_host = ""
    image_registry_port = ""
    image_registry_is_loopback = ""

    # operator
    operator_image_name = defaults.OPERATOR_IMAGE_NAME
    operator_ee_image_name = defaults.OPERATOR_EE_IMAGE_NAME
    operator_version_tag = defaults.OPERATOR_VERSION_TAG
    operator_old_version_tag = defaults.OPERATOR_OLD_VERSION_TAG
    operator_pull_policy = defaults.OPERATOR_PULL_POLICY

    # server
    server_version_tag = defaults.SERVER_VERSION_TAG
    server_image_name = defaults.SERVER_IMAGE_NAME
    server_ee_image_name = defaults.SERVER_EE_IMAGE_NAME

    # router
    router_version_tag = defaults.ROUTER_VERSION_TAG
    router_image_name = defaults.ROUTER_IMAGE_NAME
    router_ee_image_name = defaults.ROUTER_EE_IMAGE_NAME

    # enterprise
    enterprise_skip = defaults.ENTERPRISE_SKIP
    audit_log_skip = defaults.AUDIT_LOG_SKIP

    # OCI Object Store backup
    oci_skip = defaults.OCI_SKIP
    oci_config_path = defaults.OCI_CONFIG_PATH
    oci_bucket_name = defaults.OCI_BUCKET_NAME

    # Azure BLOB Storage Backup
    azure_skip = defaults.AZURE_SKIP
    start_azure = False
    azure_config_file = defaults.AZURE_CONFIG_FILE
    azure_container_name = defaults.AZURE_CONTAINER_NAME

    # vault
    vault_cfg_path = defaults.OCI_VAULT_CONFIG_PATH

    # metrics sidecar
    metrics_image_name = defaults.METRICS_IMAGE_NAME

    # runtime environment
    workspace_dir = None

    # diagnostics
    work_dir = None
    store_operator_log = False

    # Optional K8s cluster domain alias
    k8s_cluster_domain_alias = defaults.K8S_CLUSTER_DOMAIN_ALIAS

    # an optional custom secret to be copied to each test-case namespace at the startup
    custom_secret_ns = None
    custom_secret_name = None

    @property
    def operator_shell_version_num(self):
        a,b,c = self.operator_version_tag.split("-")[0].split(".")
        return int(a)*10000 + int(b)*100 + int(c)

    @property
    def server_version_num(self):
        a,b,c = self.server_version_tag.split("-")[0].split(".")
        return int(a)*10000 + int(b)*100 + int(c)

    def set_custom_secret(self, custom_secret):
        if not custom_secret:
            return
        if '/' in custom_secret:
            self.custom_secret_ns, self.custom_secret_name = custom_secret.split('/')
        else:
            self.custom_secret_name = custom_secret

        if not self.custom_secret_ns:
            self.custom_secret_ns = 'default'

    def commit(self):
        if not self.env:
            self.env = "minikube"

        if not self.env_binary_path:
            self.env_binary_path = self.env

        if not self.kubectl_path:
            self.kubectl_path = "kubectl"

        if self.image_registry:
            self.image_registry_host, self.image_registry_port, self.image_registry_is_loopback = auxutil.resolve_registry_url(self.image_registry)

        if not self.workspace_dir:
            self.workspace_dir = str(pathlib.Path(__file__).absolute().parent.parent.parent)

        if not self.work_dir:
            self.work_dir = tempfile.mkdtemp()

        if self.start_azure:
            if not g_ts_cfg.azure_config_file:
                g_ts_cfg.azure_config_file = tempfile.mktemp('.cfg','azure-')
            if not g_ts_cfg.azure_container_name:
                g_ts_cfg.azure_container_name = f'azure-{g_ts_cfg.k8s_cluster}'

    def get_worker_label(self):
        if self.k8s_cluster:
            return f"{self.k8s_cluster}"
        else:
            return f"{self.env}-internal"

    def get_old_version_tag(self):
        return self.min_supported_version

    def get_image_registry_repository(self):
        if self.image_registry:
            if self.image_repository:
                return self.image_registry + "/" + self.image_repository
            else:
                return self.image_registry
        else:
            return self.image_repository

    def get_operator_image(self, version=None):
        return f"{self.get_image_registry_repository()}/{self.operator_image_name}:{version if version else self.operator_version_tag}"

    def get_server_image(self, version=None):
        return f"{self.get_image_registry_repository()}/{self.server_image_name}:{version if version else self.version_tag}"

    def get_old_server_image(self):
        return self.get_server_image(self.get_old_version_tag())

    def get_router_image(self, version=None):
        return f"{self.get_image_registry_repository()}/{self.router_image_name}:{version if version else self.version_tag}"

    def get_old_router_image(self):
        return f"{self.get_image_registry_repository()}/{self.router_image_name}:{self.get_old_version_tag()}"

    def get_tests_dir(self):
        return os.path.join(self.workspace_dir, "tests")

    def get_ci_dir(self):
        return os.path.join(self.get_tests_dir(), "ci")


# test-suite configuration
g_ts_cfg = Config()
