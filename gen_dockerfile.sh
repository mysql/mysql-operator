#!/bin/bash
# Copyright (c) 2021, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


MYSQL_REPO_URL="http://repo.mysql.com"; [ -n "${1}" ] && MYSQL_REPO_URL="${1}"
MYSQL_OPERATOR_PYTHON_DEPS="mysql-operator-python-deps"; [ -n "${2}" ] && MYSQL_OPERATOR_PYTHON_DEPS="${2}"
MYSQL_OPERATOR_PYTHON_DEPS_VERSION="3.10.8"; [ -n "${3}" ] && MYSQL_OPERATOR_PYTHON_DEPS_VERSION="${3}"
MYSQL_SHELL_VERSION=8.4.1; [ -n "${4}" ] && MYSQL_SHELL_VERSION="${4}"
MYSQL_CONFIG_PKG="mysql80-community-release"; [ -n "${5}" ] && MYSQL_CONFIG_PKG="${5}"
MYSQL_SHELL_REPO="mysql-tools-community"; [ -n "${6}" ] && MYSQL_SHELL_REPO="${6}"
ARCH="amd64"; [ -n "${7}" ] && ARCH="${7}"

echo "MYSQL_REPO_URL                     = $MYSQL_REPO_URL"
echo "MYSQL_OPERATOR_PYTHON_DEPS         = $MYSQL_OPERATOR_PYTHON_DEPS"
echo "MYSQL_OPERATOR_PYTHON_DEPS_VERSION = $MYSQL_OPERATOR_PYTHON_DEPS_VERSION"
echo "MYSQL_SHELL_VERSION                = $MYSQL_SHELL_VERSION"
echo "MYSQL_CONFIG_PKG                   = $MYSQL_CONFIG_PKG"
echo "MYSQL_SHELL_REPO                   = $MYSQL_SHELL_REPO"
echo "ARCH                               = $ARCH"

sed 's#%%MYSQL_OPERATOR_PYTHON_DEPS%%#'"${MYSQL_OPERATOR_PYTHON_DEPS}:${MYSQL_OPERATOR_PYTHON_DEPS_VERSION}-${ARCH}"'#g' docker-build/Dockerfile > tmpfile
if [[ $(uname) == "Darwin" ]]; then
  sed -i '' 's#%%MYSQL_SHELL_VERSION%%#'"${MYSQL_SHELL_VERSION}"'#g' tmpfile
  sed -i '' 's#%%MYSQL_REPO_URL%%#'"${MYSQL_REPO_URL}"'#g' tmpfile
  sed -i '' 's#%%MYSQL_CONFIG_PKG%%#'"${MYSQL_CONFIG_PKG}"'#g' tmpfile
  sed -i '' 's#%%MYSQL_SHELL_REPO%%#'"${MYSQL_SHELL_REPO}"'#g' tmpfile
else
  sed -i 's#%%MYSQL_SHELL_VERSION%%#'"${MYSQL_SHELL_VERSION}"'#g' tmpfile
  sed -i 's#%%MYSQL_REPO_URL%%#'"${MYSQL_REPO_URL}"'#g' tmpfile
  sed -i 's#%%MYSQL_CONFIG_PKG%%#'"${MYSQL_CONFIG_PKG}"'#g' tmpfile
  sed -i 's#%%MYSQL_SHELL_REPO%%#'"${MYSQL_SHELL_REPO}"'#g' tmpfile
fi
mv tmpfile Dockerfile
