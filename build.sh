#!/bin/bash
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

ARCH='amd64'; [ -n "$1" ] && ARCH="${1}"
set -e

IMG_TAG=$(./tag.sh)
MAJOR_VERSION=${IMG_TAG:0:3}

docker build --build-arg http_proxy=${http_proxy} --build-arg https_proxy=${https_proxy} --build-arg no_proxy=${no_proxy} -t mysql/community-operator:${MAJOR_VERSION}-$ARCH .
