# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

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
api_cron_job: client.BatchV1beta1Api = client.BatchV1beta1Api()
api_policy: client.PolicyV1beta1Api = client.PolicyV1beta1Api()
api_rbac: client.RbacAuthorizationV1Api = client.RbacAuthorizationV1Api()

T = TypeVar("T")


def catch_404(f: Callable[..., T]) -> Optional[T]:
    try:
        return f()
    except ApiException as e:
        if e.status == 404:
            return None
        raise
