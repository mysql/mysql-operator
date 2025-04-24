#!/usr/bin/env python
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import argparse
import os

from kubernetes import client, config, utils
import yaml

def add_env(container_spec: dict, name, value):
    if not "env" in container_spec:
        container_spec["env"] = []

    container_spec["env"].append({
        "name":  name,
        "value": value
        })

def process(config: object):
    filename = os.path.dirname(os.path.realpath(__file__)) + '/deploy-all.yaml'
    if config.input:
        filename = config.input

    with open(filename) as f:
        docs = list(yaml.load_all(f, Loader=yaml.FullLoader))

    operator = None
    for doc in docs:
        if doc["kind"] == "Deployment" and doc["metadata"]["name"] == "mysql-operator":
            operator = doc

    if not operator:
        raise Exception(f"Input {filename} contains no operator deployment!")

    container_spec = operator["spec"]["template"]["spec"]["containers"][0]
    container_spec["imagePullPolicy"] = config.pull_policy
    if config.prefix:
        container_spec["image"] = container_spec["image"].replace("container-registry.oracle.com/mysql", config.prefix, 1)
        if config.prefix != "mysql":
            add_env(container_spec, "MYSQL_OPERATOR_DEFAULT_REPOSITORY", config.prefix)

    if config.debug:
        add_env(container_spec, "MYSQL_OPERATOR_DEFAULT_DEBUG", "1")

    if config.pull_secret:
        operator["spec"]["template"]["spec"]["imagePullSecrets"] = [{"name": config.pull_secret}]

    if config.source_volume:
        container_spec["volumeMounts"].append({
            "mountPath": "/usr/lib/mysqlsh/python-packages/mysqloperator",
            "name": "operator-source-volume"
        })
        operator["spec"]["template"]["spec"]["volumes"].append({
            "name": "operator-source-volume",
            "hostPath": {
                  "path": config.source_volume,
                  "type": "Directory"
            }
        })

    return docs

def apply_to_k8s(docs: list):
    config.load_kube_config()
    k8s_client = client.ApiClient()
    for doc in docs:
        utils.create_from_dict(k8s_client, doc, verbose=True)

def main():
    parser = argparse.ArgumentParser(description="Generate operator manifest")
    parser.add_argument("input", metavar='INPUT', nargs='?', help="Original file")
    parser.add_argument("--prefix", dest="prefix", help="Repository prefix", default="mysql")
    parser.add_argument("--pull-secret", dest="pull_secret", help="Image Pull Secret", default=None)
    parser.add_argument("--pull-policy", dest="pull_policy", help="Image Pull Policy",
            default="IfNotPresent")
    parser.add_argument("--debug", dest="debug", help="Enable Debug Logging", action='store_true',
        default=False)
    parser.add_argument("--src-volume", dest="source_volume", nargs="?",
            const="/src/mysql-shell/python/kubernetes/mysqloperator", default=None,
            help="Mount local directory as volume containing operator source")
    parser.add_argument("--apply", dest="apply", action="store_true")

    args = parser.parse_args()
    docs = process(args)

    if args.apply:
        apply_to_k8s(docs)
    else:
        print(yaml.dump_all(docs))

if __name__ == "__main__":
    main()
