#!/bin/bash
# Copyright (c) 2020, 2021, Oracle and/or its affiliates.

# load and tag operator from archive
# usage: <k8s-shell-archive> <mysql-shell-image> <mysql-shell-enterprise-image> <deploy-operator-path>
# e.g.: shell-k8s-shell.bz2 mysql/mysql-shell:8.0.24 mysql/mysql-shell-commercial:8.0.24 ./python/kubernetes/deploy/deploy-operator.yaml

if [ "$#" -ne 4 ]; then
    echo "usage: <k8s-shell-archive> <mysql-shell-image> <mysql-shell-enterprise-image> <deploy-operator-path>"
	exit 1
fi

K8S_SHELL_ARCHIVE="$1"
MYSQL_SHELL_IMAGE=$2
MYSQL_SHELL_ENTERPRISE_IMAGE=$3
DEPLOY_OPERATOR_PATH=$4

# unpack mysql-shell-operator image
read K8S_SHELL_IMAGE <<< $(bunzip2 -kc "$K8S_SHELL_ARCHIVE" | docker load | awk '{ print $3 }')

# tag images used by tests
docker tag $K8S_SHELL_IMAGE $MYSQL_SHELL_IMAGE
docker tag $K8S_SHELL_IMAGE $MYSQL_SHELL_ENTERPRISE_IMAGE

DEPLOY_OPERATOR_IMAGE=$(grep image: "$DEPLOY_OPERATOR_PATH" | awk '{ print $2 }')
docker tag $K8S_SHELL_IMAGE $DEPLOY_OPERATOR_IMAGE

echo $K8S_SHELL_IMAGE
