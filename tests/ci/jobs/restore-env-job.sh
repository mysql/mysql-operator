#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# an auxiliary script to help restore the environment - download needed binaries (kubectl, minikube, k3d, kind, ...),
# ensure the local registry runs, and charge it with required images

set -vx

source $WORKSPACE/tests/ci/jobs/auxiliary/set-env.sh || exit 10

BINARIES_DIR=$WORKSPACE/binaries
ARCHIVES_DIR=$WORKSPACE/archives

mkdir -p $BINARIES_DIR
mkdir -p $ARCHIVES_DIR

$CI_DIR/restore/restore-env.sh $BINARIES_DIR $ARCHIVES_DIR

echo """
    0. before executing this job, run $CI_DIR/restore/registry/pull-and-save-dockerhub-images.sh locally to
        collect unreachable images, then copy them to $ARCHIVES_DIR, and set owner:group:
        sudo chown james:common $ARCHIVES_DIR/*
    1. after executing this job, copy all binaries from $BINARIES_DIR to a reachable path, e.g.
        sudo chmod +x $BINARIES_DIR/*
        sudo cp -ruv $BINARIES_DIR /usr/local/bin
"""
