#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# auxiliary script to charge a registry from docker saved tar archive (also compressed)
# usage: <--wget|-w|--file|-f> <link|filepath> <registry-url>
# --wget|-w link - download archive from a given link
# --file|-f filepath - use an existing archive under given path
# registry-url - url of a registry to be charged
# e.g.
# --wget https://github.com/k3s-io/k3s/releases/download/v1.23.15%2Bk3s1/k3s-airgap-images-amd64.tar.gz registry.localhost:5000
# -w https://github.com/k3s-io/k3s/releases/download/v1.22.7%2Bk3s1/k3s-airgap-images-arm64.tar registry.localhost:5000
# --file ~/Downloads/k3s-airgap-images-amd64.tar registry.localhost:5000
# -f ./k3s-airgap-images-arm64.tar.gz registry.localhost:5000

set -vx

if [ "$#" -ne 3 ]; then
	echo "usage: <--wget|-w|--file|-f> <link|filepath> <registry-url>"
	exit 1
fi

BUILD_TAG=build-$BUILD_NUMBER
BUILD_WORKDIR=$WORKSPACE/$BUILD_TAG
mkdir -p $BUILD_WORKDIR
cd $BUILD_WORKDIR

OPERATION=$1
if [[ $OPERATION == "--wget" || $OPERATION == "-w" ]]; then
    ARCHIVE_LINK=$2
	ARCHIVE_FILENAME=$(basename $ARCHIVE_LINK)-$BUILD_TAG
	ARCHIVE_TMP_PATH=$(mktemp $BUILD_WORKDIR/$ARCHIVE_FILENAME.XXXXX)
    wget $ARCHIVE_LINK -O $ARCHIVE_TMP_PATH
	ARCHIVE_PATH=$ARCHIVE_TMP_PATH
elif [[ $OPERATION == "--file" || $OPERATION == "-f" ]]; then
    ARCHIVE_PATH=$2
else
	echo "unknown option: $2"
	exit 2
fi
REGISTRY_URL=$3

ls -l $ARCHIVE_PATH

# load images to docker
docker load -i $ARCHIVE_PATH

# charge the (local) registry
REPOSITORIES_FILENAME=repositories
tar xvf $ARCHIVE_PATH $REPOSITORIES_FILENAME
cat $REPOSITORIES_FILENAME

IMAGES=$(cat $REPOSITORIES_FILENAME | jq -r 'keys[] as $k | "\($k):\(.[$k] | to_entries[] | .key)"')

echo $IMAGES

if [[ -n $ARCHIVE_TMP_PATH ]]; then
    rm $ARCHIVE_TMP_PATH
fi
rm $REPOSITORIES_FILENAME

for IMAGE in $IMAGES; do
	IMAGE_IN_REGISTRY=$REGISTRY_URL/$IMAGE
	docker tag $IMAGE $IMAGE_IN_REGISTRY
	docker push $IMAGE_IN_REGISTRY
	docker image rm $IMAGE_IN_REGISTRY
done
