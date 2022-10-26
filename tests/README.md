# The test suite of MySQL Operator for Kubernetes
=============================

## Requirements:
* python3
* modules:
    * kubernetes
    * kubernetes-client
    * pyyaml
    * mysql-connector-python (**CAUTION!** NOT mysql-connector)
    * unittest-xml-reporting (optional, to generate JUnit xml reports)

They can be installed with pip and [requirements.txt](requirements.txt), e.g.

```sh
pip3 install -r ./requirements.txt
```

The test-suite is configured in three stages:
* defaults
* environment variables
* command-line options

The defaults may be overridden by envars, in turn, they can be overridden by cmd-line options.

## Defaults
All defaults are located in [defaults.py](setup/defaults.py).

## Environment variables
The following envars are supported:
* OPERATOR_TEST_REGISTRY
* OPERATOR_TEST_REPOSITORY
* OPERATOR_TEST_IMAGE_NAME
* OPERATOR_TEST_EE_IMAGE_NAME
* OPERATOR_TEST_VERSION_TAG
* OPERATOR_TEST_PULL_POLICY
* OPERATOR_TEST_SKIP_ENTERPRISE
* OPERATOR_TEST_SKIP_OCI
* OPERATOR_TEST_OCI_CONFIG_PATH
* OPERATOR_TEST_OCI_BUCKET
* OPERATOR_TEST_VAULT_CONFIG_PATH
* OPERATOR_TEST_K8S_CLUSTER_NAME

## Command-line options
--env=[k3d|minikube]\
    set the k8s environment

--kube-version\
set the kubernetes version to use, if not set, the default depends on the installed minikube or k3d
* for minikube it is used to pass an argument to the --kubernetes-version={value} option, e.g.:
    - minikube start --kubernetes-version=v1.22.5
    - minikube start --kubernetes-version=v1.23.4
* for k3d it is used to pass an argument to the --image={value} option, e.g.:
    - k3d cluster create --image=rancher/k3s:v1.21.7-k3s1
    - k3d cluster create --image=rancher/k3s:v1.23.6-k3s1

--nodes\
    points out the nodes to use, supported for minikube only; if not set, any available will be used

--verbose|-v\
    verbose logs

-vv\
    more verbose

-vvv\
    even more verbose

--debug|-d\
    to work with py debugger

--trace|-t\
    enable tracer

--nosetup|--no-setup\
    disable the setup of the environment and creation of a new cluster / an existing cluster will be used\
    **CAUTION!** if not set the default cluster will be deleted (depending on chosen k8s environment - k3d or minikube)

--noclean|--no-clean\
    do not delete the cluster after the tests are completed, by default it is deleted\
    the flag --nosetup also enables that option

--cluster={name}\
    the name of the cluster to use;
    if not set, a default name will be used (it can be found in [defaults.py](setup/defaults.py)
    at variable K8S_CLUSTER_NAME)\
    by default, the test suite runner creates a new cluster on its own

--use-current-context\
    tests will run in the current context (returned with ```kubectl config current-context```);
    no setup will be performed (similarly as for --no-setup flag)

--load\
    obsolete! used to load images\
    at the moment the use of a local registry is strongly recommended\
    that option will be removed in the future version

--nodeploy|--no-deploy\
    do not deploy operator\
    by default there is used operator that is generated in the method:
    ```BaseEnvironment.deploy_operator```, file [base.py](./utils/ote/base.py)

--dkube\
    more verbose logging of kubectl operations

--doperator[=level]\
    set the operator debug level (by default the logging is disabled, i.e. level = 0)\
    it influences how much information will be written to the operator pod logs\
    level is an integer number, and it is optional\
    if it is not passed (just '--doperator'), then default debug level value will be set

--doci\
    enable diagnostics for oci-cli operations

--mount-operator|-O={path}\
    mount operator sources in the mysql-operator pod, very useful to test patches without building an image\
    the sources are drawn from\
        ```.${src_root}/mysqloperator```\
    while tests run from\
        ```.${src_root}/tests```\
    according to our standard git repo structure

--registry={url}\
    set the images registry, e.g. `registry.localhost:5000`

--registry-cfg={path}\
    supported only for k3d, the path to a registry config\
    if k3d is used and a registry is set, but this path is not set, then the cfg file will
    be generated according to the following template:

```cfg
mirrors:
  $registry:
    endpoint:
      - http://$registry
```

e.g.
```cfg
mirrors:
  registry.localhost:5000:
    endpoint:
      - http://registry.localhost:5000
```

--repository={name}\
    the image repository, the default value is "mysql"

--operator-tag={tag}\
    set the operator tag, e.g. 8.0.29-2.0.4 or latest, by default it is the currently developed version

--operator-pull-policy=[Never|IfNotPresent|Always]\
    set the pull policy, the default can be found in [defaults.py](setup/defaults.py)

--skip-enterprise\
    force to skip all tests related to the enterprise edition, by default it is false

--skip-oci\
    force to skip all OCI tests even if OCI is properly configured, by default it is false

--oci-config={path}\
path to an OCI config file; to run OCI tests, it should contain three profiles:
* BACKUP - it has permissions to store a backup into the bucket
* RESTORE - it has permissions to restore a backup from the bucket
* DELETE - it has permissions to delete items from the bucket, after a given OCI-related test is completed (at tear down)
* VAULT - it has permissions to create, use, and delete secrets in an OCI vault (see also --vault-cfg)

under the hood, it may be the same profile or three different profiles with more fine-grained permissions\
by default the path is empty, then all OCI-related tests are skipped\
the data below are fake and used as an illustration of what format is expected, all data and paths should be
customized according to a local environment:

```ini
    [BACKUP]
    user=ocid1.user.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbbba
    fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp
    tenancy=ocid1.tenancy.oc1..aaaaaaaaabbbbbbbbbbbbbbbbbbbbbbccccccccccccccccdddddddddddde
    region=us-ashburn-1
    passphrase=
    key_file=/home/user/oci-stuff/backup/key.pem

    [RESTORE]
    user=ocid1.user.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbbba
    fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp
    tenancy=ocid1.tenancy.oc1..aaaaaaaaabbbbbbbbbbbbbbbbbbbbbbccccccccccccccccdddddddddddde
    region=us-ashburn-1
    passphrase=
    key_file=/home/user/oci-stuff/restore/key.pem

    [DELETE]
    user=ocid1.user.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbbba
    fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp
    tenancy=ocid1.tenancy.oc1..aaaaaaaaabbbbbbbbbbbbbbbbbbbbbbccccccccccccccccdddddddddddde
    region=us-ashburn-1
    key_file=/home/user/oci-stuff/delete/key.pem

    [VAULT]
    user=ocid1.user.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbbba
    fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp
    tenancy=ocid1.tenancy.oc1..aaaaaaaaabbbbbbbbbbbbbbbbbbbbbbccccccccccccccccdddddddddddde
    region=us-ashburn-1
    key_file=/home/user/oci-stuff/vault/key.pem
```

--oci-bucket={name}\
    name of an OCI bucket used to perform backup/restore tests\
    by default it is empty, then all OCI-related tests are skipped

--vault-cfg={path}\
    Used for OCI vault-related tests. See also profile VAULT in --oci-config. It contains a single section [OCI]
    with the following data in any order (the data below are fake and used as an illustration of what format is expected):

```ini
    [OCI]
    user=ocid1.user.oc1..aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaabbbbba
    tenancy=ocid1.tenancy.oc1..aaaaaaaaabbbbbbbbbbbbbbbbbbbbbbccccccccccccccccdddddddddddde
    compartment=ocid1.compartment.oc1..aaaaaaaabbbbbbbbbbbbbbbbbccccccccc11111111112222222233333333
    virtual_vault=ocid1.vault.oc1.iad.baaaaaaaaaaae.abccccccccccccccccccccccccccccccddddddddddddddeeeeeeee123456
    master_key=ocid1.key.oc1.iad.b5rtlrieaaece.aaaaaaaaaaaaabbbbbbbbbbbbbbccccccccccccc11111111111122222223
    encryption_endpoint=bbbbbbbbbbbbb-crypto.kms.us-ashburn-1.oraclecloud.com
    management_endpoint=bbbbbbbbbbbbb-management.kms.us-ashburn-1.oraclecloud.com
    vaults_endpoint=vaults.us-ashburn-1.oci.oraclecloud.com
    secrets_endpoint=secrets.vaults.us-ashburn-1.oci.oraclecloud.com
    key_file=/home/user/.oci/vault/key.pem
    key_fingerprint=aa:bb:cc:dd:ee:ff:gg:hh:ii:jj:kk:ll:mm:nn:oo:pp
```

--xml={path}\
    generate results in JUnit xml reports

--suite={path}\
    point out the file with the list of tests to run, e.g.

```text
<suite.txt>
e2e.mysqloperator.cluster.cluster_t.TwoClustersOneNamespace
e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksModification
</suite.txt>
```

filters can be used, see the description of 'arguments'

```text
arguments:
    gtest style test filter like
        include1:include2:-exclude1:exclude2

e.g.
e2e.mysqloperator.cluster.*
e2e.mysqloperator.cluster.cluster_badspec_t.*
e2e.mysqloperator.cluster.*:-e2e.mysqloperator.cluster.cluster_badspec_t.*
```

It may also be a full path to a test case or a list of test cases:
```text
e2e.mysqloperator.backup.dump_t.DumpInstance
e2e.mysqloperator.cluster.cluster_enterprise_t.ClusterEnterprise e2e.mysqloperator.cluster.cluster_t.TwoClustersOneNamespace
```

To run all tests use:
```text
e2e.*
e2e.mysqloperator.*
```
or just pass nothing


Everything runs by the script [./tests/run](run). It requires python3. Go to the [./tests](.) directory and execute e.g.\
**CAUTION!** To avoid deleting an existing (default) cluster use option --nosetup.

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
cluster. The operator will pull images from `registry.localhost:5000/qa`.

```sh
./run --env=k3d --registry=local.registry:5005 --registry-cfg=~/mycfg/k3d-registries.yaml --noclean \
    e2e.mysqloperator.cluster.cluster_enterprise_t.ClusterEnterprise
```

It will set up a brand new k3d cluster and run tests against the enterprise edition. The operator will
pull images from `local.registry:5005/mysql`.



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

It will set up a brand new minikube cluster (the previous one will be deleted) and run all tests matching `e2e.*` pattern.
The operator will pull images from `myregistry.local:5000/mysql`. The logs will be verbose (level 1).

```
./run --env=k3d --registry=registry.localhost:5000 --nosetup --nodeploy -vv
```

It will use an existing k3d cluster (nosetup) with an already deployed operator (nodeploy). The operator will pull
images from `registry.localhost:5000/mysql`. The logs will be verbose (level 2). As no filter was passed, all tests
will be executed.

=============================

## Run tests simultaneously

The test suite may also run in parallel on many k3d or minikube instances with [dist_run_e2e_tests.py](dist_run_e2e_tests.py) script.

It supports the following command-line options:
* --env\
    similarly as for standard run on a single instance

* --tag={name}\
    it should be a unique tag as the created instances will contain it, e.g. `build-246`, `ote-mysql`, etc.

* --workers={number}\
    the number of instances to create and run the tests on\
    default is 2

* --defer={number}\
    interval (in seconds) between starting instances\
    we noticed that if many instances run at the same moment, then sometimes one or a few may fail\
    default is 60

* --work-dir|workdir={path}\
    it points out to a directory where to store all data and logs\
    if not provided, then a tmp dir will be used

* --sort\
    by default tests are shuffled among workers equally but randomly, hence execution times may differ\
    this option enables identical order for every run\
    default is false

* --expected-failures={path}\
    points to a file where are listed tests that are expected to fail\
    a sample file (timings in brackets are ignored)
```text
<expected-failures.txt>
FAIL [2.515s]: test_2_bad_pod_creation (e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksCreation)
ERROR [93.934s]: test_3_modify_ssl_certs_and_ca (e2e.mysqloperator.cluster.cluster_ssl_t.ClusterSSL)
ERROR [71.461s]: test_4_add_crl (e2e.mysqloperator.cluster.cluster_ssl_t.ClusterSSL)
FAIL [25.968s]: test_1_grow_2 (e2e.mysqloperator.cluster.cluster_t.Cluster1Defaults)
</expected-failures.txt>
```

* --xml\
    enable generation of results in JUnit xml reports\
    they will be stored in the workdir\
    **CAUTION!** converse to a single-worker run, a path shouldn't be passed
