#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

${SCRIPT_DIR}/purge_containers.sh

${SCRIPT_DIR}/purge_volumes.sh

${SCRIPT_DIR}/purge_networks.sh

${SCRIPT_DIR}/purge_images.sh
