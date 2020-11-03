# Copyright (c) 2020, Oracle and/or its affiliates.

cd mysql-router

registry=$1
version=$2

if [ -z "$registry" ]; then
    registry=local
fi
if [ -z "$version" ]; then
    version=8.0.21
fi

image=$registry/mysql-router:$version

docker build . -t $image

