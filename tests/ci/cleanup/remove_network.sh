#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

NETWORK=$1

docker network inspect \
  -f '{{ range $key, $value := .Containers }} {{ $value.Name }} {{ end }}' $NETWORK \
  | xargs -r -n 1 docker network disconnect $NETWORK

docker network rm $NETWORK
