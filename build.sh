#!/bin/bash
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

ARCH='amd64'
IMG_TAG=$(./tag.sh)
MAJOR_VERSION=${IMG_TAG:0:3}
DOCKERFILE="Dockerfile"

while getopts "a:f:t:h" opt; do
  case "$opt" in
    a)
      # Set architecture from -a option
      ARCH="$OPTARG"
      ;;
    f)
      # Set Dockerfile from -f option
      DOCKERFILE="$OPTARG"
      ;;
    t)
      # Set image tag from -t option
      TAG="$OPTARG"
      ;;
    \?)
      # Unknown option handler
      echo "Invalid option: -$OPTARG"
      exit 1
      ;;
  esac
done

docker build --build-arg http_proxy=${http_proxy} --build-arg https_proxy=${https_proxy} --build-arg no_proxy=${no_proxy} -f "${DOCKERFILE}" -t "${TAG}":${MAJOR_VERSION}-$ARCH .
