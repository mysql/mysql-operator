#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

if [ "$#" -eq 1 ]; then
    FILTER=$1
else
    FILTER='ote-'
fi

${SCRIPT_DIR}/purge_containers.sh $FILTER

${SCRIPT_DIR}/purge_volumes.sh $FILTER

${SCRIPT_DIR}/purge_networks.sh $FILTER

${SCRIPT_DIR}/purge_images.sh
