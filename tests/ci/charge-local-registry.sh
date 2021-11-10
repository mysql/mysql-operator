#!/bin/bash
# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# charge local registry
# usage: [--pull] [--no-pull] [--push-only] [--pull-only] \
#	[pull-registry-url] <pull-repository-name> <push-registry-url> <push-repository-name> <images-list>
# pull-registry-url: the remote registry to pull the images from, e.g. our internal development mysql
#	repos, it may be an empty string, then the default docker hub (docker.io) will be used
# pull-repository-name: the remote repository to pull the images from, e.g. qa, ...
# push-registry-url: the local registry to push the images to, e.g. registry.localhost:5000
# push-repository-name: the local repository to push the images to, e.g. mysql, jenkins, ...
# --pull: force to pull all images from list
# --no-pull/--push-only: force to not pull any image from list, only push images to the local registry
# --pull-only: pull images with default policy, but don't push to the local registry (may be
#   needed when e.g. VPN is required to pull images, but it precludes pushing to a local registry)
# images-list: input file with list of image names to pull from the remote/push to the local registry
# e.g.: --no-pull registry.localhost:5000 mysql ./images-list.txt
#
# input sample, every three lines are in order
# DEST_IMAGE_NAME the name of the image in the local repo (e.g. mysql-server:8.0.24)
# SRC_IMAGE_NAME source image, potentially to be pulled (e.g. mysql-server:8.0.24)
# PULL_POLICY for the source image (0 - not to pull, 1 - to pull)
# separator ---
# <images-list.txt>
# mysql-server:8.0.24
# mysql-server:8.0.24
# 1
# ---
# mysql-operator-commercial:8.0.25-2.0.1
# mysql-operator:8.0.25-2.0.1
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

if [ "$#" -ne 5 ]; then
	echo "usage: [--pull] [--no-pull] [--push-only] [--pull-only] [pull-registry-url] " \
		"<pull-repository-name> <push-registry-url> <push-repository-name> <images-list>"
	exit 1
fi

PULL_REGISTRY_URL=$1
PULL_REPOSITORY_NAME=$2
PUSH_REGISTRY_URL=$3
PUSH_REPOSITORY_NAME=$4
IMAGES_LIST=$5

images_to_process=$(mktemp)
cat "$IMAGES_LIST" | awk 'BEGIN { RS = "---" } { print $1 " " $2 " " $3 }' > $images_to_process

while read -r image_info
do
	read DEST_IMAGE_NAME SRC_IMAGE_NAME PULL_IMAGE <<< "$image_info"
	case "${PULL_POLICY}" in
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

	if [ -n "$PULL_REGISTRY_URL" ]; then
		SRC_IMAGE=$PULL_REGISTRY_URL/$PULL_REPOSITORY_NAME/$SRC_IMAGE_NAME
	else
		SRC_IMAGE=$PULL_REPOSITORY_NAME/$SRC_IMAGE_NAME
	fi
	if [ $PERFORM_PULL -eq 1 ]; then
		docker pull $SRC_IMAGE
	fi

	if [ "$PUSH_POLICY" == "default" ]; then
		DEST_IMAGE=$PUSH_REGISTRY_URL/$PUSH_REPOSITORY_NAME/$DEST_IMAGE_NAME

		docker tag $SRC_IMAGE $DEST_IMAGE
		docker push $DEST_IMAGE
		docker image rm $DEST_IMAGE
	fi
done < $images_to_process

rm $images_to_process
