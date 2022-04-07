# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from logging import Logger
from .innodbcluster import operator_cluster
from .backup import operator_backup
from . import config, utils
from .group_monitor import g_group_monitor
import kopf
import logging


# @kopf.on.login()
# def on_login(**kwargs):
#     return kopf.login_via_client(**kwargs)


@kopf.on.startup()  # type: ignore
def on_startup(settings: kopf.OperatorSettings, logger: Logger, *args, **_):
    utils.log_banner(__file__, logger)
    config.log_config_banner(logger)

    # don't post logger.debug() calls as k8s events
    settings.posting.level = logging.INFO

    # Change the annotation field for storing kopf state, so that the main operator
    # and the pod controller don't collide
    # settings.persistence.finalizer = "operator.mysql.oracle.com/kopf-finalizer"
    # settings.persistence.progress_storage = kopf.AnnotationsProgressStorage(prefix='operator.mysql.oracle.com')
    # settings.persistence.diffbase_storage = kopf.AnnotationsDiffBaseStorage(
    #     name='operator.mysql.oracle.com/last-handled-configuration'
    # )

    operator_cluster.monitor_existing_clusters(logger)

    g_group_monitor.start()


@kopf.on.cleanup()  # type: ignore
def on_shutdown(logger: Logger, *args, **kwargs):
    g_group_monitor.stop()
