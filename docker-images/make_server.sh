# Copyright (c) 2020, Oracle and/or its affiliates.

if [ -n "$1" ]; then
  base_version=$1
else
  base_version=8.0.21
fi
version=$base_version
#push=1

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
    sed -e "s/@VERSION@/$version/g" -e "s/@BASE_VERSION@/$base_version/g" $src > $dst
    chmod +x $dst
done

image=akkojima/mysql-server:$version

minikube ssh "docker image rm -f $image"

docker build . -t $image
cd ..

minikube cache add $image
minikube cache reload

if [ -n "$push" ]; then
  docker push $image
fi

