#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

SCRIPT_DIR=$(dirname $(readlink -f "${BASH_SOURCE[0]}"))

if [ "$#" -ne 2 ]; then
	echo "usage: <oci-config-path> <vault-config-path>"
	exit 1
fi

OCI_CONFIG_PATH=$1
VAULT_CONFIG_PATH=$2

VAULT_ID=$(grep virtual_vault $VAULT_CONFIG_PATH | awk -F"=" '{print $2}')
COMPARTMENT_ID=$(grep compartment $VAULT_CONFIG_PATH | awk -F"=" '{print $2}')

VAULT_SECRETS_JSON=$(mktemp /tmp/vault-secrets-json.XXXXXX)

oci --config-file $OCI_CONFIG_PATH --profile VAULT vault secret list --all --vault-id $VAULT_ID --compartment-id $COMPARTMENT_ID >  $VAULT_SECRETS_JSON

VAULT_SECRETS_TO_DELETE=$(mktemp /tmp/vault-secrets-delete.XXXXXX)
python3 $SCRIPT_DIR/auxiliary/filter_oci_vault_secrets.py $VAULT_SECRETS_JSON > $VAULT_SECRETS_TO_DELETE

DELETION_TIME=$(date -u --iso-8601=minutes -d "+2 days")

while read -r secret_ocid
do
	read SECRET_OCID <<< "$secret_ocid"
	oci --config-file $OCI_CONFIG_PATH --profile VAULT vault secret schedule-secret-deletion --time-of-deletion $DELETION_TIME --secret-id $SECRET_OCID
done < $VAULT_SECRETS_TO_DELETE

rm $VAULT_SECRETS_JSON $VAULT_SECRETS_TO_DELETE
