# Copyright (c) 2020, 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import kopf
import logging
import os
from pathlib import Path
from logging import Logger
from . import config, utils, consts
from .innodbcluster import cluster_api
from .group_monitor import g_group_monitor
from .innodbcluster import operator_cluster
from .watched_namespaces import WatchedNamespaces


# These have to be imported so that kopf sees the annotations in those files
from .backup import operator_backup

# @kopf.on.login()
# def on_login(**kwargs):
#     return kopf.login_via_client(**kwargs)


READY_FILE = Path("/tmp/mysql-operator-ready")


def get_startup_clusters() -> list[cluster_api.InnoDBCluster]:
    watched = WatchedNamespaces(os.getenv("OPERATOR_NAMESPACES", ""))
    watched.validate()
    namespaces = watched.resolve_operator_namespaces()
    return cluster_api.get_all_clusters(namespaces)


@kopf.on.startup()  # type: ignore
def on_startup(settings: kopf.OperatorSettings, logger: Logger, *args, **_):
    READY_FILE.unlink(missing_ok=True)

    utils.log_banner(__file__, logger)
    config.log_config_banner(logger)

    settings.posting.level = logging.INFO
    settings.posting.enabled = False

    clusters = get_startup_clusters()
    logger.info(f"CLUSTERS={[f'{cluster.namespace}/{cluster.name}' for cluster in clusters]}")

    operator_cluster.ensure_backup_schedules_use_current_image(clusters, logger)
    operator_cluster.ensure_switchover_rbac_uptodate(clusters, logger)
    operator_cluster.ensure_meb_self_signed_tls_uptodate(clusters, logger)
    operator_cluster.monitor_existing_clusters(clusters, logger)
    operator_cluster.refresh_existing_cluster_status(clusters, logger)
    operator_cluster.ensure_router_accounts_are_uptodate(clusters, logger)

    for cluster in clusters:
        cluster.info(
            action="HandlingOperatorRestart",
            reason="OperatorRestarted",
            message=f"Handling operator restarted for cluster {cluster.namespace}/{cluster.name}",
        )

    g_group_monitor.start()

    READY_FILE.touch()


@kopf.on.cleanup()  # type: ignore
def on_shutdown(logger: Logger, *args, **kwargs):
    g_group_monitor.stop()
    if g_group_monitor._thread_started:
        g_group_monitor.join(timeout=10)
        if g_group_monitor.is_alive():
            logger.warning("Group monitor did not stop within timeout")
