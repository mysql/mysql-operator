# Copyright (c) 2020, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

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

api_core = client.CoreV1Api()
api_customobj = client.CustomObjectsApi()
api_apps = client.AppsV1Api()
api_batch = client.BatchV1Api()
