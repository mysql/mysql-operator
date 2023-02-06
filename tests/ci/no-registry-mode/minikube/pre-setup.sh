#!/bin/bash
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# usage: <latest-k8s-operator-image> <mysql-operator-image> <mysql-operator-enterprise-image>
# e.g. jenkins-operator/mysql-operator-k8s:35 mysql/community-operator:8.0.24 mysql/enterprise-operator:8.0.24

if [ "$#" -ne 3 ]; then
    echo "usage: <latest-k8s-operator-image> <mysql-operator-image> <mysql-operator-enterprise-image>"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))
CI_DIR=$SCRIPT_DIR/..

minikube delete
minikube start

# patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$CI_DIR/jobs/auxiliary/show-progress.sh" 40 30 &
SHOW_PROGRESS_JOB=$!
source $SCRIPT_DIR/load-n-tag-images.sh $1 $2 $3
kill $SHOW_PROGRESS_JOB
