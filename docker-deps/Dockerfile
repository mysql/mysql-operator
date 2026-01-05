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

# we need --no-deps because we need urllib3 @ 2.6.2 because of cves, which is not supported officially by k8s
# "kubernetes 34.1.0 depends on urllib3<2.4.0 and >=1.24.2"
# when k8s officially support it, we need to remove --no-deps. --no-deps should be used with care
RUN python3 -m pip install --target=/tmp/site-packages -r requirements.txt --no-deps

FROM container-registry.oracle.com/os/oraclelinux:8-slim

COPY --from=pip-stage /tmp/site-packages /usr/lib/mysqlsh/python-packages
