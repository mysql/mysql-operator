#!/bin/bash
# Copyright (c) 2025, Oracle and/or its affiliates.
#

set -e

cp Dockerfile tmpfile

cat >>tmpfile  <<EOT
USER 0
COPY internal/mysqloperator/clusterset_failover_main.py /usr/lib/mysqlsh/python-packages/mysqloperator/clusterset_failover_main.py
USER 2

ENV MYSQL_OPERATOR_ENTERPRISE=1
EOT

mv tmpfile Dockerfile.enterprise
