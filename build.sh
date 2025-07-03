#!/bin/bash
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# Usage function
print_usage() {
cat <<EOF
Usage: $0 -a <arch> -t <tag>

Options:
  -a  Architecture (amd64,arm64)
  -t  Image tag (e.g., mysql/community-operator, mysql/enterprise-operator)
  -h  Show this help message
EOF
}

#Default values
ARCH='amd64'
IMG_TAG=$(./tag.sh)
MAJOR_VERSION=${IMG_TAG:0:3}
DOCKERFILE="Dockerfile"

while getopts "a:f:t:h" opt; do
  case "$opt" in
    a)
      # Set architecture from -a option
      ARCH="$OPTARG"
      if [[ -z "$ARCH" || ! "$ARCH" =~ ^(amd64|arm64)$ ]]; then
        echo "Error: Invalid architecture '$ARCH'" >&2
        exit 1
      fi
      ;;
    f)
      # Set Dockerfile from -f option
      DOCKERFILE="$OPTARG"
      if [[ -z "$DOCKERFILE" ]]; then
        echo "Error: Docker file arg (-f) is required and cannot be empty." >&2
        exit 1
      fi
      ;;
    t)
      # Set image tag from -t option
      TAG="$OPTARG"
      if [[ -z "$TAG" ]]; then
        echo "Error: Image tag (-t) is required and cannot be empty." >&2
        exit 1
      fi
      ;;
    h)
      print_usage
      exit 0
      ;;
    \?)
      # Unknown option handler
      echo "Invalid option: -$OPTARG"
      exit 1
      ;;
  esac
done

#check if any unknown args passed to the script
shift $((OPTIND - 1))
if [[ $# -gt 0 ]]; then
  echo "Error: Unknown arguments: $*" >&2
  print_usage
  exit 1
fi

docker build --build-arg http_proxy=${http_proxy} --build-arg https_proxy=${https_proxy} --build-arg no_proxy=${no_proxy} -f "${DOCKERFILE}" -t "${TAG}":${MAJOR_VERSION}-$ARCH .
