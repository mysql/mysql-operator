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

## Pre-requisites
* Kubernetes 1.21+
* Helm v3

## MySQL InnoDB Cluster Installation with Helm

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

## More Information

Refer to the official documentation at:

  * https://dev.mysql.com/doc/mysql-operator/en/

For additional downloads and the source code, visit:

  * https://dev.mysql.com/downloads
  * https://github.com/mysql/mysql-operator

Contributing to MySQL Operator for Kubernetes, see:

  * See [CONTRIBUTING](CONTRIBUTING.md)
