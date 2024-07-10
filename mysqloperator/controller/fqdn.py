# Copyright (c) 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

"""Management of FQDNs

For setting up replication we need to configure @@report_host of all MySQL
servers. Also for tasks like backup or bootstraping routers we need to access
MySQL servers using a proper name.

In multi-kuberentes-cluster environments, like submariner or cillium we can't
reliably auto-detect or guess the fully qualified names to any pod or our
headless service.

This module centralizes all related logic.
"""

from logging import Logger
from os import getenv
from typing import TYPE_CHECKING

from .kubeutils import k8s_cluster_domain

if TYPE_CHECKING:
    from .innodbcluster.cluster_api import InnoDBCluster, InnoDBClusterSpec, MySQLPod

FQDN_ENV_NAME = "MYSQL_OPERATOR_FQDN_TEMPLATE"
FQDN_ANNOTATION_NAME = "mysql.oracle.com/fqdn-template"

def operator_service_fqdn_template() -> str:
    """Get the global default FQDN template based on opertor config"""
    return getenv(FQDN_ENV_NAME,
                  "{service}.{namespace}.svc.{domain}")


def idc_service_fqdn_template(spec: 'InnoDBClusterSpec') -> str:
    """Get the service template
    This is used during creation of the IDC and defaults to value from
    operator config and overrides from IDC spec
    """
    template = spec.serviceFqdnTemplate
    if not template:
        template = operator_service_fqdn_template()

    return template


def idc_service_fqdn(cluster: 'InnoDBCluster', logger: Logger) -> str:
    """Get the FQDN Service for a specific IDC with filled template

    For "new" IDCs (created using operator >=8.4) this is from annotation
    on IDC object, for "older" IDC clusters this is based on the defaults
    """
    if FQDN_ANNOTATION_NAME in cluster.annotations:
        template = cluster.annotations[FQDN_ANNOTATION_NAME]
    else:
        template = idc_service_fqdn_template(cluster.parsed_spec)

    return template.format(
        service=f"{cluster.name}-instances",
        namespace=cluster.namespace,
        domain=k8s_cluster_domain(logger)
    )


def pod_fqdn(pod: 'MySQLPod', logger) -> str:
    """Get the FQDN Service template for a specific pod in an Pod

    This reads annotation from a single pod, thus doesn't have to fetch IDC
    """
    if FQDN_ANNOTATION_NAME in pod.metadata.annotations:
        template = pod.metadata.annotations[FQDN_ANNOTATION_NAME]
    else:
        template = idc_service_fqdn_template(pod.get_cluster().parsed_spec)

    return pod.name + "." + template.format(
        service=pod.spec.subdomain,
        namespace=pod.namespace,
        domain=k8s_cluster_domain(logger)
    )
