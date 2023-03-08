#!/bin/bash
# Copyright (c) 2023 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to extract the list of tested k8s versions

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

# extract versions from the weekly pipeline
# samples
#     input: 'minikube-v1.26.1;v1.24.1;kubectl-v1.24.8;4;1;8192',
#     k8s version: v1.24.1
#
#     input: 'k3d-v5.4.1;rancher/k3s:v1.22.7-k3s1;kubectl-v1.22.17;12;1;8192',
#     k8s version: v1.22.7
WEEKLY_PIPELINE_PATH=$SCRIPT_DIR/weekly/Jenkinsfile
K8S_VERSIONS_PATH=$(mktemp)

grep "'minikube-v[0-9]" $WEEKLY_PIPELINE_PATH | awk -F";" '{print $2}' > $K8S_VERSIONS_PATH
grep "'k3d-v[0-9]" $WEEKLY_PIPELINE_PATH | awk -F"[;:]" '{sub(/-k3.*/, ""); print $3}' >> $K8S_VERSIONS_PATH

cat $K8S_VERSIONS_PATH | sort -uV
rm $K8S_VERSIONS_PATH
