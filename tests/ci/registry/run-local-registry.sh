#!/bin/bash
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

# ensures the local registry is running
# usage: <container-name> <host-port> <container-port>
# container-name: the name of the container, e.g. registry.localhost
# host-port: registry port on the host side, e.g. 5000
# container-port: registry port on the container side, e.g. 5000
#
# e.g.:
# run-local-registry.sh registry.localhost 5000 5000

if [ "$#" -ne 3 ]; then
	echo "usage: <container-name> <host-port> <container-port>"
	exit 1
fi

LOCAL_REGISTRY_CONTAINER_NAME=$1
LOCAL_REGISTRY_HOST_PORT=$2
LOCAL_REGISTRY_CONTAINER_PORT=$3

# if the local registry is not running
if [ ! "$(docker ps -q -f name=^${LOCAL_REGISTRY_CONTAINER_NAME}\$)" ]; then
	# if the local registry exited
	if [ "$(docker ps -aq -f status=exited -f name=^${LOCAL_REGISTRY_CONTAINER_NAME}\$)" ]; then
		# cleanup
		docker rm ${LOCAL_REGISTRY_CONTAINER_NAME}
	fi
	# run registry
	docker pull registry:2
	docker run -d -p $LOCAL_REGISTRY_HOST_PORT:$LOCAL_REGISTRY_CONTAINER_PORT --restart=always \
		--name $LOCAL_REGISTRY_CONTAINER_NAME registry:2
fi
docker ps | grep ${LOCAL_REGISTRY_CONTAINER_NAME}
