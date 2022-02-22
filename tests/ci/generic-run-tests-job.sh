#!/bin/bash
# generic script intended for running tests for both k3d / minikube
set -vx

df -lh | grep /sd

export http_proxy=$HTTP_PROXY
export https_proxy=$HTTPS_PROXY
export no_proxy=$NO_PROXY

pwd
TESTS_DIR=$WORKSPACE/tests
CI_DIR=$TESTS_DIR/ci

LOCAL_REGISTRY_CONTAINER_NAME=registry.localhost
LOCAL_REGISTRY_HOST_PORT=5000
LOCAL_REGISTRY_CONTAINER_PORT=5000

IFS=':' read OPERATOR_IMAGE_PREFIX OPERATOR_IMAGE_TAG <<< ${OPERATOR_IMAGE}

export OPERATOR_TEST_REGISTRY=$LOCAL_REGISTRY_CONTAINER_NAME:$LOCAL_REGISTRY_HOST_PORT
export OPERATOR_TEST_VERSION_TAG=$OPERATOR_IMAGE_TAG

# OCI config
export OPERATOR_TEST_BACKUP_OCI_APIKEY_PATH=${WORKSPACE}/cred/backup
export OPERATOR_TEST_RESTORE_OCI_APIKEY_PATH=${WORKSPACE}/cred/restore
export OPERATOR_TEST_BACKUP_OCI_BUCKET=dumps

export OPERATOR_TEST_OCI_CONFIG_PATH=${WORKSPACE}/cred/config
export OPERATOR_TEST_OCI_BUCKET=dumps

PUSH_REGISTRY_URL=$OPERATOR_TEST_REGISTRY
PUSH_REPOSITORY_NAME=mysql
IMAGES_LIST=$CI_DIR/images-list.txt
IMAGES_LIST_EE=$CI_DIR/images-list-ee.txt

# ensure the local registry is running
$CI_DIR/run-local-registry.sh $LOCAL_REGISTRY_CONTAINER_NAME $LOCAL_REGISTRY_HOST_PORT $LOCAL_REGISTRY_CONTAINER_PORT

# charge the local registry
$CI_DIR/charge-local-registry.sh $PULL_REGISTRY_URL $PULL_REPOSITORY_NAME \
	$PUSH_REGISTRY_URL $PUSH_REPOSITORY_NAME $IMAGES_LIST

# temporarily push-only, till a proper setup of credentials for the EE registry
$CI_DIR/charge-local-registry.sh --push-only $PULL_REGISTRY_URL_EE $PULL_REPOSITORY_NAME_EE \
	$PUSH_REGISTRY_URL $PUSH_REPOSITORY_NAME $IMAGES_LIST_EE

# push the newest operator image to the local registry
LOCAL_REGISTRY_OPERATOR_IMAGE=$PUSH_REGISTRY_URL/$PUSH_REPOSITORY_NAME/mysql-operator:$OPERATOR_TEST_VERSION_TAG
docker pull ${OPERATOR_IMAGE}
docker tag ${OPERATOR_IMAGE} ${LOCAL_REGISTRY_OPERATOR_IMAGE}
docker push ${LOCAL_REGISTRY_OPERATOR_IMAGE}

docker images

# set our temporary kubeconfig, because the default one may contain unrelated data that could fail the build
TMP_KUBE_CONFIG="$WORKSPACE/tmpkubeconfig.$K8S_DRIVER"
cat "$TMP_KUBE_CONFIG"
: > "$TMP_KUBE_CONFIG"
export KUBECONFIG=$TMP_KUBE_CONFIG

trap 'kill $(jobs -p)' EXIT
cd "$TESTS_DIR"

read -r -d '\t' TEST_SUITE << EOM
	e2e.mysqloperator.backup.dump_t.DumpInstance
	e2e.mysqloperator.backup.ordinary_no_timestamp_t.OrdinaryBackupNoTimestamp
	e2e.mysqloperator.backup.ordinary_timestamp_t.OrdinaryBackupTimestamp
	e2e.mysqloperator.backup.scheduled_disabled_inline_t.ScheduledBackupDisabledInline
	e2e.mysqloperator.backup.scheduled_disabled_ref_t.ScheduledBackupDisabledRef
	e2e.mysqloperator.backup.scheduled_inline_oci_t.ScheduledBackupInlineOci
	e2e.mysqloperator.backup.scheduled_inline_t.ScheduledBackupInline
	e2e.mysqloperator.backup.scheduled_ref_oci_t.ScheduledBackupRefOci
	e2e.mysqloperator.backup.scheduled_ref_t.ScheduledBackupRef
	e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecAdmissionChecks
	e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksCreation
	e2e.mysqloperator.cluster.cluster_badspec_t.ClusterSpecRuntimeChecksModification
	e2e.mysqloperator.cluster.cluster_enterprise_t.ClusterEnterprise
	e2e.mysqloperator.cluster.cluster_t.Cluster1Defaults
	e2e.mysqloperator.cluster.cluster_t.Cluster3Defaults
	e2e.mysqloperator.cluster.cluster_t.ClusterCustomConf
	e2e.mysqloperator.cluster.cluster_t.ClusterCustomImageConf
	e2e.mysqloperator.cluster.cluster_t.ClusterRaces
	e2e.mysqloperator.cluster.cluster_t.TwoClustersOneNamespace
	e2e.mysqloperator.cluster.cluster_resources_t.ClusterResources
	e2e.mysqloperator.cluster.cluster_ssl_t.ClusterAddSSL
	e2e.mysqloperator.cluster.cluster_ssl_t.ClusterNoSSL
	e2e.mysqloperator.cluster.cluster_ssl_t.ClusterRouterSSL
	e2e.mysqloperator.cluster.cluster_ssl_t.ClusterSSL
	e2e.mysqloperator.cluster.cluster_upgrade_t.UpgradeToNext
	e2e.mysqloperator.cluster.cluster_volume_t.ClusterVolume
	e2e.mysqloperator.cluster.initdb_t.ClusterFromClone
	e2e.mysqloperator.cluster.initdb_t.ClusterFromDumpOCI
EOM

# run all tests
TEST_SUITE=

TEST_OPTIONS='-t -vvv --doperator --dkube'
TESTS_LOG=$WORKSPACE/tests-$JOB_BASE_NAME-$BUILD_NUMBER.log
touch $TESTS_LOG

# a patch to avoid timeout ("FATAL: command execution failed") for long-lasting operations
"$CI_DIR/show-progress.sh" 240 30 &

if test -z ${WORKERS+x}; then
	WORKERS=1
fi
TAG=build-$BUILD_NUMBER

tail -f "$TESTS_LOG" &
python3 --version
if test $WORKERS == 1; then
	./run --env=$K8S_DRIVER $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
else
	python3 ./dist_run_e2e_tests.py --workers=$WORKERS --tag=$TAG --env=$K8S_DRIVER $TEST_OPTIONS ${TEST_SUITE} > "$TESTS_LOG" 2>&1
fi

# process the tests results
python3 $CI_DIR/inspect-result.py $CI_DIR/expected-failures.txt "$TESTS_LOG"
TESTS_RESULT=$?

bzip2 "$TESTS_LOG"
df -lh | grep /sd

exit $TESTS_RESULT
