#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

NETWORK_PATTERN=$1

docker network ls -q -f "name=${NETWORK_PATTERN}" \
  | xargs -r -n 1 ${SCRIPT_DIR}/remove_network.sh
