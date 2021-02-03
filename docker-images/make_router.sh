# Copyright (c) 2020, 2021, Oracle and/or its affiliates.

registry=$1
version=$2

if [ -z "$registry" ]; then
    registry=local
fi
if [ -z "$version" ]; then
    version=8.0.23
fi

image=$registry/mysql-router:$version


if [ -n "$clean" -o ! -d bld.mysql-router ]; then
    rm -fr bld.mysql-router
    mkdir bld.mysql-router
fi
cd bld.mysql-router

for src in ../mysql-router/*; do
    dst=`basename $src`
    sed -e "s/@VERSION@/$version/g" $src > $dst
done

chmod +x run.sh

ARGS=""
if test "$http_proxy" != ""; then
    ARGS="--build-arg HTTP_PROXY=$HTTP_PROXY --build-arg HTTPS_PROXY=$HTTP_PROXY"
fi

docker build . $ARGS -t $image
