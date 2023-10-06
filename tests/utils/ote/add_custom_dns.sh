#!/bin/bash
# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# add custom dns to coredns configmap
# usage: <kubectl-path> <k8s-context> <custom-dns-address>
# kubectl-path - path to the kubectl binary (it can be just 'kubectl' too, if available)
# k8s-context - k8s context used to run kubectl commands
# custom-dns-address - may be used e.g. for k3d clusters in CI behind a proxy
if [ "$#" -ne 3 ]; then
	echo "usage: <kubectl-path> <k8s-context> <custom-dns-address>"
	exit 1
fi

KUBECTL_PATH=$1
K8S_CONTEXT=$2
CUSTOM_DNS_ADDRESS=$3

${KUBECTL_PATH} --context=${K8S_CONTEXT} get -n kube-system cm coredns -o yaml | sed "s/forward . \/etc\/resolv.conf/forward . \/etc\/resolv.conf ${CUSTOM_DNS_ADDRESS}/g" | kubectl replace -f -
${KUBECTL_PATH} --context=${K8S_CONTEXT} get -n kube-system cm coredns -o yaml
