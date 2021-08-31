#!/bin/bash
# Copyright (c) 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# usage: <latest-k8s-shell-image> <mysql-shell-image> <mysql-shell-enterprise-image>
# e.g. jenkins-shell/mysql-shell-k8s:35 mysql/mysql-shell:8.0.24 mysql/mysql-shell-commercial:8.0.24

if [ "$#" -ne 3 ]; then
    echo "usage: <latest-k8s-shell-image> <mysql-shell-image> <mysql-shell-enterprise-image>"
	exit 1
fi

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))
CI_DIR=$SCRIPT_DIR/..

minikube delete
minikube start

# patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$CI_DIR/show-progress.sh" 40 30 &
SHOW_PROGRESS_JOB=$!
source $SCRIPT_DIR/load-n-tag-images.sh $1 $2 $3
kill $SHOW_PROGRESS_JOB
