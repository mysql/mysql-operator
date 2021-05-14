FROM oraclelinux:8.1 AS pip-stage

ARG PYTHON_TARBALL
ARG PYTHON_ROOT
ARG PYTHON_BASE_DIR

RUN dnf install -y gcc git tar
COPY ${PYTHON_TARBALL} .
RUN mkdir -p  ${PYTHON_BASE_DIR} && cd  ${PYTHON_BASE_DIR} && tar xzf /${PYTHON_TARBALL}
ENV PATH=${PYTHON_BASE_DIR}/${PYTHON_ROOT}/bin:$PATH
ENV LD_LIBRARY_PATH=${PYTHON_BASE_DIR}/${PYTHON_ROOT}/lib

COPY requirements.txt .

RUN pip3 install --target=/tmp/site-packages -r requirements.txt

FROM oraclelinux:8-slim

ARG MYSQL_SHELL_RPM=http://repo.mysql.com/mysql-shell-comunity-8.0.26.el8.x86_64.rpm
# TODO this won't work if rpm came via http ;-)
COPY ${MYSQL_SHELL_RPM} .

RUN microdnf update && microdnf install  -y python3 && microdnf clean all
RUN rpm -i ${MYSQL_SHELL_RPM}

COPY --from=pip-stage /tmp/site-packages /usr/lib/mysqlsh/python-packages

COPY mysqloperator/ /usr/lib/mysqlsh/python-packages/mysqloperator

