# MySQL Operator for Kubernetes

## Introduction

The MySQL Operator for Kubernetes is an operator for managing MySQL InnoDB Cluster setups inside a Kubernetes Cluster. 
It manages the full lifecycle with set up and maintenance that includes automating upgrades and backup.

MySQL Operator for Kubernetes is brought to you by the MySQL team at Oracle.

## Issues and Pull Requests

As with all MySQL projects, issues (including bugs and feature requests) are tracked here:

  * https://bugs.mysql.com/

Pull requests submitted via github are also tracked at bugs.mysql.com; see [CONTRIBUTING](CONTRIBUTING.md) for related information.

## License

Copyright (c) 2020, 2024, Oracle and/or its affiliates.

License information can be found in the [LICENSE](https://github.com/mysql/mysql-operator/blob/trunk/LICENSE) file.
This distribution may include materials developed by third parties. For license
and attribution notices for these materials, please refer to the `LICENSE` file.

## MySQL Operator for Kubernetes Installation

### Using Manifest Files with kubectl

First deploy the Custom Resource Definition (CRDs):

```sh
$> kubectl apply -f https://raw.githubusercontent.com/mysql/mysql-operator/8.4.0-2.1.3/deploy/deploy-crds.yaml
```

Then deploy MySQL Operator for Kubernetes:

```sh
$> kubectl apply -f https://raw.githubusercontent.com/mysql/mysql-operator/8.4.0-2.1.3/deploy/deploy-operator.yaml
```

Verify the operator is running by checking the deployment inside the `mysql-operator` namespace:

```sh
$> kubectl get deployment -n mysql-operator mysql-operator

NAME             READY   UP-TO-DATE   AVAILABLE   AGE
mysql-operator   1/1     1            1           1h
```

### Using Helm

Alternatively, you may use [Helm](https://helm.sh/docs/intro/quickstart/); which is a package manager for Kubernetes.

Install the Helm repository:

```sh
$> helm repo add mysql-operator https://mysql.github.io/mysql-operator/
$> helm repo update
```

Then deploy the operator:

```sh
$> helm install mysql-operator mysql-operator/mysql-operator --namespace mysql-operator --create-namespace
```

This deploys the latest MySQL Operator for Kubernetes from DockerHub using all defaults; although the deployment 
can be customized through a variety of options to override built-in defaults. See the documentation for details.

## MySQL InnoDB Cluster Installation

### Using kubectl

For creating a MySQL InnoDB Cluster, first create a secret with credentials for a MySQL root user used to 
perform administrative tasks in the cluster. For example:

```sh
$> kubectl create secret generic mypwds \
        --from-literal=rootUser=root \
        --from-literal=rootHost=% \
        --from-literal=rootPassword="sakila"
```

Define your MySQL InnoDB Cluster, which references the secret. For example:

```yaml
apiVersion: mysql.oracle.com/v2
kind: InnoDBCluster
metadata:
  name: mycluster
spec:
  secretName: mypwds
  tlsUseSelfSigned: true
  instances: 3
  router:
    instances: 1
```

Assuming it's saved as `mycluster.yaml`, deploy it:

```sh
$> kubectl apply -f mycluster.yaml
```

This sample creates an InnoDB Cluster with three MySQL Server instances and one MySQL Router instance. 
The process can be observed using:

```sh
$> kubectl get innodbcluster --watch

NAME          STATUS    ONLINE   INSTANCES   ROUTERS   AGE
mycluster     PENDING   0        3           1         2m6s
...
mycluster     ONLINE    3        3           1         10s
```

### Using Helm

Create MySQL InnoDB Cluster installations using defaults or with customization. 
Here's an example using all defaults for a cluster named `mycluster`:

```sh
$> helm install mycluster mysql-operator/mysql-innodbcluster
```

Or customize, this example sets options from the command line:

```sh
$> helm install mycluster mysql-operator/mysql-innodbcluster \
        --namespace mynamespace \
        --create-namespace \
        --set credentials.root.user='root' \
        --set credentials.root.password='supersecret' \
        --set credentials.root.host='%' \
        --set serverInstances=3 \
        --set routerInstances=1
```

## Connecting to MySQL InnoDB Cluster

A MySQL InnoDB Cluster `Service` is created inside the Kubernetes cluster:

```sh
$> kubectl get service mycluster

NAME        TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)                                                           AGE
mycluster   ClusterIP   10.110.228.51   <none>        3306/TCP,33060/TCP,6446/TCP,6448/TCP,6447/TCP,6449/TCP,6450/TCP   26h
```

The ports represent read-write and read-only ports for the MySQL Protocol and the X Protocol. 
Use `describe` or see the documentation for additional information.

### Using MySQL Shell

This example creates a new container named `myshell` using the `container-registry.oracle.com/mysql/community-operator` image, and immediately executes MySQL Shell:
        
```sh
$> kubectl run --rm -it myshell --image=container-registry.oracle.com/mysql/community-operator -- mysqlsh
If you don't see a command prompt, try pressing enter.

MySQL JS>  \connect root@mycluster

Creating a session to 'root@mycluster'
Please provide the password for 'root@mycluster': ******

MySQL mycluster JS>
```

Using `root@mycluster` connection assumes the default namespace is used; the long form is `{innodbclustername}.{namespace}.svc.cluster.local`. 
Each MySQL instance has MySQL Shell installed that can be used when troubleshooting.

### Using Port Forwarding

Kubernetes port forwarding creates a redirection from your local machine to use a MySQL client, such as `mysql` or MySQL Workbench. 
For example, for read-write connection to the primary using the MySQL protocol:

```sh
$> kubectl port-forward service/mycluster mysql

Forwarding from 127.0.0.1:3306 -> 6446
Forwarding from [::1]:3306 -> 6446
```

And in a second terminal:

```sh
$> mysql -h127.0.0.1 -P3306 -uroot -p

Enter password:
Welcome to the MySQL monitor.  Commands end with ; or \g.
...
```

When prompted, enter the password used when creating the Secret.

## More Information

Refer to the official documentation at:

  * https://dev.mysql.com/doc/mysql-operator/en/

For additional downloads and the source code, visit:

  * https://dev.mysql.com/downloads
  * https://github.com/mysql/mysql-operator

Contributing to MySQL Operator for Kubernetes, see:

  * See [CONTRIBUTING](CONTRIBUTING.md)
