#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# script meant to perform Azure set up and start a related container
# usage: <k8s-context> <registry-url> <azure-config-file> <azure-container-name>
# k8s-context - k8s context used to run kubectl commands
# registry-url - url of a registry to pull azure images from, it may be a local registry, or a remote one
# azure-config-file - path to the config file, if it doesn't exist it will be generated
# azure-container-name - the name of an azure storage container

set -vx

if [ "$#" -ne 4 ]; then
	echo "usage: <k8s-context> <registry-url> <azure-config-file> <azure-container-name>"
	exit 1
fi

K8S_CONTEXT=$1
REGISTRY_URL=$2
AZURE_CONFIG_FILE=$3
AZURE_CONTAINER_NAME=$4

AZURITE_IMAGE=${REGISTRY_URL}/mcr.microsoft.com/azure-storage/azurite
for i in $(eval echo "{1..180}"); do
    kubectl --context=${K8S_CONTEXT} run --image=${AZURITE_IMAGE} azurite -- azurite --blobHost 0.0.0.0
    if [[ $? -eq 0 ]]; then
        break
    fi
    sleep 1
done

for i in $(eval echo "{1..180}"); do
    AZURE_POD_IP=$(kubectl --context=${K8S_CONTEXT} get pod azurite -o=jsonpath='{.status.podIP}')
    if [[ -n "$AZURE_POD_IP" ]]; then
        break
    fi
    sleep 1
done

kubectl --context=${K8S_CONTEXT} run \
    --attach \
    --image=${REGISTRY_URL}/mcr.microsoft.com/azure-cli \
    --restart=Never \
    --env AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://${AZURE_POD_IP}:10000/devstoreaccount1;" \
    azure \
    -- \
    az storage container create --name=${AZURE_CONTAINER_NAME}

# create the azure config file only if it doesn't exist
if [[ ! -f $AZURE_CONFIG_FILE ]]; then
    cat > $AZURE_CONFIG_FILE<< EOC
[storage]
account=devstoreaccount1
key=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==
connection_string=BlobEndpoint=http://${AZURE_POD_IP}:10000/devstoreaccount1
EOC
fi

kubectl --context=${K8S_CONTEXT} create secret generic azure-config --from-file=config=${AZURE_CONFIG_FILE}
