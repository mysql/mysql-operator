# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import socket
import sys
import time
from logging import Logger

from typing import Callable, Optional, TypeVar
from kubernetes.client.rest import ApiException
from kubernetes import client, config

try:
    # outside k8s
    config.load_kube_config()
except config.config_exception.ConfigException:
    try:
        # inside a k8s pod
        config.load_incluster_config()
    except config.config_exception.ConfigException:
        raise Exception(
            "Could not configure kubernetes python client")

api_core: client.CoreV1Api = client.CoreV1Api()
api_customobj: client.CustomObjectsApi = client.CustomObjectsApi()
api_apps: client.AppsV1Api = client.AppsV1Api()
api_batch: client.BatchV1Api = client.BatchV1Api()
api_cron_job: client.BatchV1Api = client.BatchV1Api()
api_policy: client.PolicyV1Api = client.PolicyV1Api()
api_rbac: client.RbacAuthorizationV1Api = client.RbacAuthorizationV1Api()
api_client: client.ApiClient = client.ApiClient()
api_apis: client.ApisApi() = client.ApisApi()

T = TypeVar("T")


def catch_404(f: Callable[..., T]) -> Optional[T]:
    try:
        return f()
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def available_apis():
    return api_apis.get_api_versions()

def k8s_version() -> str:
    api_instance = client.VersionApi(api_client)

    api_response = api_instance.get_code()
    return f"{api_response.major}.{api_response.minor}"


_k8s_cluster_domain = None


def k8s_cluster_domain(logger: Optional[Logger], ns="kube-system") -> str:
    """Get the Kubernetes Cluster's Domain. Can
    be overwritten using environment MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN.

    If this fails to detect it will retry in a blocking loop. This should only
    happen in operator_main before startup. If it constantly fails the process
    will be terminated.
    """

    global _k8s_cluster_domain

    # We use the cached value instead of querying multiple times
    if _k8s_cluster_domain:
        return _k8s_cluster_domain

    # The user could override the lookup using env
    _k8s_cluster_domain = os.getenv("MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN")
    if _k8s_cluster_domain:
        if logger:
            logger.info(f"Environment provided cluster domain: {_k8s_cluster_domain}")
        return _k8s_cluster_domain

    for _ in range(15):
        try:
            # Try reverse lookup via some service having a cluster_ip set. Operator
            # is allowed to list all services and we assume some service is in
            # kube-system namespace.
            ip = next(
                filter(
                    lambda ip: ip,
                    map(
                        lambda service: service.spec.cluster_ip,
                        api_core.list_namespaced_service(ns).items
                    )
                )
            )

            if ip:
                fqdn = socket.gethostbyaddr(ip)[0]
                [_, _, _, _k8s_cluster_domain] = fqdn.split('.', maxsplit=3)
                if logger:
                    logger.info(f"Auto-detected cluster domain: {_k8s_cluster_domain}")

                return _k8s_cluster_domain
        except Exception as e:
            if logger:
                logger.warning("Failed to detect cluster domain. "
                                f"Reason: {e}")
            time.sleep(2)

    logger.error(
        """Failed to automatically identify the cluster domain. If this
        persists try setting MYSQL_OPERATOR_K8S_CLUSTER_DOMAIN via environment."""
    )

    sys.exit(1)
