#!/bin/bash
# Copyright (c) 2025, 2026, Oracle and/or its affiliates.
#

set -e

cp Dockerfile tmpfile

cat >>tmpfile  <<EOT
ENV MYSQL_OPERATOR_ENTERPRISE=1
EOT

mv tmpfile Dockerfile.enterprise
