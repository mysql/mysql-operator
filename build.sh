#!/bin/bash
# Copyright (c) 2021, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

ARCH='amd64'
if [[ $# -gt 0 && ! "$1" =~ ^- ]]; then
  ARCH="$1"
  shift
fi

IMG_TAG=$(./tag.sh)
MAJOR_VERSION=${IMG_TAG:0:3}
DOCKERFILE="Dockerfile"

while [[ $# -gt 0 ]]; do
  case "$1" in
    -f|--file)
      if [[ -n "${2-}" ]]; then
        DOCKERFILE="$2"
        shift 2
      else
        echo "Error: --file requires a filename argument" >&2
        exit 1
      fi
      ;;
    -t|--tag)
      TAG="$2"
      shift 2
      ;;
    -*)
      echo "Unknown option: $1" >&2
      exit 1
      ;;
    *)
      shift
      ;;
  esac
done

docker build --build-arg http_proxy=${http_proxy} --build-arg https_proxy=${https_proxy} --build-arg no_proxy=${no_proxy} -f "${DOCKERFILE}" -t "${TAG}":${MAJOR_VERSION}-$ARCH .
