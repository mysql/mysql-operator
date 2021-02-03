# Copyright (c) 2020, 2021, Oracle and/or its affiliates.

registry=$1
version=$2

if [ -z "$registry" ]; then
    registry=local
fi
if [ -z "$version" ]; then
    version=8.0.23
fi

image=$registry/mysql-enterprise-server:$version


if [ -n "$clean" -o ! -d bld.mysql-server-ee ]; then
    rm -fr bld.mysql-server-ee
    mkdir bld.mysql-server-ee
fi
cd bld.mysql-server-ee


for src in ../mysql-server-ee/*; do
    dst=`basename $src`
    sed -e "s/@VERSION@/$version/g" $src > $dst
    chmod +x $dst
done

ARGS=""
if test "$http_proxy" != ""; then
    ARGS="--build-arg HTTP_PROXY=$HTTP_PROXY --build-arg HTTPS_PROXY=$HTTP_PROXY"
fi

docker build . $ARGS -t $image

