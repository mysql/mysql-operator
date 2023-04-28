#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry from docker saved tar archive (also compressed)
# usage: <archive-path> <registry-url>
# archive-path - path to an archive
# registry-url - url of a registry to be charged
# e.g.
# ./charge-registry-from-archive.sh ./k3s-airgap-images-arm64.tar.gz registry.localhost:5000

set -vx

if [ "$#" -ne 2 ]; then
	echo "usage: <archive-path> <registry-url>"
	exit 1
fi

ARCHIVE_PATH=$1
REGISTRY_URL=$2

ls -l $ARCHIVE_PATH

if [[ ! -f $ARCHIVE_PATH ]]; then
	echo "warning: image archive $ARCHIVE_PATH not found"
	exit 2
fi

# load images to docker
docker load -i $ARCHIVE_PATH

# charge the (local) registry
REPOSITORIES_FILENAME=repositories
TMP_REPOSITORIES_FILENAME=$(mktemp repositories.XXXXX)
tar xvf $ARCHIVE_PATH -O $REPOSITORIES_FILENAME > $TMP_REPOSITORIES_FILENAME
cat $TMP_REPOSITORIES_FILENAME

IMAGES=$(cat $TMP_REPOSITORIES_FILENAME | jq -r 'keys[] as $k | "\($k):\(.[$k] | to_entries[] | .key)"')

echo $IMAGES

rm $TMP_REPOSITORIES_FILENAME

for IMAGE in $IMAGES; do
	IMAGE_IN_REGISTRY=$REGISTRY_URL/$IMAGE
	docker tag $IMAGE $IMAGE_IN_REGISTRY
	docker push $IMAGE_IN_REGISTRY
	docker image rm $IMAGE_IN_REGISTRY
done
