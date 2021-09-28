# MySQL Operator for Kubernetes

## Introduction
The MYSQL Operator for Kubernetes is an Operator for Kubernetes managing
MySQL InnoDB Cluster setups inside a Kubernetes Cluster.

The MySQL Operator manages the full lifecycle with setup and maintenance
including automation of upgrades and backup.

## Release Status
--------------
The MySQL Operator for Kubernetes currently is in a preview state.
DO NOT USE IN PRODUCTION.

## License
------
Copyright (c) 2020, 2021, Oracle and/or its affiliates.

This is a release of MySQL Operator, a Kubernetes Operator for MySQL InnoDB Cluster

License information can be found in the LICENSE file.
This distribution may include materials developed by third parties. For license
and attribution notices for these materials, please refer to the LICENSE file.

For more information on MySQL Operator visit https://dev.mysql.com/doc/mysql-shell/8.0/en/
For additional downloads and the source of MySQL Operator visit http://dev.mysql.com/downloads
and https://github.com/mysql

MySQL Operator is brought to you by the MySQL team at Oracle.

---

## Installation of the MySQL Operator

### Installing the MYSQL Operator using `kubectl`:

```sh
kubectl apply -f https://raw.githubusercontent.com/mysql/mysql-operator/trunk/deploy/deploy-crds.yaml
```

```sh
kubectl apply -f https://raw.githubusercontent.com/mysql/mysql-operator/trunk/deploy/deploy-operator.yaml
```

Note: The propagation of the CRDs can take a few seconds depending on the size
of your Kubernetes cluster. Best is to wait a second or two between those
commands. If the second command fails due to missing CRD apply it a second
time.

To verify the operator is running check the deployment managing the 
operator, inside the `mysql-operator` namespace.

```sh
kubectl get deployment -n mysql-operator mysql-operator
```

Once the Operator is ready the putput should be like

``` 
NAME             READY   UP-TO-DATE   AVAILABLE   AGE
mysql-operator   1/1     1            1           1h
```

### Installing the MYSQL Operator using `Helm
Helm is a package manager for Kubernetes. It makes the installation of Kubernetes Operators
and resources handled by them easy. Please refer to the [Helm Quickstart Guide](https://helm.sh/docs/intro/quickstart/)
as well as the [Installing Helm Guide](https://helm.sh/docs/intro/install/) for more information on Helm and how to install it.

You need to download the sources MySQL Operator for Kubernetes to install the operator with Helm. The sources contain a top level directory named helm. Under this directory there are two subdirectories. The `mysql-operator` directory contains the Helm Chart of the MySQL Operator for Kubernetes.
Change directory to the source checkout and then execute
```sh
export NAMESPACE="mysql-operator"
helm install mysql-operator helm/mysql-operator --namespace $NAMESPACE --create-namespace
```

The structure is `helm install [name-of-the-installation] [path/to/the/helm/chart] --namespace [namespace-where-to-put-the-operator] [whether-to-create-the-namespace]` . If the namespace whether the operator will be installed already
exists please omit the `--create-namespace` option. Without adding more options to the command above, the latest MySQL Operator for Kubernetes will be downloaded from DockerHub and deployed. The deployment of the operator can be customized through a variety of options which will override built-in defaults. For example, if you have an air-gapped Kubernetes installation and use own private container registry, there is a way to use it with the operator. 

---
### ADVANCED: Copying the MySQL Operator for Kubernetes container image into a private registry by using Docker

*If you don't use a private registry, please skip these instructions.*

1. Look into `helm/mysql-operator/Chart.yaml` and copy the appVersion string. Alterntively you can run
   `grep appVersion helm/mysql-operator/Chart.yaml | cut -d '"' -f2` to extract the version. This is the version of the operator. 
2. Execute `docker pull mysql/mysql-operator:VERSION` where version is string from step 1.
3. Execute `docker save  mysql/mysql-operator:VERSION -o mysql-operator.tar` to export the container image
4. Copy `mysql-operator.tar` to a host which has access to the private registry.
5. Load the image into the local Docker cache on that host by issuing `docker load -i mysql-operator.yaml`
6. Retag the image as preparation for pushing to the private registry by issuing `docker tag mysql/mysql-server:8.0.26 registry:port/repo/mysql-server:8.0.26
7. Push the newly created tag to the private registy by executing `docker push registry:port/repo/mysql-server:VERSION`
8. If you won't need the image from the importing host cache, then you can delete it with `docker rmi mysql/mysql-operator:VERSION registry:port/repo/mysql-server:VERSION`. This will remove it from the host but the registry itself won't be affected.

You can use the following commands to pull and push in one command. The command is to be run on a host that has access to DockerHub. This host also needs to have an access to bastion host that has access to the private registry. P(please adjust the variable values to fit your needs. The command will not consume local space for a tarball but will stream the container image over SSH. 
```sh
export BASTION_USER='k8s'
export BASTION_HOST='k8'
export REGISTRY="..." # for example 192.168.20.199:5000
export REPOSITORY="..." # for example mysql
export OPERATOR_VERSION=$(grep appVersion helm/mysql-operator/Chart.yaml | cut -d '"' -f2)
docker pull mysql/mysql-operator:$OPERATOR_VERSION
docker save mysql/mysql-operator:$OPERATOR_VERSION | \
    ssh $BASTION_USER@$BASTION_HOST \
        "docker load && \
         docker tag mysql/mysql-operator:$OPERATOR_VERSION $REGISTRY/$REPOSITORY/mysql-operator:$OPERATOR_VERSION && \
         docker push $REGISTRY/$REPOSITORY/mysql-operator:$OPERATOR_VERSION && \
         docker rmi mysql/mysql-operator:$OPERATOR_VERSION $REGISTRY/$REPOSITORY/mysql-operator:$OPERATOR_VERSION"
docker rmi mysql/mysql-operator:$OPERATOR_VERSION
```
---
### ADVANCED: Copying the MySQL Operator for Kubernetes container image into a private registry by using Skopeo

*If you don't use a private registry, please skip these instructions.*

If [Skopeo](https://github.com/containers/skopeo) is available for your platform, you can use it for copying images. It is
also possible run Skopeo in a container. Use the following to copy the operator image from DockerHub to your private registry.
It needs to be run on a host (that has Docker or Podman ) that has both access to DockerHub and your private registry. In case of Podman just exchange `docker` with `podman` (please adjust the variable values to fit your needs)

```sh
export REGISTRY="..." # for example 192.168.20.199:5000
export REPOSITORY="..." # for example mysql
export OPERATOR_VERSION=$(grep appVersion helm/mysql-operator/Chart.yaml | cut -d '"' -f2)
docker run --rm quay.io/skopeo/stable copy docker://mysql/mysql-operator:$OPERATOR_VERSION docker://$REGISTRY/$REPOSITORY/mysql-operator:$OPERATOR_VERSION
```

If your private registry is authenticated, then you need to append `--dest-creds user:pass` to the skopeo command. In case, your private registry doesn't use TLS, then you need to also append `--dest-tls-verify=false`.

---
### ADVANCED: Installing the Operator with Helm when using a private registry

*If you don't use a private registry, please skip these instructions.*

If your private registry is not authenticated then once you have pushed the operator image to your private registry execute the following on the host where helm installed (please adjust the variable values to fit your needs)
```sh
export REGISTRY="..."   # like 192.168.20.199:5000
export REPOSITORY="..." # like "mysql"
export NAMESPACE="mysql-operator"
helm install mysql-operator helm/mysql-operator \
    --namespace $NAMESPACE \
    --create-namespace \
    --set image.registry=$REGISTRY \
    --set image.repository=$REPOSITORY \
    --set envs.imagesDefaultRegistry="$REGISTRY" \
    --set envs.imagesDefaultRepository="$REPOSITORY"
```
---
If your private registry is authenticated you need to run a few additional commands
1. You need to create the namespace in which the operator will be installed, e.g. by issuing `kubectl create namespace mysql-operator`
2. The you need to create a Kubernetes `docker-registry` secret in the namespace, e.g. by issuing `kubectl -n mysql-operator create secret docker-registry priv-reg-secret --docker-server=https://192.168.20.199:5000/v2/ --docker-username=user --docker-password=pass --docker-email=user@example.com`
3. Once the docker-registry secret is created you have to execute `helm install` with a few more arguments.

As a script it should look like this (please adjust the variable values to fit your needs)
```sh
export REGISTRY="..."   # like 192.168.20.199:5000
export REPOSITORY="..." # like "mysql"
export NAMESPACE="mysql-operator"
export DOCKER_SECRET_NAME="priv-reg-secret"
export DOCKER_USER="user"
export DOCKER_USER_PASS="pass"

kubectl create namespace $NAMESPACE

kubectl -n $NAMESPACE create secret docker-registry $DOCKER_SECRET_NAME \
        --docker-server="https://$REGISTRY/v2/" \
        --docker-username=user --docker-password=pass \
        --docker-email=user@example.com

helm install mysql-operator helm/mysql-operator \
        --namespace $NAMESPACE \
        --set image.registry=$REGISTRY \
        --set image.repository=$REPOSITORY \
        --set image.pullSecrets.enabled=true \
        --set image.pullSecrets.secretName=$DOCKER_SECRET_NAME \
        --set image.pullSecrets.username="$DOCKER_USER" \
        --set image.pullSecrets.password="$DOCKER_USER_PASS" \
        --set image.pullSecrets.email='user@example.com' \
        --set envs.imagesPullPolicy='IfNotPresent' \
        --set envs.imagesDefaultRegistry="$REGISTRY" \
        --set envs.imagesDefaultRepository="$REPOSITORY"
```

Check the result status with `helm list -n $NAMESPACE` and `kubectl -n $NAMESPACE get pods`

---
### Using the MySQL Operator to setup a MySQL InnoDB Cluster

Helm can create MySQL InnoDB Cluster installations with just one command. The installation can be tuned in multiple ways. Here is an example how to create an installation with MySQL InnoDB Cluster with three MySQL Server 8.0.26 instances and three MySQL Router 8.0.26 instances

```sh
export NAMESPACE="mynamespace"

helm install mycluster helm/mysql-innodbcluster \
        --namespace $NAMESPACE \
        --create-namespace \
        --set credentials.root.user='root' \
        --set credentials.root.password='supersecret' \
        --set credentials.root.host='%' \
        --set serverInstances=3 \
        --set routerInstances=3
```
---
### Using the MySQL Operator to setup a MySQL InnoDB Cluster from a private registry

*If you don't use a private registry, please skip these instructions.*

```sh
export REGISTRY="..."   # like 192.168.20.199:5000
export REPOSITORY="..." # like "mysql"
export NAMESPACE="mynamespace"
export DOCKER_SECRET_NAME="priv-reg-secret"
export DOCKER_USER="user"
export DOCKER_USER_PASS="pass"

kubectl create namespace $NAMESPACE

kubectl -n $NAMESPACE create secret docker-registry $DOCKER_SECRET_NAME \
        --docker-server="https://$REGISTRY/v2/" \
        --docker-username=user --docker-password=pass \
        --docker-email=user@example.com

helm install mycluster helm/mysql-innodbcluster \
        --namespace $NAMESPACE \
        --set credentials.root.user='root' \
        --set credentials.root.password='supersecret' \
        --set credentials.root.host='%' \
        --set serverInstances=3 \
        --set routerInstances=3 \
        --set image.registry=$REGISTRY \
        --set image.repository=$REPOSITORY \
        --set image.pullSecrets.enabled=true \
        --set image.pullSecrets.secretName=$DOCKER_SECRET_NAME \
        --set image.pullSecrets.username="$DOCKER_USER" \
        --set image.pullSecrets.password="$DOCKER_USER_PASS" \
        --set image.pullSecrets.email='user@example.com'
```
Check the result status with :
```sh
helm list -n $NAMESPACE
```
The output should look like this
```
NAME            NAMESPACE       REVISION        UPDATED                                 STATUS          CHART                                 APP VERSION
mycluster       mynamespace     1               2021-09-27 16:09:01.784987942 +0000 UTC deployed        mysql-innodbcluster-8.0.26-2.0.2      8.0.26
```

### Using Helm for bootstrapping a MySQL InnoDB Cluster from a dump

The MySQL InnoDB Cluster can be initialized with a database dump which was created my MySQL Shell or with a backup created by the MySQL Operator for Kubernetes. The backup could reside in OCI Object Storage bucket or on a Persistent Volume accessible to the cluster.
When the cluster is to be bootstrapped from OCI OS the following data must be known:
1. The credentials of the user who has access to OCI OS
2. The OCI OS Bucket Name
3. The OCI OS Object Prefix (plays the role of a directory)
The following Helm variables must be set:
1. `initDB.dump.name` - a name for the dump, which should follow the Kubernetes rules for naming an identifier, e.g. dump-20210916-140352
2. `initDB.dump.ociObjectStorage.prefix` - the prefix from list above
3. `initDB.dump.ociObjectStorage.bucketName` - the bucket name from the list above
4. `initDB.dump.ociObjectStorage.credentials` - the name of the kubernetes secret that holds the credentials for accessing the OCI OS bucket.

The credentials secret the following information is needed:
1. OCI OS User Name
2. Fingerprint
3. Tenancy Name
4. Region Name
5. Passphrase
6. The Private Key of the user

If you have already used the OCI CLI tool you will find this information in $HOME/config under the [DEFAULT] section. Once you have obtained that information, please execute the following
```sh
export NAMESPACE="mynamespace"
export OCI_CREDENTIALS_SECRET_NAME="oci-credentials"
export OCI_USER="..."                # like ocid1.user.oc1....
export OCI_FINGERPRINT="..."         # like 90:01:..:..:....
export OCI_TENANCY="..."             # like ocid1.tenancy.oc1...
export OCI_REGION="..."              # like us-ashburn-1
export OCI_PASSPHRASE="..."          # set to empty string if no passphrase
export OCI_PATH_TO_PRIVATE_KEY="..." # like $HOME/.oci/oci_api_key.pem

kubectl -n $NAMESPACE create secret generic $OCI_CREDENTIALS_SECRET_NAME \
        --from-literal=user="$OCI_USER" \
        --from-literal=fingerprint="$OCI_FINGERPRINT" \
        --from-literal=tenancy="$OCI_TENANCY" \
        --from-literal=region="$OCI_REGION" \
        --from-literal=passphrase="$OCI_PASSPHRASE" \
        --from-file=privatekey="$OCI_PATH_TO_PRIVATE_KEY"
```

After you have created the OCI secret you can create the cluster which will be initialized from the dump in OCI OS.

```sh
export NAMESPACE="mynamespace"
export OCI_DUMP_PREFIX="..."  # like dump-20210916-140352
export OCI_BUCKET_NAME="..."  # like idbcluster_backup
export OCI_CREDENTIALS_SECRET_NAME="oci-credentials"
kubectl create namespace $NAMESPACE
helm install mycluster helm/mysql-innodbcluster \
        --namespace $NAMESPACE \
        --set image.registry=$REGISTRY \
        --set image.repository=$REPOSITORY \
        --set credentials.root.user='root' \
        --set credentials.root.password='supersecret' \
        --set credentials.root.host='%' \
        --set serverInstances=3 \
        --set routerInstances=3 \
        --set initDB.dump.name="initdb-dump" \
        --set initDB.dump.ociObjectStorage.prefix="$OCI_DUMP_PREFIX" \
        --set initDB.dump.ociObjectStorage.bucketName="$OCI_BUCKET_NAME" \
        --set initDB.dump.ociObjectStorage.credentials="$OCI_CREDENTIALS_SECRET_NAME"
```

---
### Using `kubectl` to create a MySQL InnoDB Cluster

For creating an InnoDB Cluster you first have to create a secret containing
credentials for a MySQL root user which is to be created:

```sh
kubectl create secret generic mypwds \
        --from-literal=rootUser=root \
        --from-literal=rootHost=% \
        --from-literal=rootPassword="your secret password, REPLACE ME"
```

With that the sample cluster can be created:

```sh
kubectl apply -f https://raw.githubusercontent.com/mysql/mysql-operator/trunk/samples/sample-cluster.yaml
```

This sample will create an InnoDB Cluster with three MySQL server instances
and one MySQL Router instance. The process can be observed using

```sh
kubectl get innodbcluster --watch
```

```
NAME          STATUS    ONLINE   INSTANCES   ROUTERS   AGE
mycluster     PENDING   0        3           1         10s
```

---
## Connecting to the MYSQL InnoDB Cluster

For connecting to the InnoDB Cluster a `Service` is created inside the 
Kubernetes cluster.

```sh
kubectl get service mycluster
```

```
NAME          TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)                               AGE
mycluster     ClusterIP   10.43.203.248   <none>        6446/TCP,6448/TCP,6447/TCP,6449/TCP   1h
```

The exported ports represent Read-write and read-only ports for the
MySQL Protocol and the X Protocol. Using `describe` more information can be seen

```sh
kubectl describe service mycluster
```

    Name:              mycluster
    Namespace:         default
    Labels:            mysql.oracle.com/cluster=mycluster
                       tier=mysql
    Annotations:       <none>
    Selector:          component=mysqlrouter,mysql.oracle.com/cluster=mycluster,tier=mysql
    Type:              ClusterIP
    IP Families:       <none>
    IP:                10.43.203.248
    IPs:               <none>
    Port:              mysql  6446/TCP
    TargetPort:        6446/TCP
    Endpoints:         <none>
    Port:              mysqlx  6448/TCP
    TargetPort:        6448/TCP
    Endpoints:         <none>
    Port:              mysql-ro  6447/TCP
    TargetPort:        6447/TCP
    Endpoints:         <none>
    Port:              mysqlx-ro  6449/TCP
    TargetPort:        6449/TCP
    Endpoints:         <none>
    Session Affinity:  None
    Events:            <none>

Using Kubernetes port forwarding you can create a redirection from your local
machine, so that you can use any MySQL Client, like MySQL Shell or MySQL
Workbench to inspect or using the server.

For a read-write connection to the primary using MYSQL protocol:

```sh
kubectl port-forward service/mycluster mysql
```

And then in a second terminal:

```sh
mysqlsh -h127.0.0.1 -P6446 -uroot -p
```

When promted enter the password used, when creating the Secret above.


