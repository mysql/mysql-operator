#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

docker container prune -f

CONTAINERS=$(docker ps -q -f 'name=k3d|minikube' | xargs -r -n 1 docker container inspect -f '{{.ID}} {{json .Created}}' \
  | awk -v cut_off_date=\""$(date -d 'yesterday' -Ins)"\" '$2 <= cut_off_date {print $1}')

if [ -n "$CONTAINERS" ]; then
  docker container stop -t 60 $CONTAINERS
  docker container rm -v $CONTAINERS
fi
