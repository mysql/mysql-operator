#!/bin/bash
# Copyright (c) 2022, 2023 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

if [ "$#" -eq 1 ]; then
	FILTER=$1
else
	FILTER='ote-'
fi

${SCRIPT_DIR}/purge_containers.sh "$FILTER" "$MAX_ALLOWED_CONTAINER_LIFETIME"

${SCRIPT_DIR}/purge_volumes.sh "$FILTER" "$MAX_ALLOWED_CONTAINER_LIFETIME"

${SCRIPT_DIR}/purge_networks.sh "$FILTER" "$MAX_ALLOWED_CONTAINER_LIFETIME"

${SCRIPT_DIR}/purge_images.sh

${SCRIPT_DIR}/purge_oci_vault_secrets.sh $OPERATOR_TEST_OCI_CONFIG_PATH $OPERATOR_TEST_VAULT_CONFIG_PATH
