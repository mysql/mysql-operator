#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# script generates info about used kubectl - client and server version, full path
# usage: <kubectl-path> <k8s-context> <output-path>
# kubectl-path - path to the kubectl binary (it can be just 'kubectl' too, if available)
# k8s-context - k8s context used to run kubectl commands
# output-path - path to a file where info should be stored

set -vx

if [ "$#" -ne 3 ]; then
	echo "usage: <kubectl-path> <k8s-context> <output-path>"
	exit 1
fi

KUBECTL_PATH=$1
K8S_CONTEXT=$2
OUTPUT_PATH=$3

# kubectl (client, server, path)
KUBECTL_VERSION=$(${KUBECTL_PATH} --context=${K8S_CONTEXT} version --client -o json | jq '.clientVersion.gitVersion')
echo "kubectl client: ${KUBECTL_VERSION}" > $OUTPUT_PATH
KUBECTL_VERSION=$(${KUBECTL_PATH} --context=${K8S_CONTEXT} version -o json | jq '.serverVersion.gitVersion')
echo "kubectl server: ${KUBECTL_VERSION}" >> $OUTPUT_PATH
KUBECTL_FULL_PATH=$(which ${KUBECTL_PATH})
echo "path: ${KUBECTL_FULL_PATH}" >> $OUTPUT_PATH
cat ${OUTPUT_PATH}
