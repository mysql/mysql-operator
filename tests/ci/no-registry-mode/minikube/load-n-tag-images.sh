#!/bin/bash
# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


# ensure minikube uses proper operator image
# we've noticed that 'minikube image load' may work weirdly when another image with
# the same name was already loaded in the past, i.e. it may be not updated
# usage: <latest-k8s-operator-image> <mysql-operator-image> <mysql-operator-enterprise-image>
# e.g. jenkins-operator/mysql-operator-k8s:35 mysql/community-operator:8.0.24 mysql/enterprise-operator:8.0.24

if [ "$#" -ne 3 ]; then
    echo "usage: <latest-k8s-operator-image> <mysql-operator-image> <mysql-operator-enterprise-image>"
	exit 1
fi

LATEST_K8S_OPERATOR_IMAGE=$1
MYSQL_OPERATOR_IMAGE=$2
MYSQL_OPERATOR_ENTERPRISE_IMAGE=$3

minikube start
minikube image load $LATEST_K8S_OPERATOR_IMAGE
minikube ssh docker tag $LATEST_K8S_OPERATOR_IMAGE $MYSQL_OPERATOR_IMAGE
minikube ssh docker tag $LATEST_K8S_OPERATOR_IMAGE $MYSQL_OPERATOR_ENTERPRISE_IMAGE
minikube ssh docker images
minikube stop
