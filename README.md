MySQL Operator for Kubernetes
=============================

The MYSQL Operator for Kubernetes is an Operator for Kubernetes managing
MySQL InnoDB Cluster setups inside a Kubernetes Cluster.

The MySQL Operator manages the full lifecycle with setup and maintenance
including automation of upgrades and backup.

Release Status
--------------
The MySQL Operator for Kubernetes currently is in a preview state.
DO NOT USE IN PRODUCTION.

License
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

Installation of the MySQL Operator
----------------------------------

The MYSQL Operator can be installed using `kubectl`:

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

Using the MySQL Operator to setup a MySQL InnoDB Cluster
-------------------------------------------------------

For creating an InnoDB Cluster you first have to create a secret containing
credentials for a MySQL root user which is to be created:

```
kubectl create secret generic  mypwds \
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

Connecting to the MYSQL InnoDB Cluster
-------------------------------------

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


