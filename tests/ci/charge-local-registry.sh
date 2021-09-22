#!/bin/bash
# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# charge local registry
# usage: [--pull] [--no-pull] [--push-only] [--pull-only] <registry-url> <repository-name> <images-list>
# registry-url: e.g. registry.localhost:5000
# repository-name: e.g. mysql, jenkins, ...
# --pull: force to pull all images from list
# --no-pull/--push-only: force to not pull any image from list, only push images to the local registry
# --pull-only: pull images with default policy, but don't push to the local registry (may be
#   needed when e.g. VPN is required to pull images, but it precludes pushing to a local registry)
# e.g.: --no-pull registry.localhost:5000 mysql ./images-list.txt
# input sample, every three lines are in order
# DEST_IMAGE_NAME the name of the image in the local repo (e.g. mysql-server:8.0.24)
# SRC_IMAGE_FULL_NAME source image, potentially to be pulled (e.g. mysql/mysql-server:8.0.24 or
# mydocker.mysql.oraclecorp.com/qa/mysql-router:8.0.25)
# PULL_POLICY for the source image (0 - not to pull, 1 - to pull)
# separator ---
# <images-list.txt>
# mysql-server:8.0.24
# mysql/mysql-server:8.0.24
# 1
# ---
# mysql-router:8.0.24
# mysql/mysql-router:8.0.24
# 1
# ---
# [...]
# </images-list.txt>

set -vx

PULL_POLICY="default"
PUSH_POLICY="default"

while [[ $# -gt 0 ]]; do
	flag="$1"
	case $flag in
		--pull)
			PULL_POLICY="force-pull"
			shift
			;;
		--no-pull|--push-only)
			PULL_POLICY="force-no-pull"
			shift
			;;
		--pull-only)
			PUSH_POLICY="force-no-push"
			shift
			;;
		*)
			break
			;;
	esac
done

if [ "$#" -ne 3 ]; then
	echo "usage: [--pull] [--no-pull] [--push-only] [--pull-only] <registry-url> <repository-name> <images-list>"
	exit 1
fi

REGISTRY_URL=$1
REPOSITORY_NAME=$2
IMAGES_LIST=$3

images_to_process=$(mktemp)
cat "$3" | awk 'BEGIN { RS = "---" } { print $1 " " $2 " " $3 }' > $images_to_process

while read -r image_info
do
	read DEST_IMAGE_NAME SRC_IMAGE_FULL_NAME PULL_IMAGE <<< "$image_info"
	case ${PULL_POLICY} in
		"default")
			PERFORM_PULL=$PULL_IMAGE
			;;
		"force-pull")
			PERFORM_PULL=1
			;;
		"force-no-pull")
			PERFORM_PULL=0
			;;
		*)
			PERFORM_PULL=0
			;;
	esac

	if [ $PERFORM_PULL -eq 1 ]; then
		docker pull $SRC_IMAGE_FULL_NAME
	fi

	if [ $PUSH_POLICY == "default" ]; then
		FULL_IMAGE_NAME=$REGISTRY_URL/$REPOSITORY_NAME/$DEST_IMAGE_NAME

		docker tag $SRC_IMAGE_FULL_NAME $FULL_IMAGE_NAME
		docker push $FULL_IMAGE_NAME
		docker image rm $FULL_IMAGE_NAME
	fi
done < $images_to_process

rm $images_to_process
