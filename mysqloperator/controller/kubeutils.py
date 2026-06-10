# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import os
import ssl
import socket
import sys
import time
from logging import Logger
from urllib3 import PoolManager

from typing import Callable, Optional, TypeVar
from kubernetes.client.rest import ApiException, RESTClientObject
from kubernetes import client, config

from .consts import TLS_VALID_CIPHERS
from .watched_namespaces import WatchedNamespaces

context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)

context.set_ciphers(":".join(TLS_VALID_CIPHERS))
context.check_hostname = True
context.verify_mode = ssl.CERT_REQUIRED


class CustomRESTClient(RESTClientObject):
    def __init__(self, configuration):
        super().__init__(configuration)
        self.pool_manager = PoolManager(
            num_pools=configuration.connection_pool_maxsize,
            maxsize=configuration.connection_pool_maxsize,
            cert_reqs=ssl.CERT_REQUIRED if configuration.verify_ssl else ssl.CERT_NONE,
            ca_certs=configuration.ssl_ca_cert,
            cert_file=configuration.cert_file,
            key_file=configuration.key_file,
            ssl_context=context,
        )

configuration = client.Configuration()

try:
    # outside k8s
    config.load_kube_config(client_configuration=configuration)
except config.config_exception.ConfigException:
    try:
        # inside a k8s pod
        config.load_incluster_config(client_configuration=configuration)
    except config.config_exception.ConfigException:
        raise Exception(
            "Could not configure kubernetes python client")

api_client = client.ApiClient(configuration=configuration)
api_client.rest_client = CustomRESTClient(configuration)

api_core: client.CoreV1Api = client.CoreV1Api(api_client)
api_customobj: client.CustomObjectsApi = client.CustomObjectsApi(api_client)
api_apps: client.AppsV1Api = client.AppsV1Api(api_client)
api_batch: client.BatchV1Api = client.BatchV1Api(api_client)
api_cron_job: client.BatchV1Api = client.BatchV1Api(api_client)
api_policy: client.PolicyV1Api = client.PolicyV1Api(api_client)
api_rbac: client.RbacAuthorizationV1Api = client.RbacAuthorizationV1Api(api_client)
api_apis: client.ApisApi = client.ApisApi(api_client)

T = TypeVar("T")


def is_ignorable_event_post_error(exc: ApiException) -> bool:
    body = getattr(exc, "body", "") or ""
    return exc.status == 404 or (
        exc.status == 403 and "NamespaceTerminating" in body
    )


def catch_404(f: Callable[..., T]) -> Optional[T]:
    try:
        return f()
    except ApiException as e:
        if e.status == 404:
            return None
        raise


def available_apis():
    return api_apis.get_api_versions()


def validate_operator_namespaces(namespaces: Optional[str]) -> None:
    WatchedNamespaces(namespaces).validate()


def watched_namespaces(namespaces: Optional[str]) -> list[str]:
    return WatchedNamespaces(namespaces).resolve_operator_namespaces() or []

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
