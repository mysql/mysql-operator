# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import pathlib
import shutil
from utils import auxutil
from setup import defaults
import tempfile
from typing import Dict
from enum import Enum

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
    azure_config_file_is_tmp = False
    azure_container_name = defaults.AZURE_CONTAINER_NAME

    # vault
    vault_cfg_path = defaults.OCI_VAULT_CONFIG_PATH

    # runtime environment
    workspace_dir = None

    # diagnostics
    work_dir = None
    work_dir_is_tmp = False
    store_operator_log = False
    current_test_name = None

    # Optional K8s cluster domain alias
    k8s_cluster_domain_alias = defaults.K8S_CLUSTER_DOMAIN_ALIAS

    # an optional custom secret to be copied to each test-case namespace at the startup
    custom_secret_ns = None
    custom_secret_name = None

    custom_operator_ns_labels: Dict[str, str] = {}
    custom_test_ns_labels: Dict[str, str] = {}
    custom_sts_labels: Dict[str, str] = {}
    custom_sts_podspec: str = ""
    custom_ic_server_version: str = ""
    custom_ic_server_version_override: str = ""
    custom_ic_router_version: str = ""
    custom_ic_router_version_override: str = ""

    router_extra_containers_per_pod = 0

    local_path_provisioner_install = False
    local_path_provisioner_manifest_url = "https://raw.githubusercontent.com/rancher/local-path-provisioner/v0.0.24/deploy/local-path-storage.yaml"
    local_path_provisioner_shared_path = "/tmp/local-path-shared"

    def __del__(self):
        if self.azure_config_file_is_tmp:
             os.remove(self.azure_config_file)

        if self.work_dir_is_tmp:
            shutil.rmtree(self.work_dir)

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
            self.work_dir_is_tmp = True

        if self.start_azure:
            if not g_ts_cfg.azure_config_file:
                g_ts_cfg.azure_config_file = tempfile.mktemp('.cfg','azure-')
                g_ts_cfg.azure_config_file_is_tmp = True
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

    def get_diagnostics_dir(self, ns=None):
        diagnostics_dir = os.path.join(g_ts_cfg.work_dir, 'diagnostics', g_ts_cfg.k8s_context)
        if self.current_test_name:
            diagnostics_dir = os.path.join(diagnostics_dir, self.current_test_name)
        elif ns:
            diagnostics_dir = os.path.join(diagnostics_dir, ns)
        return diagnostics_dir

    class Image(Enum):
        AZURE_STORAGE = 0
        AZURE_CLI = 1
        FLUENTD = 2
        METRICS = 3

    def image_to_name(self, image: Image) -> str:
        if image == Config.Image.AZURE_STORAGE:
            return defaults.AZURE_STORAGE_IMAGE_NAME
        elif image == Config.Image.AZURE_CLI:
            return defaults.AZURE_CLI_IMAGE_NAME
        elif image == Config.Image.FLUENTD:
            return defaults.FLUENTD_IMAGE_NAME
        elif image == Config.Image.METRICS:
            return defaults.METRICS_IMAGE_NAME
        else:
            return None

    def get_image(self, image: Image) -> str:
        image_name = self.image_to_name(image)
        return f"{self.image_registry}{'/' if self.image_registry else ''}{image_name}"

    def get_custom_operator_ns_labels(self) -> Dict[str, str]:
        return self.custom_operator_ns_labels

    def get_custom_test_ns_labels(self) -> Dict[str, str]:
        return self.custom_test_ns_labels

    def get_custom_sts_labels(self) -> Dict[str, str]:
        return self.custom_sts_labels

    def get_custom_sts_podspec(self) -> str:
        return self.custom_sts_podspec

    def get_custom_ic_server_version(self) -> str:
        return self.custom_ic_server_version

    def get_custom_ic_server_version_override(self) -> str:
        return self.custom_ic_server_version_override

    def get_custom_ic_router_version(self) -> str:
        return self.custom_ic_router_version

    def get_custom_ic_router_version_override(self) -> str:
        return self.custom_ic_router_version_override

    def get_router_total_containers_per_pod(self) -> str:
        return 1 + self.router_extra_containers_per_pod

    def get_local_path_provisioner_shared_path(self) -> str:
        return self.local_path_provisioner_shared_path

    def get_local_path_provisioner_manifest_url(self) -> str:
        return self.local_path_provisioner_manifest_url

    def __str__(self):
        return f"""
Image registry:                      : {self.get_image_registry_repository()}
Operator image                       : {self.get_operator_image()}
Server image / old image             : {self.get_server_image()} / {self.get_old_server_image()}
Router image / old image             : {self.get_router_image()} / {self.get_old_router_image()}
Fluentd image                        : {self.get_image(Config.Image.FLUENTD)}
Metrics image                        : {self.get_image(Config.Image.METRICS)}
Custom test ns labels                : {self.get_custom_test_ns_labels()}
Custom operator ns labels            : {self.get_custom_operator_ns_labels()}
Custom STS podspec                   : {self.get_custom_sts_podspec()}
Custom IC Server version             : {self.get_custom_ic_server_version()}
Custom IC Server version override all: {self.get_custom_ic_server_version_override()}
Custom IC Router version             : {self.get_custom_ic_router_version()}
Custom IC Router version override all: {self.get_custom_ic_server_version_override()}
Total containers per router pod      : {self.get_router_total_containers_per_pod()}
Local path provisioner install       : {self.local_path_provisioner_install}
Local path provisioner shared path   : {self.get_local_path_provisioner_shared_path()}
Local path provisioner manifest URL  : {self.get_local_path_provisioner_manifest_url()}"""

# test-suite configuration
g_ts_cfg = Config()
