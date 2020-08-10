# Copyright (c) 2020, Oracle and/or its affiliates.

cd mysql-router
version=8.0.21

image=akkojima/mysql-router:$version

docker build . -t $image

minikube cache add $image

#myregistry=localhost:5000
#docker tag $image $myregistry/mysql-router:$version
#docker push $myregistry/mysql-router:$version

