# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

#!/bin/bash

PYTHON_TARBALL=$1
PYTHON_ROOT=$2
PYTHON_BASE_DIR=$3
ARCH=amd64; [ -n "$4" ] && ARCH=$4

py_ver=`echo $PYTHON_ROOT | cut -d'-' -f2`

docker build --build-arg PYTHON_TARBALL=${PYTHON_TARBALL} --build-arg PYTHON_ROOT=${PYTHON_ROOT} --build-arg PYTHON_BASE_DIR=${PYTHON_BASE_DIR} -t mysql/mysql-operator-python-deps:$py_ver-$ARCH .
