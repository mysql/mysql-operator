#!/bin/bash
# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


# pull test images
# usage: <images-list>
# e.g.: ./images-list.txt
# input sample (every three lines are in order LABEL, IMAGE_TO_PULL, IMAGE_TO_TEST, separator ---)
# <images-list.txt>
# community-server:8.0.24
# mysql/community-server:8.0.24
# mysql/community-server:8.0.24
# ---
# community-router:8.0.24
# mysql/community-router:8.0.24
# mysql/community-router:8.0.24
# ---
# [...]
# </images-list.txt>

if [ "$#" -ne 1 ]; then
    echo "usage: <images-list>"
	exit 1
fi

images_to_process=$(mktemp)
cat "$1" | awk 'BEGIN { RS = "---" } { print $2 " " $3 }' > $images_to_process

while read -r image_info
do
	read IMAGE_TO_PULL IMAGE_TO_TEST <<< "$image_info"
	docker pull $IMAGE_TO_PULL
	docker tag $IMAGE_TO_PULL $IMAGE_TO_TEST
done < $images_to_process

rm $images_to_process
