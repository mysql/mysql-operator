#!/bin/bash
# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


# load and tag operator from archive
# usage: <k8s-operator-archive> <mysql-operator-image> <mysql-operator-enterprise-image> <deploy-operator-path>
# e.g.: operator-k8s-operator.bz2 mysql/community-operator:8.0.24 mysql/enterprise-operator:8.0.24 ./python/kubernetes/deploy/deploy-operator.yaml

if [ "$#" -ne 4 ]; then
    echo "usage: <k8s-operator-archive> <mysql-operator-image> <mysql-operator-enterprise-image> <deploy-operator-path>"
	exit 1
fi

K8S_OPERATOR_ARCHIVE="$1"
MYSQL_OPERATOR_IMAGE=$2
MYSQL_OPERATOR_ENTERPRISE_IMAGE=$3
DEPLOY_OPERATOR_PATH=$4

# unpack mysql-shell-operator image
read K8S_OPERATOR_IMAGE <<< $(bunzip2 -kc "$K8S_OPERATOR_ARCHIVE" | docker load | awk '{ print $3 }')

# tag images used by tests
docker tag $K8S_OPERATOR_IMAGE $MYSQL_OPERATOR_IMAGE
docker tag $K8S_OPERATOR_IMAGE $MYSQL_OPERATOR_ENTERPRISE_IMAGE

DEPLOY_OPERATOR_IMAGE=$(grep image: "$DEPLOY_OPERATOR_PATH" | awk '{ print $2 }')
docker tag $K8S_OPERATOR_IMAGE $DEPLOY_OPERATOR_IMAGE

echo $K8S_OPERATOR_IMAGE
