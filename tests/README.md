The test suite of MySQL Operator for Kubernetes
=============================

Requirements:
0) python3
1) module: kubernetes
2) module: mysql-connector-python (CAUTION! not mysql-connector)
3) module: unittest-xml-reporting (optional, to generate JUnit xml reports)


The test-suite is configured in three stages:
0) defaults
1) environment variables
2) command-line options

The defaults may be overridden by envars, in turn, they can be overridden by cmd-line options.

Ad 0) defaults
All defaults are located in
./tests/setup/defaults.py

Ad 1) envars
The following envars are supported:
OPERATOR_TEST_REGISTRY
OPERATOR_TEST_REPOSITORY

OPERATOR_TEST_IMAGE_NAME
OPERATOR_TEST_EE_IMAGE_NAME
OPERATOR_TEST_VERSION_TAG
OPERATOR_TEST_PULL_POLICY
OPERATOR_TEST_GR_IP_WHITELIST

OPERATOR_TEST_SKIP_OCI
OPERATOR_TEST_OCI_CONFIG_PATH
OPERATOR_TEST_OCI_BUCKET

OPERATOR_TEST_K8S_CLUSTER_NAME

Ad 2) command-line options
--env=[k3d|minikube]
    set the k8s environment

--kube-version
    set the kubernetes version to use, supported for minikube only
    if not set, the default depends on the installed minikube

--nodes
    points out the nodes to use, supported for minikube only
    if not set, any available will be used

--verbose|-v
    verbose logs

-vv
    more verbose

-vvv
    even more verbose

--debug|-d
    to work with py debugger

--trace|-t
    enable tracer

--cluster=<name>
    the name of the cluster/context to use, by default it creates and uses its own
    the default name is stored in ./src/tests/setup/defaults.py at variable K8S_CLUSTER_NAME

--nosetup|--no-setup
    disable setup of the environment and creation of cluster / an existing cluster will be used
    CAUTION! if not set the default cluster will be deleted (depending on chosen k8s environment - k3d or minikube)

--noclean|--no-clean
    Do not delete the cluster after the tests completed. By default it is deleted.

--load
    obsolete! used to load images
    at the moment the use of a local registry is strongly recommended
    that option probably will be removed in the future version

--nodeploy|--no-deploy
    do not deploy operator
    by default there is used operator that is generated in the method:
    BaseEnvironment.deploy_operator, file ./tests/utils/ote/base.py

--dkube
    more verbose logging of kubectl operations

--doperator
    set the operator debug level to 3

--doci
    enable diagnostics for oci-cli operations

--mount-operator|-O=<path>
    mount operator sources in the mysql-operator pod, very useful to test patches without building an image
    the sources are drawn from
        .${src_root}/mysqloperator
    where tests run from
        .${src_root}/tests
    according to our standard git repo structure

--registry=<url>
    set the images registry, e.g. registry.localhost:5000

--registry-cfg=<path>
    supported only for k3d, the path to a registry config
    if k3d is used and a registry is set, but this path is not set, then the cfg file will
    be generated according to the following template:
mirrors:
  $registry:
    endpoint:
      - http://$registry

e.g.
mirrors:
  registry.localhost:5000:
    endpoint:
      - http://registry.localhost:5000

--repository=<name>
    the image repository, the default value is "mysql"

--operator-tag=<tag>
    set the operator tag, e.g. 8.0.29-2.0.4 or latest, by default it is the currently developed version

--operator-pull-policy=[Never|IfNotPresent|Always]
    set the pull policy, the default can be found in defaults.py

--skip-oci
    force to skip all OCI tests even if OCI is properly configured, by default it is false

--oci-config=<path>
    path to an OCI config file
    to run OCI tests, it should contain three profiles:
    a) BACKUP - it has permissions to store a backup into the bucket
    b) RESTORE - it has permissions to restore a backup from the bucket
    c) DELETE - it has permissions to delete items from the bucket, after a given OCI-related test
        is completed (at tear down)
    under the hood, it may be the same profile or three different profiles with more fine-grained permissions
    by default the path is empty, then all OCI-related tests are skipped

--oci-bucket=<name>
    name of an OCI bucket used to perform backup/restore tests
    by default it is empty, then all OCI-related tests are skipped

--xml=<path>
    generate results in JUnit xml reports

--suite=<path>
    point out the file with the list of tests to run, e.g.
<suite.txt>
e2e.mysqloperator.cluster.cluster_t.TwoClustersOneNamespace
e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksModification
</suite.txt>
    filters can be used, see the description of 'arguments'

arguments:
    gtest style test filter like
        include1:include2:-exclude1:exclude2

e.g.
e2e.mysqloperator.cluster.*
e2e.mysqloperator.cluster.cluster_badspec_t.*
e2e.mysqloperator.cluster.*:-e2e.mysqloperator.cluster.cluster_badspec_t.*

It may also be a full path to a test case or a list of test cases:
e2e.mysqloperator.backup.dump_t.DumpInstance
e2e.mysqloperator.cluster.cluster_enterprise_t.ClusterEnterprise e2e.mysqloperator.cluster.cluster_t.TwoClustersOneNamespace

To run all tests use:
e2e.*
e2e.mysqloperator.*
or just pass nothing


Everything runs by the script ./tests/run. It requires python3. Go to the ./tests directory and execute e.g.
CAUTION! To avoid deleting an existing (default) cluster use option --nosetup.


```sh
./run --env=minikube e2e.mysqloperator.backup.dump_t.DumpInstance
```

It will delete the existing default minikube cluster and set up a brand new one, then run the backup-dump-instance test case.


```sh
./run --env=k3d -vvv -t --dkube --doperator e2e.mysqloperator.cluster.cluster_t.Cluster1Defaults
```

It will delete the existing default k3d cluster. Then It will set up a brand new default k3d cluster and
run tests against the 1-instance default cluster. The logs will be very verbose.



```sh
./run --env=minikube --registry=registry.localhost:5000 --repository=qa e2e.mysqloperator.cluster.cluster_t.Cluster3Defaults
```

It will set up a brand new minikube cluster (the previous one will be deleted) and run tests against the 3-instances default
cluster. The operator will pull images from registry.localhost:5000/qa.


```sh
./run --env=k3d --registry=local.registry:5005 --registry-cfg=~/mycfg/k3d-registries.yaml --noclean \
    e2e.mysqloperator.cluster.cluster_enterprise_t.ClusterEnterprise
```

It will set up a brand new k3d cluster and run tests against the enterprise edition. The operator will
pull images from local.registry:5005/mysql.



The exact group of testcases can be run in the following way (here tabs are used to separate the names):

```sh
read -r -d '\t' TEST_SUITE << EOM
	e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecAdmissionChecks
	e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksCreation
	e2e.mysqloperator.cluster.cluster_t.Cluster1Defaults
	e2e.mysqloperator.cluster.cluster_t.ClusterCustomConf
	e2e.mysqloperator.cluster.cluster_upgrade_t.UpgradeToNext
	e2e.mysqloperator.cluster.initdb_t.ClusterFromDumpOCI
EOM

./run --env=k3d --mount-operator --noclean $TEST_SUITE
```

It will set up a brand new k3d cluster and run the chosen list of tests against the mounted (patched) operator. After tests
are completed the cluster will not be deleted.


```sh
./run --env=minikube --registry=myregistry.local:5000 -v e2e.*
```

It will set up a brand new minikube cluster (the previous one will be deleted) and run all tests matching e2e.* pattern.
The operator will pull images from myregistry.local:5000/mysql. The logs will be verbose (level 1).


```
./run --env=k3d --registry=registry.localhost:5000 --nosetup --nodeploy -vv
```

It will use an existing k3d cluster (nosetup) with an already deployed operator (nodeploy). The operator will pull
images from registry.localhost:5000/mysql. The logs will be verbose (level 2). As no filter was passed, all tests
will be executed.

=============================

The test suite may also run in parallel on many k3d or minikube instances with src/tests/dist_run_e2e_tests.py script.

It supports the following command-line options:
--env
    similarly as for standard run on a single instance

--tag=<name>
    it should be a unique tag as the created instances will contain it, e.g. build-246, ote-mysql, etc.

--workers=<number>
    the number of instances to create and run the tests on
    default is 2

--defer=<number>
    interval (in seconds) between starting instances
    we noticed that if many instances run at the same moment, then sometimes one or a few may fail
    default is 60

--work-dir|workdir=<path>
    it points out to a directory where to store all data and logs
    if not provided, then a tmp dir will be used

--sort
    by default tests are shuffled among workers equally but randomly, hence execution times may differ
    this option enables identical order for every run
    default is false

--expected-failures=<path>
    points to a file where are listed tests that are expected to fail
    a sample file (timings in brackets are ignored)
<expected-failures.txt>
FAIL [2.515s]: test_2_bad_pod_creation (e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksCreation)
ERROR [93.934s]: test_3_modify_ssl_certs_and_ca (e2e.mysqloperator.cluster.cluster_ssl_t.ClusterSSL)
ERROR [71.461s]: test_4_add_crl (e2e.mysqloperator.cluster.cluster_ssl_t.ClusterSSL)
FAIL [25.968s]: test_1_grow_2 (e2e.mysqloperator.cluster.cluster_t.Cluster1Defaults)
</expected-failures.txt>

--xml
    enable generation of results in JUnit xml reports
    they will be stored in the workdir
    CAUTION! converse to a single-worker run, a path shouldn't be passed
