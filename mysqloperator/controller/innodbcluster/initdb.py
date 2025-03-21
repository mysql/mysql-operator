# Copyright (c) 2020, 2024, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import TYPE_CHECKING, cast
from .cluster_api import DumpInitDBSpec, MySQLPod, InitDB, CloneInitDBSpec, InnoDBCluster, ClusterSetInitDBSpec
from ..shellutils import SessionWrap, DbaWrap, connect_dba
from .. import mysqlutils, utils
from ..kubeutils import api_core, api_apps, api_customobj
from ..kubeutils import client as api_client, ApiException
from .. import fqdn
from abc import ABC, abstractmethod
import mysqlsh
import time
import os
from logging import Logger
if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession


def start_clone_seed_pod(session: 'ClassicSession',
                         cluster: InnoDBCluster,
                         seed_pod: MySQLPod, clone_spec: CloneInitDBSpec,
                         logger: Logger) -> bool:
    logger.info(
        f"Initializing seed instance. method=clone  pod={seed_pod}  source={clone_spec.uri}")

    donor_root_co = dict(mysqlsh.globals.shell.parse_uri(clone_spec.uri))
    # Here we get only the password from the cluster secret. The secret
    # might contain also rootUser and rootHost (mask from where the user connects)
    # shouldn't we respect rootUser and not ask for rootUser in clone_spec?
    # Or...this is different kind of secret?
    donor_root_co["user"] = clone_spec.root_user or "root"
    donor_root_co["password"] = clone_spec.get_password(cluster.namespace)

    logger.info(f"CONNECTING {donor_root_co['user']}@{donor_root_co['host']}...")

    # Let's check if the donor has the CLONE plugin and if not install it
    # It's not possible to clone without this plugin being installed
    with SessionWrap(donor_root_co) as donor:
        clone_installed = False
        for row in iter(donor.run_sql("SHOW PLUGINS").fetch_one, None):
            if row[3]:
                logger.info(f"Donor has plugin {row[0]} / {row[3]}")
                if row[0] == "clone":
                    clone_installed = True

        if not clone_installed:
            logger.info(f"Installing clone plugin at {donor.uri}")
            # A: Check here if the plugin reall got installed before continuing?
            donor.run_sql("install plugin clone soname 'mysql_clone.so'")

        # TODO copy other installed plugins(?)

    # clone
    try:
        donor_co = dict(mysqlsh.globals.shell.parse_uri(clone_spec.uri))
        # Here we get only the password from the cluster secret. The secret
        # might contain also rootUser and rootHost (mask from where the user connects)
        # shouldn't we respect rootUser although the clone_spec.uri might already contain it?
        # spec : root@xyz.abc.dev
        donor_co["password"] = clone_spec.get_password(cluster.namespace)

        with SessionWrap(donor_co) as donor:
            logger.info(f"Starting server clone from {clone_spec.uri}")
            ret = mysqlutils.clone_server(donor_co, donor, session, logger)
            logger.info("Cloning finished")
            return ret
    except mysqlsh.Error as e:
        if mysqlutils.is_client_error(e.code) or e.code == mysqlsh.mysql.ErrorCode.ER_ACCESS_DENIED_ERROR:
            # TODO check why are we still getting access denied here, the container should have all accounts ready by now
            # rethrow client and retriable errors
            raise
        else:
            raise


def monitor_clone(session: 'ClassicSession', start_time: str, logger: Logger) -> None:
    logger.info("Waiting for clone...")
    while True:
        r = session.run_sql("select * from performance_schema.clone_progress")
        time.sleep(1)


def finish_clone_seed_pod(session: 'ClassicSession', cluster: InnoDBCluster, logger: Logger) -> None:
    logger.info(f"Finalizing clone - Not implemented")
    return


    # copy sysvars that affect data, if any
    # TODO

    logger.info(f"Clone finished successfully")


def get_secret(secret_name: str, namespace: str, logger: Logger) -> dict:
    logger.info(f"load_dump::get_secret")

    if not secret_name:
        raise Exception(f"No secret provided")

    ret = {}
    try:
        secret = cast(api_client.V1Secret, api_core.read_namespaced_secret(secret_name, namespace))
        for k, v in secret.data.items():
            ret[k] = utils.b64decode(v)
    except Exception:
        raise Exception(f"Secret {secret_name} in namespace {namespace} cannot be found")

    return ret

class RestoreDump(ABC):
    def __init__(self, init_spec: DumpInitDBSpec, namespace: str, logger: Logger) -> None:
        super().__init__()
        self.init_spec = init_spec
        self.storage = init_spec.storage
        self.namespace = namespace
        self.logger = logger

        self.create_config()

    def __del__(self):
        try:
            self.clean_config()
        except:
            # We ignore cleanup errors, which is fine
            pass

    @abstractmethod
    def create_config(self):
        ...

    @abstractmethod
    def add_options(self, options: dict):
        ...

    @property
    def path(self):
        ...

    @abstractmethod
    def clean_config(self):
        ...


class RestoreOciObjectStoreDump(RestoreDump):
    def __init__(self, init_spec: DumpInitDBSpec, namespace: str, logger: Logger) -> None:
        self.oci_config_file = f"{os.getenv('MYSQLSH_USER_CONFIG_HOME')}/oci_config"
        self.oci_privatekey_file = f"{os.getenv('MYSQLSH_USER_CONFIG_HOME')}/privatekey.pem"
        super().__init__(init_spec, namespace, logger)

    def create_config(self):
        import configparser
        privatekey = None
        config_profile = "DEFAULT"
        config = configparser.ConfigParser()
        oci_credentials = get_secret(self.storage.ociObjectStorage.ociCredentials, self.namespace, self.logger)
        if not isinstance(oci_credentials, dict):
            raise Exception(f"Failed to process credentials secret {self.storage.ociObjectStorage.ociCredentials}")

        for k, v in oci_credentials.items():
            if k != "privatekey":
                config[config_profile][k] = v
            else:
                privatekey = v
                config[config_profile]["key_file"] = self.oci_privatekey_file

        with open(self.oci_config_file, 'w') as f:
            config.write(f)

        with open(self.oci_privatekey_file, 'w') as f:
            f.write(privatekey)

    def add_options(self, options: dict):
       options["ociConfigFile"] = self.oci_config_file
       options["ociProfile"] = "DEFAULT"
       options["osBucketName"] = self.storage.ociObjectStorage.bucketName

    @property
    def path(self):
        return self.storage.ociObjectStorage.prefix

    def clean_config(self):
        try:
            os.remove(self.oci_config_file)
            os.remove(self.oci_privatekey_file)
        except:
            pass

class RestoreS3Dump(RestoreDump):
    def create_config(self):
        s3_credentials = get_secret(self.storage.s3.config, self.namespace, self.logger)
        if not isinstance(s3_credentials, dict):
            raise Exception(f"Failed to process S3 config secret {self.storage.s3.config}")
        configdir = f"{os.getenv('HOME')}/.aws"
        try:
            os.mkdir(configdir)
        except FileExistsError:
            # ok if directory exists
            pass

        for filename in s3_credentials:
            with open(f"{configdir}/{filename}", "w") as file:
                file.write(s3_credentials[filename])

    def add_options(self, options: dict):
        options["s3BucketName"] = self.storage.s3.bucketName
        options["s3Profile"] = self.storage.s3.profile
        if self.storage.s3.endpoint:
            options["s3EndpointOverride"] = self.storage.s3.endpoint

    @property
    def path(self):
        return self.storage.s3.prefix

    def clean_config(self):
        configdir = f"{os.getenv('HOME')}/.aws"
        try:
            os.remove(f"{configdir}/credentials")
            os.remove(f"{configdir}/config")
            os.rmdir(configdir)
        except:
            pass

class RestoreAzureBlobStorageDump(RestoreDump):
    def create_config(self):
        configdir = f"{os.getenv('HOME')}/.azure"
        azure_config = get_secret(self.storage.azure.config, self.namespace, self.logger)
        if not isinstance(azure_config, dict):
            raise Exception(f"Failed to process Azure config secret {self.storage.azure.config}")

        try:
            os.mkdir(configdir)
        except FileExistsError:
            # ok if directory exists
            pass

        with open(f"{configdir}/config", "w") as file:
            file.write(azure_config["config"])

    def add_options(self, options: dict):
        options["azureContainerName"] = self.storage.azure.containerName

    @property
    def path(self):
        return self.storage.azure.prefix

    def clean_config(self):
        configdir = f"{os.getenv('HOME')}/.azure"
        try:
            os.remove(f"{configdir}/config")
            os.rmdir(configdir)
        except:
            pass

class RestorePath(RestoreDump):
    def create_config(self):
        pass

    def add_options(self, options: dict):
        pass

    @property
    def path(self):
        return self.init_spec.path

    def clean_config(self):
        pass

def load_dump(session: 'ClassicSession', cluster: InnoDBCluster, pod: MySQLPod, init_spec: DumpInitDBSpec, logger: Logger) -> None:
    logger.info("::load_dump")
    options = init_spec.loadOptions.copy()
    options["progressFile"] = ""

    restore = None
    if init_spec.storage.ociObjectStorage:
        restore = RestoreOciObjectStoreDump(init_spec, cluster.namespace, logger)
    elif init_spec.storage.s3:
        restore = RestoreS3Dump(init_spec, cluster.namespace, logger)
    elif init_spec.storage.azure:
        restore = RestoreAzureBlobStorageDump(init_spec, cluster.namespace, logger)
    else:
        restore = RestorePath(init_spec, cluster.namespace, logger)

    restore.add_options(options)
    path = restore.path

    logger.info(f"Executing load_dump({path}, {options})")

    assert path
    try:
        mysqlsh.globals.util.load_dump(path, options)
        logger.info("Load_dump finished")
    except mysqlsh.Error as e:
        logger.error(f"Error loading dump: {e}")
        raise
    finally:
        del restore


def join_innodb_cluster_set(session: 'ClassicSession', cluster: InnoDBCluster,
                            init_spec: ClusterSetInitDBSpec, pod: MySQLPod,
                            logger: Logger, create_admin_account):

    primary_root_co = dict(mysqlsh.globals.shell.parse_uri(init_spec.uri))
    # Here we get only the password from the cluster secret. The secret
    # might contain also rootUser and rootHost (mask from where the user connects)
    # shouldn't we respect rootUser and not ask for rootUser in clone_spec?
    # Or...this is different kind of secret?
    primary_root_co["user"] = init_spec.root_user or "root"

    # don't print password by assigning later - but mind: user might provide password in URI ... is it their fault then?
    print(
        f"CONNECTING WITH {primary_root_co} {isinstance(primary_root_co, dict)} {type(primary_root_co)}")

    primary_root_co["password"] = init_spec.get_password(cluster.namespace)

    #import random
    #import string
    #characters = string.ascii_letters + string.digits
    #suffix = ''.join(random.choice(characters) for _ in range(10))

    with SessionWrap(primary_root_co) as primary_session:
        # TODO the create_admin_Account function is passed as callback, quite a hack, need to decide where that has to live, probably the caller ins sidecar_main should establish the right session and preapre the user and then call this function ... or move it all to sidecar_main?
        #  also other accounts are created per pod while binlog is disabled,we however have to make sure mysqladmin accoutns are replicated through tehe complete clusterset, so that current primary can be reached from anywhere, by the time of writing replicacluster can reach all (as here we write to primary), but primary can't reach replicaclsuters (as primary's account isn't replicated over)
        create_admin_account(primary_session, cluster, logger)

    with DbaWrap(connect_dba(primary_root_co, logger)) as dba:
        cluster_set = dba.get_cluster().get_cluster_set()
        add_instance_options = {
            "recoveryProgress": True,
            "recoveryMethod": "clone",
            "timeout": 120
        }
        if not cluster.parsed_spec.tlsUseSelfSigned:
            logger.info("Using TLS GR authentication")
            rdns = cluster.get_tls_issuer_and_subject_rdns()
            add_instance_options["certSubject"] = rdns["subject"]
        else:
            logger.info("Using PASSWORD GR authentication")
        try:
            pod_fqdn = fqdn.pod_fqdn(pod, logger)
            logger.info(f"Creating replica cluster {cluster.name} attached to {pod_fqdn} with options {add_instance_options}")
            cluster_set.create_replica_cluster(
                pod_fqdn,
                #TODO: don't hard code!
                cluster.name,  # + '-' + suffix,
                add_instance_options)
        except:
            logger.error("Exception when creating replica cluster")
            raise
        logger.info("Replica cluster created")


    return session
