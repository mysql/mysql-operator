# Copyright (c) 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

#!/bin/bash

MYSQL_OPERATOR_PYTHON_DEPS="mysql-operator-python-deps:3.7.7"
MYSQL_SHELL_PACKAGE="mysql-shell-8.0.26"
MYSQL_REPO_URL="http://repo.mysql.com"
if [ -n "${1}" ]; then
  MYSQL_REPO_URL="${1}"
fi
if [ -n "${2}" ]; then
  MYSQL_OPERATOR_PYTHON_DEPS="${2}"
fi
sed 's#%%MYSQL_OPERATOR_PYTHON_DEPS%%#'"${MYSQL_OPERATOR_PYTHON_DEPS}"'#g' docker-build/Dockerfile > tmpfile
sed -i 's#%%MYSQL_SHELL_PACKAGE%%#'"${MYSQL_SHELL_PACKAGE}"'#g' tmpfile
sed -i 's#%%MYSQL_REPO_URL%%#'"${MYSQL_REPO_URL}"'#g' tmpfile

mv tmpfile Dockerfile
