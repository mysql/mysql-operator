#!/bin/bash
# Copyright (c) 2021, 2022 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


MYSQL_REPO_URL="http://repo.mysql.com"
MYSQL_OPERATOR_PYTHON_DEPS="mysql-operator-python-deps"
MYSQL_OPERATOR_PYTHON_DEPS_VERSION="3.9.5"
MYSQL_SHELL_VERSION=8.0.30
MYSQL_CONFIG_PKG="mysql80-community-release"
if [ -n "${1}" ]; then
  MYSQL_REPO_URL="${1}"
fi
if [ -n "${2}" ]; then
  MYSQL_OPERATOR_PYTHON_DEPS="${2}"
fi
if [ -n "${3}" ]; then
 MYSQL_SHELL_VERSION="${3}"
fi
if [ -n "${4}" ]; then
 MYSQL_CONFIG_PKG="${4}"
fi
sed 's#%%MYSQL_OPERATOR_PYTHON_DEPS%%#'"${MYSQL_OPERATOR_PYTHON_DEPS}:${MYSQL_OPERATOR_PYTHON_DEPS_VERSION}"'#g' docker-build/Dockerfile > tmpfile
sed -i 's#%%MYSQL_SHELL_VERSION%%#'"${MYSQL_SHELL_VERSION}"'#g' tmpfile
sed -i 's#%%MYSQL_REPO_URL%%#'"${MYSQL_REPO_URL}"'#g' tmpfile
sed -i 's#%%MYSQL_CONFIG_PKG%%#'"${MYSQL_CONFIG_PKG}"'#g' tmpfile

mv tmpfile Dockerfile
