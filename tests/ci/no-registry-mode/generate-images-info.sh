#!/bin/bash
# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# prepare info regarding images used by tests
# usage: <images-list> <output-dir>
# e.g.: ./images-list.txt ~/docker-images
# input sample (every three lines are in order LABEL, IMAGE_TO_PULL, IMAGE_TO_TEST, separator ---)
# <images-list.txt>
# mysql-server:8.0.24
# mysql/community-server:8.0.24
# mysql/community-server:8.0.24
# ---
# mysql-router:8.0.24
# mysql/community-router:8.0.24
# mysql/community-router:8.0.24
# ---
# [...]
# </images-list.txt>

if [ "$#" -ne 2 ]; then
    echo "usage: <images-list> <output-dir>"
	exit 1
fi

if [ ! -d "$2" ]; then
    echo "output-dir '$2' doesn't exist"
	exit 2
fi

docker_images=$(mktemp)
docker images > $docker_images

images_to_process=$(mktemp)
cat "$1" | awk 'BEGIN { RS = "---" } { print $1 " " $3 }' > $images_to_process

OUTPUT_DIR="$2"

while read -r image_info
do
	read LABEL IMAGE_TO_TEST <<< "$image_info"
	IFS=: read IMAGE_REPO IMAGE_TAG <<< "$IMAGE_TO_TEST"
	read IMAGE_ID <<< $(grep -E "$IMAGE_REPO.*$IMAGE_TAG" $docker_images | awk '{print $3}')
	OUTPUT_FILE="$OUTPUT_DIR/$LABEL.txt"
	echo -e "$IMAGE_ID\n$IMAGE_TO_TEST" > "$OUTPUT_FILE"
done < $images_to_process

rm $docker_images
rm $images_to_process
