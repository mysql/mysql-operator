#!/bin/bash

# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


NAMESPACE=${NAMESPACE:-mysql-operator}
CM_NAME=${CM_NAME:-tls-validate-code}
DEPLOYMENT=${DEPLOYMENT:-mysql-operator}

####


DEPLOYMENT_CONFIG=$(kubectl get deployment -n $NAMESPACE $DEPLOYMENT -o json)

# we can't simply delete by name, but have to find our environment variables
# this extracts all names of environment variables and then uses `nl` and `grep` to find offset and then `awk` to print the number ... 
K8S_HOST_INDEX=$(echo $DEPLOYMENT_CONFIG | jq '.spec.template.spec.containers[0].env[] | .name' -r | nl -v0 | grep KUBERNETES_SERVICE_HOST | awk '{print $1}')
K8S_PORT_INDEX=$(echo $DEPLOYMENT_CONFIG | jq '.spec.template.spec.containers[0].env[] | .name' -r | nl -v0 | grep KUBERNETES_SERVICE_PORT | awk '{print $1}')
CM_VOLUME_INDEX=$(echo $DEPLOYMENT_CONFIG | jq '.spec.template.spec.volumes[] | .name' -r | nl -v0 | grep validatecode | awk '{print $1}')

PATCH='[{"op": "remove", "path": "/spec/template/spec/containers/1"}]'

if [ -n "$K8S_HOST_INDEX" ]; then
  PATCH=$(echo $PATCH | jq ". += [{\"op\": \"remove\", \"path\": \"/spec/template/spec/containers/0/env/$K8S_HOST_INDEX\"}]")
fi

if [ -n "$K8S_PORT_INDEX" ]; then
  # operations happen one after another, thus this may (will) move up. We might simply
  # change the order, but doing the -1 should be more robust
  if [ "$K8S_HOST_INDEX" -lt "$K8S_PORT_INDEX" ]; then
    K8S_PORT_INDEX=$((K8S_PORT_INDEX-1))
  fi
  PATCH=$(echo $PATCH | jq ". += [{\"op\": \"remove\", \"path\": \"/spec/template/spec/containers/0/env/$K8S_PORT_INDEX\"}]")
fi

if [ -n "$CM_VOLUME_INDEX" ]; then
  PATCH=$(echo $PATCH | jq ". += [{\"op\": \"remove\", \"path\": \"/spec/template/spec/volumes/$CM_VOLUME_INDEX\"}]")
fi  

kubectl patch deployment -n ${NAMESPACE} ${DEPLOYMENT} --type='json' -p="$PATCH"

kubectl delete -n $NAMESPACE cm/$CM_NAME
