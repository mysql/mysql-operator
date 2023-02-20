#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to build a community image from enterprise image, needed for SysQA flow for testing enterprise images
# usage: base-enterprise-operator-image community-operator-image
# e.g. our.internal.repo/qa/enterprise-operator:8.0.29-2.0.4 our.internal.repo/qa/community-operator:8.0.29-2.0.4

set -vx

if [ "$#" -ne 2 ]; then
    echo "usage: <base-enterprise-operator-image> <community-operator-image>"
	exit 1
fi

BASE_ENTERPRISE_OPERATOR_IMAGE=$1
COMMUNITY_OPERATOR_IMAGE=$2

CONTAINER=$(docker container create ${BASE_ENTERPRISE_OPERATOR_IMAGE})
sed 's/Edition.enterprise/Edition.community/' < mysqloperator/controller/config.py > mysqloperator/controller/config.community.py
docker container cp mysqloperator/controller/config.community.py ${CONTAINER}:/usr/lib/mysqlsh/python-packages/mysqloperator/controller/config.py
docker container commit ${CONTAINER} ${COMMUNITY_OPERATOR_IMAGE}
if [ $? -ne 0 ]; then
	echo "cannot build community dev-image ${COMMUNITY_OPERATOR_IMAGE}"
	exit 2
fi
docker container rm ${CONTAINER}
