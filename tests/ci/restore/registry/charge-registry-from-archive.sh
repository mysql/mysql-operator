#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry from docker saved tar archive (also compressed)
# usage: <archive-path> <registry-url>
# archive-path - path to an archive
# registry-url - url of a registry to be charged
# original-image-name - original name of an image, needed only for archives containing single image (i.e. no 'repository'
#		file inside)
# e.g.
# ./charge-registry-from-archive.sh ~/k8s-archives/k3s-airgap-images-amd64-v1_21_11_2Bk3s1.tar.gz registry.localhost:5000
# ./charge-registry-from-archive.sh \
#		~/k8s-archives/kindest_node_v1_27_1_sha256_9915f5629ef4d29f35b478e819249e89cfaffcbfeebda4324e5c01d53d937b09.tar.gz
#		registry.localhost:5000 \
#		kindest/node:v1.27.1@sha256:9915f5629ef4d29f35b478e819249e89cfaffcbfeebda4324e5c01d53d937b09

set -vx

if [[ "$#" -ne 2 && "$#" -ne 3 ]]; then
	echo "usage: <archive-path> <registry-url> <original-image-name>"
	exit 1
fi

ARCHIVE_PATH=$1
REGISTRY_URL=$2
IMAGE_NAME=$3

ls -l $ARCHIVE_PATH

if [[ ! -f $ARCHIVE_PATH ]]; then
	echo "warning: image archive $ARCHIVE_PATH not found"
	exit 2
fi

# load images to docker
docker load -i $ARCHIVE_PATH

# charge the (local) registry
REPOSITORIES_FILENAME=repositories
if tar -tf $ARCHIVE_PATH | grep -q $REPOSITORIES_FILENAME; then
	TMP_REPOSITORIES_FILENAME=$(mktemp repositories.XXXXX)
	tar xvf $ARCHIVE_PATH -O $REPOSITORIES_FILENAME > $TMP_REPOSITORIES_FILENAME
	cat $TMP_REPOSITORIES_FILENAME

	IMAGES=$(cat $TMP_REPOSITORIES_FILENAME | jq -r 'keys[] as $k | "\($k):\(.[$k] | to_entries[] | .key)"')
	rm $TMP_REPOSITORIES_FILENAME
else
	IMAGES=$IMAGE_NAME
fi

echo $IMAGES

for IMAGE in $IMAGES; do
	if [[ $IMAGE != *@* ]]; then
		IMAGE_IN_REGISTRY=$REGISTRY_URL/$IMAGE
	else
		IMAGE_WITH_SHA="${IMAGE%%@*}"-$(sed -e 's/:/-/g' <<< "${IMAGE##*@}")
		IMAGE_IN_REGISTRY=$REGISTRY_URL/$IMAGE_WITH_SHA
	fi
	docker tag $IMAGE $IMAGE_IN_REGISTRY
	docker push $IMAGE_IN_REGISTRY
	docker image rm $IMAGE_IN_REGISTRY
done
