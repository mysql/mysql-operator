# Copyright (c) 2021, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

FROM %%MYSQL_OPERATOR_PYTHON_DEPS%%


RUN rpm -U %%MYSQL_REPO_URL%%/%%MYSQL_CONFIG_PKG%%-el8.rpm \
  && microdnf update && echo "[main]" > /etc/dnf/dnf.conf \
  && microdnf install --enablerepo=%%MYSQL_SHELL_REPO%% -y glibc-langpack-en mysql-shell-%%MYSQL_SHELL_VERSION%% \
  && microdnf remove %%MYSQL_CONFIG_PKG%% \
  && microdnf clean all

RUN groupadd -g27 mysql && useradd -u27 -g27 mysql

RUN mkdir /mysqlsh && chown 2 /mysqlsh

COPY mysqloperator/ /usr/lib/mysqlsh/python-packages/mysqloperator

# Workaround for BC issue with newest Python library for Kubernetes
# See move here: https://github.com/kubernetes-client/python/issues/1718
RUN sed -i "s/available_replicas=None,/available_replicas=0,/" /usr/lib/mysqlsh/python-packages/kubernetes/client/models/v1_stateful_set_status.py


USER 2

ENV HOME=/mysqlsh
