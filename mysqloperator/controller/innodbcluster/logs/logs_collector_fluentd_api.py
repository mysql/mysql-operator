# Copyright (c) 2023, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import Optional, List, Dict, Union, Tuple
from logging import Logger
from ... import utils
from ...api_utils import dget_bool, dget_dict, dget_list, dget_str
from ...kubeutils import client as api_client
from .logs_types_api import  ServerLogType, GeneralLogSpec, SlowQueryLogSpec, ErrorLogSpec
import yaml
import os


def get_volume_name(volume) -> Optional[str]:
    return volume.get('name') if type(volume) == dict else volume.name #V1Volume

def get_container_name(container) -> Optional[str]:
    return container.get('name') if type(container) == dict else container.name #V1Container


class FluentdMysqlLogSpec:
    def __init__(self, tag: str = None):
        self.tag = tag
        self.options = {}

    def parse(self, spec: dict, prefix: str) -> None:
        if "tag" in spec:
            self.tag = dget_str(spec, "tag", prefix)

        if "options" in spec:
            self.options = dget_dict(spec, "options", prefix, {})


class FluentdRecordAugmentationSpec:
    def __init__(self):
        self.enabled : Optional[bool] = None
        self.labels: Optional[List[Dict]] = None
        self.annotations: Optional[List[Dict]] = None
        self.staticFields: Optional[List[Dict]] = None
        self.podFields: Optional[List[Dict]] = None
        self.resourceFields: Optional[List[Dict]] = None

        # record as in record_transformer terminology
        self.ecords: Dict[str, str] = {}
        self.envs: List[Dict] = []

    def parse(self, spec: dict, prefix: str) -> None:
        if "enabled" in spec:
            self.enabled = dget_bool(spec, "enabled", prefix)

        field = "labels"
        if field in spec:
            self.labels = dget_list(spec, field, prefix, [], content_type=dict)

        field = "annotations"
        if field in spec:
            self.annotations = dget_list(spec, field, prefix, [], content_type=dict)

        field = "staticFields"
        if field in spec:
            self.staticFields = dget_list(spec, field, prefix, [], content_type=dict)

        field = "podFields"
        if field in spec:
            self.podFields = dget_list(spec, field, prefix, [], content_type=dict)

        field = "resourceFields"
        if field in spec:
            self.resourceFields = dget_list(spec, field, prefix, [], content_type=dict)

        self.process_augmentation_fields()


    def process_augmentation_fields(self) -> None:
        if not self.filter_enabled():
            return

        records: Dict[str] = {}
        envs: List[Dict] = []
        if self.labels:
            for recordDef in self.labels:
                labelName = recordDef['labelName']
                env_name = self._mangle_label_for_env(labelName)
                env = {
                    "name" : env_name,
                    "valueFrom": {
                        "fieldRef" : {
                            "fieldPath" : f"metadata.labels['{labelName}']"
                        }
                    }
                }
                envs.append(env)
                records[recordDef["fieldName"]] = """ "${ENV['""" +  env_name + """']}" """

        if self.annotations:
            for recordDef in self.annotations:
                annotationName = recordDef['annotationName']
                env_name = self._mangle_annotation_for_env(annotationName)
                env = {
                    "name" : env_name,
                    "valueFrom": {
                        "fieldRef" : {
                            "fieldPath" : f"metadata.annotations['{annotationName}']"
                        }
                    }
                }
                envs.append(env)
                records[recordDef["fieldName"]] = """ "${ENV['""" +  env_name + """']}" """

        if self.staticFields:
            for recordDef in self.staticFields:
                records[recordDef["fieldName"]] = self.escape_value_for_fluent_conf(recordDef["fieldValue"])

        if self.podFields:
            for recordDef in self.podFields:
                fieldPath = recordDef['fieldPath']
                env_name = self._mangle_pod_field_for_env(fieldPath)
                env = {
                    "name" : env_name,
                    "valueFrom": {
                        "fieldRef" : {
                            "fieldPath" : fieldPath
                        }
                    }
                }
                envs.append(env)
                records[recordDef["fieldName"]] = """ "${ENV['""" +  env_name + """']}" """

        if self.resourceFields:
            for recordDef in self.resourceFields:
                containerName = recordDef['containerName']
                resource = recordDef['resource']
                env_name = self._mangle_resource_field_for_env(f"{containerName}_{resource}")
                env = {
                    "name" : env_name,
                    "valueFrom": {
                        "resourceFieldRef" : {
                            "containerName" : containerName,
                            "resource" : resource
                        }
                    }
                }
                envs.append(env)
                records[recordDef["fieldName"]] = """ "${ENV['""" +  env_name + """']}" """

        self.records = records
        self.envs = envs


    def escape_value_for_fluent_conf(self, value: str) -> str:
        return '"' + value.replace("\\", "\\\\").replace("\"", "\\\"") +'"'

    def filter_enabled(self) -> bool:
        if not self.enabled:
            return False
        if not self.labels and \
           not self.annotations and \
           not self.staticFields and \
           not self.podFields and \
           not self.resourceFields:
            return False

        return True

    def _mangle_uri(self, uri: str) -> str:
        return uri.replace("/", "00").replace(".", "11").replace("-","22").replace("_","33")

    def _mangle_label_for_env(self, label: str) -> str:
        return "MOKLBL00" + self._mangle_uri(label)

    def _mangle_annotation_for_env(self, annotation: str) -> str:
        return "MOKANN00" + self._mangle_uri(annotation)

    def _mangle_pod_field_for_env(self, annotation: str) -> str:
        return "MOKPODF00" + self._mangle_uri(annotation)

    def _mangle_resource_field_for_env(self, annotation: str) -> str:
        return "MOKRESF00" + self._mangle_uri(annotation)

    def get_env_mounts(self) -> List[Dict]:
        return self.envs

    def get_filter(self, tag: str) -> str:
        if not self.filter_enabled():
            return ""

        records = "\n".join(f"{record_name} {record_value}" for record_name, record_value in self.records.items())

        return f"""
<filter {tag}>""" + """
  @type record_transformer
  #  remove_keys prio # `label` contains the human understandable representation
  enable_ruby true
  <record>
    log_type ${ if record.has_key?('log_type'); then record['log_type']; else 1; end }
""" + utils.indent(records, 4) + """
  </record>
</filter>"""


class FluentdSinkSpec:
    def __init__(self):
        self.name: Optional[str] = None
        self.rawConfig: Optional[str] = None

    def parse(self, spec: dict, prefix: str) -> None:
        field = "name"
        if field in spec:
            self.name = dget_str(spec, field, prefix)

        field = "rawConfig"
        if field in spec:
            self.rawConfig = dget_str(spec, field, prefix)

    def get_config(self) -> str:
        return str(self.rawConfig)

class FluentdSinksSpec:
    def __init__(self):
        self.sinks: List[FluentdSinkSpec] = []

    def parse(self, spec: list, prefix: str) -> None:
        for sink_spec in spec:
            sink = FluentdSinkSpec()
            sink.parse(sink_spec, prefix + ".rawConfig")
            self.sinks.append(sink)

    def get_sinks_config(self) -> str:
        if len(self.sinks) == 0:
            return ""

        sinks_configs = "\n".join( [sink.get_config() for sink in self.sinks] )

        ret = f"""<match *.**>
  @type copy
{utils.indent(sinks_configs, 2)}
</match>"""

        return ret

class FluentdSpec:
    fluentd_container_mysql_datadir_path: str = "/var/lib/mysql"
    fluentd_container_config_path: str = "/tmp/fluent.conf"
    fluentd_container_logs_path: str = "/tmp/fluent"
    fluentd_configmap_volume_mount_name = "fluentd-config"

    def __init__(self, namespace: str, cluster_name: str):
        self.namespace: str = namespace
        self.cluster_name: str = cluster_name
        self.generalLog: FluentdMysqlLogSpec = FluentdMysqlLogSpec("general_log")
        self.errorLog: FluentdMysqlLogSpec = FluentdMysqlLogSpec("error_log")
        self.slowQueryLog: FluentdMysqlLogSpec = FluentdMysqlLogSpec("slow_query_log")

        self.additionalFilterConfiguration: Optional[str] = None
        self.recordAugmentation: FluentdRecordAugmentationSpec = FluentdRecordAugmentationSpec()
        self.sinks: FluentdSinksSpec = FluentdSinksSpec()

    def parse(self, spec: dict, prefix: str) -> None:
        field = "generalLog"
        self.generalLog.parse(dget_dict(spec, field, prefix, {}), f"{prefix}.{field}")

        field = "errorLog"
        self.errorLog.parse(dget_dict(spec, field, prefix, {}), f"{prefix}.{field}")

        field = "slowQueryLog"
        self.slowQueryLog.parse(dget_dict(spec, field, prefix, {}), f"{prefix}.{field}")

        field = "recordAugmentation"
        if field in spec:
            self.recordAugmentation.parse(dget_dict(spec, field, prefix, {}), f"{prefix}.{field}")

        field = "additionalFilterConfiguration"
        if field in spec:
            self.additionalFilterConfiguration = dget_str(spec, field, prefix)

        field = "sinks"
        if field in spec:
            self.sinks.parse(dget_list(spec, field, prefix, [], content_type=dict), f"{prefix}.{field}")

    def _remove_containers_from_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet],
                                         container_name: str,
                                         logger: Logger) -> None:
        if isinstance(sts, dict):
            containers = sts["spec"]["template"]["spec"]["containers"]
            sts["spec"]["template"]["spec"]["containers"] = [container for container in containers if get_container_name(container) != container_name]
        elif isinstance(sts, api_client.V1StatefulSet):
            containers = sts.spec.template.spec.containers
            sts.spec.template.spec.containers = [container for container in containers if get_container_name(container) != container_name]

    def _remove_volumes_from_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet],
                                      logger: Logger) -> None:
        volume_name = self.fluentd_configmap_volume_mount_name
        if isinstance(sts, dict):
            volumes = sts["spec"]["template"]["spec"]["volumes"]
            sts["spec"]["template"]["spec"]["volumes"] = [volume for volume in volumes if get_volume_name(volume) != volume_name]
        elif isinstance(sts, api_client.V1StatefulSet):
            volumes = sts.spec.template.spec.volumes
            sts.spec.template.spec.volumes = [volume for volume in volumes if get_volume_name(volume) != volume_name]

    def remove_from_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet],
                             container_name: str,
                             image_name: str,
                             image_envs: List[Dict],
                             logger: Logger) -> None:

        self._remove_containers_from_sts_spec(sts, container_name, logger)
        self._remove_volumes_from_sts_spec(sts, logger)


    def _add_containers_to_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet],
                                    container_name: str,
                                    image_name: str,
                                    image_envs: List[Dict],
                                    enable: bool,
                                    logger: Logger) -> None:

        _, config_file_name = os.path.split(self.fluentd_container_config_path)
        envs_list = self.recordAugmentation.get_env_mounts()
        patch = f"""
- name: {container_name}
  image: {image_name}
  securityContext:
    readOnlyRootFilesystem: false
  env:
{utils.indent(yaml.dump(image_envs),2) if len(image_envs) else ""}
{utils.indent(yaml.dump(envs_list),2) if len(envs_list) else ""}
  volumeMounts:
  - name: datadir
    mountPath: {self.fluentd_container_mysql_datadir_path}
    readOnly: true
  - name: {self.fluentd_configmap_volume_mount_name}
    mountPath: {self.fluentd_container_config_path}
    subPath: {config_file_name}
"""

        if isinstance(sts, dict):
            sts["spec"]["template"]["spec"]["containers"] += yaml.safe_load(patch)
        elif isinstance(sts, api_client.V1StatefulSet):
            # first filter out our old logs container spec
            containers = sts.spec.template.spec.containers
            sts.spec.template.spec.containers = [container for container in containers if get_container_name(container) != container_name]
            sts.spec.template.spec.containers += yaml.safe_load(patch)


    def _add_volumes_to_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet], add: bool,
                                 logger: Logger) -> None:
        _, config_file_name = os.path.split(self.fluentd_container_config_path)
        volume_name = self.fluentd_configmap_volume_mount_name
        patch = f"""
- name: {volume_name}
  configMap:
    name: {self.cluster_name}-fluentd-conf
    defaultMode: 0644
    items:
    - key: {config_file_name}
      path: {config_file_name}
"""
        # During first time creation
        if isinstance(sts, dict):
            sts["spec"]["template"]["spec"]["volumes"] += yaml.safe_load(patch)
        elif isinstance(sts, api_client.V1StatefulSet):
            # first filter out our old logs volumes spec
            sts.spec.template.spec.volumes = [volume for volume in sts.spec.template.spec.volumes if volume and get_volume_name(volume) != volume_name]
            sts.spec.template.spec.volumes += yaml.safe_load(patch)

    def add_to_sts_spec(self, sts: Union[dict, api_client.V1StatefulSet],
                        container_name: str,
                        image_name: str,
                        image_envs: List[Dict],
                        add: bool,
                        logger: Logger) -> None:

        self._add_containers_to_sts_spec(sts, container_name, image_name, image_envs, add, logger)
        self._add_volumes_to_sts_spec(sts, add, logger)


    def _get_general_log_fluent_conf(self, general_log: GeneralLogSpec) -> Optional[str]:
        if not general_log.collect:
            return ""

        tag = self.generalLog.tag
        options = "\n".join(f'{option_name} {option_value}' for option_name, option_value in self.generalLog.options.items())
        filter = self.recordAugmentation.get_filter(tag)

        parser = """
<parse>
  @type multiline
  # 2023-02-02T10:01:54.836730Z         6 Prepare   CREATE TABLE IF NOT EXISTS slave_worker_info
  format_firstline /^\\d{4}-\\d{1,2}-\\d{1,2}.*/
  format1 /^(?<time>\\d{4}-\\d{1,2}-\\d{1,2}\\S+)\\s+(?<thread>\\d+)\\s+(?<command_type>\\w+)\\s+(?<command>.*)\\s{0,}/
  time_key time
  time_format %iso8601
</parse>"""

        return f"""

#GENERAL QUERY LOG SOURCE
<source>
  @type tail
  refresh_interval 5
  path  {self.fluentd_container_mysql_datadir_path}/{general_log.fileName}
  read_from_head true
  pos_file {self.fluentd_container_logs_path}/{general_log.fileName}.pos
  multiline_flush_interval 5s
  tag {tag}
{utils.indent(options, 2)}
{utils.indent(parser, 2)}
</source>
{filter}
"""

    def _get_slow_log_fluent_conf(self, slow_log: SlowQueryLogSpec) -> Optional[str]:
        if not slow_log.collect:
            return ""

        tag = self.slowQueryLog.tag

        filter = self.recordAugmentation.get_filter(tag)

        # `host` may be zero is the `ip` cannot be reverse resolved
        # Here is a line that has proper `host`
        # `# User@Host: mysqladmin[mysqladmin] @ 10-42-0-4.mysql-operator.mysql-operator.svc.cluster.local [10.42.0.4]  Id:    21`
        # Here is a line that has empty `host`
        # ```
        # # User@Host: mysqlrouter[mysqlrouter] @  [10.42.0.6]  Id:  1453
        # ```
        # In addition, GR uses mysql_admin_session, which uses SQL Sessions in the server and in this case no user/current_user is set
        # and there is neither host, nor IP, only connection Id
        # Example
        # ```
        # # Time: 2023-05-26T13:41:59.414433Z
        # # User@Host: [] @  []  Id:    30
        # # Query_time: 0.000038  Lock_time: 0.000000 Rows_sent: 0  Rows_examined: 0
        # SET timestamp=1685108519;
        # SET SESSION group_replication_consistency= EVENTUAL;
        # ```
        parser = """
<parse>
  @type multiline
  # "# Time: 2023-01-31T14:14:44.570056Z\\n# User@Host: root[root] @ localhost []  Id:   185\\n# Query_time: 5.001852  Lock_time: 0.000000 Rows_sent: 1  Rows_examined: 1\\nSET timestamp=1675174479;\\nselect sleep(5);
  format_firstline /^#\\s+Time:/
  format1 /#\\s+Time:\\s+(?<time>\\S+).*/
  format2 /#\\s+User@Host:\\s+(?<user>[^\\s]*)\\[(?<current_user>[^\\s]*)\\]\\s+@\\s+(?<host>[^\\s]*)\\s+\\[(?<ip>[\\d\\.]{0,})\\]\\s+Id:\\s+(?<id>\\d+)\\s+/
  format3 /#\\s+Query_time:\\s+(?<query_time>\\d+\\.\\d+)\\s+Lock_time:\\s+(?<lock_time>\\d+\\.\\d+)\\s+Rows_sent:\\s+(?<rows_sent>\\d+)\\s+Rows_examined:\\s+(?<rows_examined>\\d+)\\s+/
  format4 /(((use\\s(?<schema>\\S+))|(SET\\s+timestamp=(?<timestamp>\\d+))|(?<query>.*));\\s+)+/
  format5 /(?<query>.*)/
  types id:integer,query_time:float,lock_time:float,rows_sent:integer,rows_examined:integer,timestamp:integer
  time_key time
  time_format %iso8601
</parse>"""

        return f"""

#SLOW QUERY LOG SOURCE
<source>
  @type tail
  refresh_interval 5
  path {self.fluentd_container_mysql_datadir_path}/{slow_log.fileName}
  read_from_head true
  pos_file {self.fluentd_container_logs_path}/{slow_log.fileName}.pos
  multiline_flush_interval 5s
  tag {tag}
  emit_unmatched_lines false
  #  enable_watch_timer true
{utils.indent(parser, 2)}
</source>
{filter}
"""

    def _get_error_log_fluent_conf(self, error_log: ErrorLogSpec) -> Optional[str]:
        if not error_log.collect:
            return ""

        tag = self.errorLog.tag

        filter = self.recordAugmentation.get_filter(tag)

        return f"""

#ERROR LOG SOURCE
<source>
  @type tail
  path {self.fluentd_container_mysql_datadir_path}/{error_log.error_log_name}.*.json
  refresh_interval 5
  read_from_head true
  pos_file {self.fluentd_container_logs_path}/{error_log.error_log_name}.pos
  tag {tag}
  <parse>
    @type json
    time_key time
    time_format %iso8601
  </parse>
</source>
{filter}
"""

    def _get_additional_filter_configuration(self) -> Optional[str]:
        if not self.additionalFilterConfiguration:
            return None
        return f"""

# ADDITIONAL USER PROVIDED FILTER
{self.additionalFilterConfiguration}
"""

    def _get_sinks_fluent_conf(self) -> Optional[str]:
        return self.sinks.get_sinks_config()

    def get_config_maps(self, logs: dict) -> List[Tuple[str, Optional[Dict]]]:
        general_log: GeneralLogSpec = logs[ServerLogType.GENERAL.value]
        error_log: ErrorLogSpec = logs[ServerLogType.ERROR.value]
        slow_log: SlowQueryLogSpec = logs[ServerLogType.SLOW_QUERY.value]

        conf = self._get_general_log_fluent_conf(general_log) + \
               self._get_slow_log_fluent_conf(slow_log) + \
               self._get_error_log_fluent_conf(error_log)

        if conf:
            additional_filter = self._get_additional_filter_configuration()
            if additional_filter:
                conf += additional_filter

            conf += self._get_sinks_fluent_conf()
            cm_name = self.cluster_name + '-fluentd-conf' # must be the same as in get_config_maps_names

        cm = {
                'apiVersion' : "v1",
                'kind': 'ConfigMap',
                'metadata': {
                    'name': cm_name
                },
                'data' : {
                    'fluent.conf' : f"###### Generated by the MySQL Operator for Kubernetes ######{conf}"
                }
            } if conf else None
        return [(cm_name, cm)]
