# Copyright (c) 2020, Oracle and/or its affiliates.

registry=$1
version=$2

if [ -z "$registry" ]; then
    registry=local
fi
if [ -z "$version" ]; then
    version=8.0.21
fi

image=$registry/mysql-server:$version


if [ -n "$clean" -o ! -d bld.mysql-server ]; then
    rm -fr bld.mysql-server
    mkdir bld.mysql-server
    cd bld.mysql-server
    install_pydeps=1
else
    cd bld.mysql-server
fi


for src in ../mysql-server/*; do
    dst=`basename $src`
    sed -e "s/@VERSION@/$version/g" $src > $dst
    chmod +x $dst
done

docker build . -t $image

