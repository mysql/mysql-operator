#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to build an enterprise image, until we have it in our internal hub
# usage: base-operator-image enterprise-operator-image
# e.g. our.internal.repo/qa/mysql-operator:8.0.29-2.0.4 our.internal.repo/qa/enterprise-operator:8.0.29-2.0.4

set -vx

if [ "$#" -ne 2 ]; then
    echo "usage: <base-operator-image> <enterprise-operator-image>"
	exit 1
fi

BASE_OPERATOR_IMAGE=$1
ENTERPRISE_OPERATOR_IMAGE=$2

CONTAINER=$(docker container create ${BASE_OPERATOR_IMAGE})
sed 's/Edition.community/Edition.enterprise/' < mysqloperator/controller/config.py > mysqloperator/controller/config.enterprise.py
docker container cp mysqloperator/controller/config.enterprise.py ${CONTAINER}:/usr/lib/mysqlsh/python-packages/mysqloperator/controller/config.py
docker container commit ${CONTAINER} ${ENTERPRISE_OPERATOR_IMAGE}
docker container rm ${CONTAINER}
