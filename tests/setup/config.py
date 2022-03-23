# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from utils import auxutil
from setup import defaults

class Config:
    version_tag = defaults.VERSION_TAG

    min_supported_version = defaults.MIN_SUPPORTED_VERSION
    max_supported_version = defaults.MAX_SUPPORTED_VERSION

    image_registry = defaults.IMAGE_REGISTRY
    image_repository = defaults.IMAGE_REPOSITORY
    image_registry_host = ""
    image_registry_port = ""
    image_registry_is_loopback = ""

    operator_image_name = defaults.OPERATOR_IMAGE_NAME
    operator_ee_image_name = defaults.OPERATOR_EE_IMAGE_NAME
    operator_version_tag = defaults.OPERATOR_VERSION_TAG
    operator_pull_policy = defaults.OPERATOR_PULL_POLICY
    operator_gr_ip_whitelist = defaults.OPERATOR_GR_IP_WHITELIST

    # server
    server_version_tag = defaults.SERVER_VERSION_TAG
    server_image_name = defaults.SERVER_IMAGE_NAME
    server_ee_image_name = defaults.SERVER_EE_IMAGE_NAME

    # router
    router_version_tag = defaults.ROUTER_VERSION_TAG
    router_image_name = defaults.ROUTER_IMAGE_NAME
    router_ee_image_name = defaults.ROUTER_EE_IMAGE_NAME

    # oci
    oci_skip = defaults.OCI_SKIP
    oci_config_path = defaults.OCI_CONFIG_PATH
    oci_bucket_name = defaults.OCI_BUCKET_NAME

    # k8s
    k8s_cluster = defaults.K8S_CLUSTER_NAME
    k8s_context = None

    @property
    def operator_shell_version_num(self):
        a,b,c = self.operator_version_tag.split("-")[0].split(".")
        return int(a)*10000 + int(b)*100 + int(c)

    @property
    def server_version_num(self):
        a,b,c = self.server_version_tag.split("-")[0].split(".")
        return int(a)*10000 + int(b)*100 + int(c)

    def commit(self):
        if self.image_registry:
            self.image_registry_host, self.image_registry_port, self.image_registry_is_loopback = auxutil.resolve_registry_url(self.image_registry)

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

    def get_operator_image(self):
        return f"{self.get_image_registry_repository()}/{self.operator_image_name}:{self.operator_version_tag}"

    def get_server_image(self, version=None):
        return f"{self.get_image_registry_repository()}/{self.server_image_name}:{version if version else self.version_tag}"

    def get_old_server_image(self):
        return self.get_server_image(self.get_old_version_tag())

    def get_router_image(self):
        return f"{self.get_image_registry_repository()}/{self.router_image_name}:{self.version_tag}"

    def get_old_router_image(self):
        return f"{self.get_image_registry_repository()}/{self.router_image_name}:{self.get_old_version_tag()}"


# test-suite configuration
g_ts_cfg = Config()
