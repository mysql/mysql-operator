# Copyright (c) 2023, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from enum import Enum
from typing import Optional, Union, List, Callable, Dict, Tuple
from logging import Logger
from ...api_utils import dget_dict, dget_str, dget_list, ApiSpecError
from ...kubeutils import client as api_client
from .logs_collector_fluentd_api import FluentdSpec
from .logs_types_api import ServerLogType, GeneralLogSpec, ErrorLogSpec, SlowQueryLogSpec, MySQLLogSpecBase

lc_default_container_name = "logcollector"

class LogCollectorSpec:
    def __init__(self, namespace: str, cluster_name: str):
        self.namespace: str = namespace
        self.cluster_name: str = cluster_name
        self.image: Optional[str] = None
        self.container : Optional[str] = lc_default_container_name
        self.envs: dict = {}
        self.collector: Union[FluentdSpec, None] = None
        self._prefix: str = None

    def parse(self, spec: dict, prefix: str, logger: Logger) -> None:
        if not spec:
            return

        self._prefix = prefix

        if "image" in spec:
            self.image = dget_str(spec, "image", prefix)

        if "containerName" in spec:
            self.container = dget_str(spec, "containerName", prefix)

        if "env" in spec:
            self.envs = dget_list(spec, "env", prefix, content_type=dict)

        if "fluentd" in spec:
            self.collector = FluentdSpec(self.namespace, self.cluster_name)
            self.collector.parse(dget_dict(spec, "fluentd", prefix, {}), f"{prefix}.fluentd")

    def validate(self, logHandlers: Dict[ServerLogType, MySQLLogSpecBase]) -> None:
        if self.collect(logHandlers) and self.image is None:
            raise ApiSpecError(f"No collector image set")
        if self.collector is None and self._prefix:
            raise ApiSpecError(f"No collector configured under {self._prefix}")

    @property
    def image_name(self) -> str:
        return self.image

    @property
    def container_name(self) -> str:
        return self.container if self.container else lc_default_container_name

    def collect(self, logHandlers: Dict[ServerLogType, MySQLLogSpecBase]) -> bool:
        return True in [logger.collect for logger in logHandlers.values()]

    def remove_from_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet], logHandlers: Dict[ServerLogType, MySQLLogSpecBase], logger: Logger) -> None:
        if self.collect(logHandlers):
            if self.collector is None:
                raise ApiSpecError(f"No collector configured")
            self.collector.remove_from_sts_spec(sts, self.container_name, self.image_name, self.envs, logger)

    def add_to_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet], logHandlers: Dict[ServerLogType, MySQLLogSpecBase], add: bool, logger: Logger) -> None:
        if self.collect(logHandlers):
            if self.collector is None:
                raise ApiSpecError(f"No collector configured")
            self.collector.add_to_sts_spec(sts, self.container_name, self.image_name, self.envs, add, logger)

    def get_config_maps(self, logHandlers: Dict[ServerLogType, MySQLLogSpecBase]) -> List[Dict]:
        return self.collector.get_config_maps(logHandlers) if self.collect(logHandlers) and self.collector else []


class LogsSpec:
    def __init__(self, namespace: str, cluster_name: str):
        self.logs: Dict[ServerLogType, MySQLLogSpecBase] = {
            ServerLogType.GENERAL.value: GeneralLogSpec(),
            ServerLogType.ERROR.value: ErrorLogSpec(),
            ServerLogType.SLOW_QUERY.value: SlowQueryLogSpec(),
        }
        self.cluster_name: str = cluster_name
        self.collector: LogCollectorSpec = LogCollectorSpec(namespace, cluster_name)
        self.cm_name = self.cluster_name + "-logs-config"

    def parse(self, spec: dict, prefix: str, logger: Logger) -> None:
        for (logName, logHandler) in self.logs.items():
            if logName in spec:
                logSpec = dget_dict(spec, logName, prefix, None)
                logHandler.parse(logSpec, prefix + f".{logName}", logger)

        if "collector" in spec:
            self.collector.parse(dget_dict(spec, "collector", prefix, {}), prefix + ".collector", logger)

    def validate(self) -> None:
        for log in self.logs.values():
            log.validate()

        self.collector.validate(self.logs)

    @property
    def enabled(self) -> bool:
        return True in [logger.enabled for logger in self.logs.values()]

    @property
    def collect(self) -> bool:
        return True in [logger.collect for logger in self.logs.values()]

    def get_add_to_initconf_cb(self) -> Optional[Callable[[Dict, str, Logger], None]]:
        def cb(configmap: dict, prefix: str, logger: Logger) -> None:
            pass

        return cb

    def get_remove_from_sts_cb(self) -> Optional[Callable[[Union[dict, api_client.V1StatefulSet], Logger], None]]:
        return (lambda sts, logger: self.collector.remove_from_sts_spec(sts, self.logs, logger))

    def get_add_to_sts_cb(self) -> Optional[Callable[[Union[dict, api_client.V1StatefulSet], Logger], None]]:
        def cb(sts: Union[dict, api_client.V1StatefulSet], logger: Logger) -> None:
            enabled = self.enabled
            for logName in self.logs:
                container_name = "mysql"
                self.logs[logName].add_to_sts_spec(sts, container_name, self.cm_name, enabled, logger)
            self.collector.add_to_sts_spec(sts, self.logs, enabled, logger)
        return cb

    def get_configmaps_cb(self) -> Optional[Callable[[str, Logger], Optional[List[Tuple[str, Optional[Dict]]]]]]:
        def cb(prefix: str, logger: Logger) -> Optional[List[Tuple[str, Optional[Dict]]]]:

            logs_configmap = {
                    'apiVersion' : "v1",
                    'kind': 'ConfigMap',
                    'metadata': {
                        'name': self.cm_name, # must be the same as in get_config_maps_names
                    },
                    'data' : {
                    }
                }

            for logName in self.logs:
                cm_data = self.logs[logName].get_cm_data(logger)
                for cm_key in cm_data:
                    logs_configmap["data"][f"{prefix}{cm_key}"] = cm_data[cm_key]

            return [(self.cm_name, logs_configmap)] + self.collector.get_config_maps(self.logs)

        return cb
