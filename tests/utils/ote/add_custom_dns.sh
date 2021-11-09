#!/bin/bash
# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# add custom dns to coredns configmap
# usage: <custom-dns-address>
# may be used e.g. for k3d clusters in CI behind a proxy
if [ "$#" -ne 1 ]; then
	echo "usage: <custom-dns-address>"
	exit 1
fi

kubectl get -n kube-system cm coredns -o yaml | sed "s/forward . \/etc\/resolv.conf/forward . \/etc\/resolv.conf $1/g" | kubectl replace -f -
kubectl get -n kube-system cm coredns -o yaml
