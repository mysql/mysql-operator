# Copyright (c) 2021, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

FROM %%MYSQL_OPERATOR_PYTHON_DEPS%%


RUN rpm -U %%MYSQL_REPO_URL%%/mysql80-community-release-el8.rpm \
  && microdnf update && echo "[main]" > /etc/dnf/dnf.conf \
  && microdnf install -y mysql-shell-%%MYSQL_SHELL_VERSION%% \
  && microdnf remove mysql80-community-release \
  && microdnf clean all

RUN groupadd -g27 mysql && useradd -u27 -g27 mysql

RUN mkdir /mysqlsh && chown 2 /mysqlsh

COPY mysqloperator/ /usr/lib/mysqlsh/python-packages/mysqloperator

USER 2

ENV HOME=/mysqlsh
