# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

FROM container-registry.oracle.com/os/oraclelinux:8 AS pip-stage

ARG PYTHON_TARBALL
ARG PYTHON_ROOT
ARG PYTHON_BASE_DIR

RUN dnf install -y gcc git tar
COPY ${PYTHON_TARBALL} .
RUN mkdir -p  ${PYTHON_BASE_DIR} && cd  ${PYTHON_BASE_DIR} && tar xzf /${PYTHON_TARBALL}
ENV PATH=${PYTHON_BASE_DIR}/${PYTHON_ROOT}/bin:$PATH
ENV LD_LIBRARY_PATH=${PYTHON_BASE_DIR}/${PYTHON_ROOT}/lib

COPY requirements.txt .

RUN python3 -m pip install --target=/tmp/site-packages -r requirements.txt

FROM container-registry.oracle.com/os/oraclelinux:8-slim

COPY --from=pip-stage /tmp/site-packages /usr/lib/mysqlsh/python-packages
