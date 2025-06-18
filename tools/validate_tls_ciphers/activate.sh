#!/bin/bash

# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -e

NAMESPACE=${NAMESPACE:-mysql-operator}
CM_NAME=${CM_NAME:-tls-validate-code}
IMAGE=${IMAGE:-container-registry.oracle.com/os/oraclelinux:9}
DEPLOYMENT=${DEPLOYMENT:-mysql-operator}

echo $1

extra_args=""
if [ "$1" = "--no-abort" ]; then
  extra_args=', "--no-abort"'
fi

kubectl create cm -n $NAMESPACE  $CM_NAME --from-file tls_validate_proxy.py=tls_validate_proxy.py --from-file tls-parameters-4.csv=tls-parameters-4.csv

# This adds a Container with the tls proxy to the deployment and configures operator
# to route via that container. We have to run in the same Pod as certificates for
# the admin server are valid on 127.0.0.1, but not a random Pod IP
# An alternative might be do to TLS termination in our proxy, but it's complex enough already
kubectl patch -n $NAMESPACE deployment $DEPLOYMENT  --type=json -p='[
  {"op": "add", "path": "/spec/template/spec/containers/-", "value": {
   "name": "validator",
   "args": ["python3", "-u", "tls_validate_proxy.py"'"${extra_args}"'],
   "workingDir": "/work",
   "image": "'${IMAGE}'",
   "imagePullPolicy": "Always",
   "securityContext": {
      "allowPrivilegeEscalation": false,
      "capabilities": {
        "drop": ["ALL"]
      },
      "privileged": false,
      "readOnlyRootFilesystem": true,
      "runAsNonRoot": true,
      "runAsUser": 2
   },
   "volumeMounts": [{
    "mountPath": "/work",
    "name": "validatecode",
    "readOnly": true
   }]
  }},
  {"op": "add", "path": "/spec/template/spec/volumes/-", "value": {
  "configMap": { "name": "'${CM_NAME}'"}, "name": "validatecode"
  }},
  {"op": "add", "path": "/spec/template/spec/containers/0/env/-", "value": {"name": "KUBERNETES_SERVICE_HOST", "value": "127.0.0.1"}},
  {"op": "add", "path": "/spec/template/spec/containers/0/env/-", "value": {"name": "KUBERNETES_SERVICE_PORT", "value": "9443"}}
]'
