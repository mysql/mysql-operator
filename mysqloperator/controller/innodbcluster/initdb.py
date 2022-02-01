# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import TYPE_CHECKING, cast
from .cluster_api import DumpInitDBSpec, MySQLPod, InitDB, CloneInitDBSpec, InnoDBCluster
from ..shellutils import SessionWrap
from .. import mysqlutils, utils
from ..kubeutils import api_core, api_apps, api_customobj
from ..kubeutils import client as api_client, ApiException
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

    print(
        f"CONNECTING WITH {donor_root_co} {isinstance(donor_root_co, dict)} {type(donor_root_co)}")

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
            return mysqlutils.clone_server(donor_co, donor, session, logger)
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


def load_dump(session: 'ClassicSession', cluster: InnoDBCluster, pod: MySQLPod, init_spec: DumpInitDBSpec, logger: Logger) -> None:
    def get_secret(secret_name: str, namespace: str, loger: Logger) -> dict:
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

    def create_oci_config(oci_credentials: dict) -> dict:
        import configparser
        # MYSQLSH_USER_CONFIG_HOME is the only writable place
        oci_config_file     = f"{os.getenv('MYSQLSH_USER_CONFIG_HOME')}/oci_config"
        oci_privatekey_file = f"{os.getenv('MYSQLSH_USER_CONFIG_HOME')}/privatekey.pem"
        privatekey = None
        config_profile = "DEFAULT"
        config = configparser.ConfigParser()
        for k, v in oci_credentials.items():
            if k != "privatekey":
                config[config_profile][k] = v
            else:
                privatekey = v
                config[config_profile]["key_file"] = oci_privatekey_file

        with open(oci_config_file, 'w') as f:
            config.write(f)

        with open(oci_privatekey_file, 'w') as f:
            f.write(privatekey)

        return {
            "ociConfigFile" : oci_config_file,
            "ociProfile" : config_profile,
        }


    logger.info("::load_dump")
    options = init_spec.loadOptions.copy()
    options["progressFile"] = "";

    oci_credentials = None
    if init_spec.storage.ociObjectStorage:
        oci_credentials = get_secret(init_spec.storage.ociObjectStorage.ociCredentials, cluster.namespace, logger)
        if isinstance(oci_credentials, dict):
            path = init_spec.storage.ociObjectStorage.prefix
            options["osBucketName"] = init_spec.storage.ociObjectStorage.bucketName
            options.update(create_oci_config(oci_credentials))
    else:
        path = init_spec.path

    logger.info(f"Executing load_dump({path}, {options})")

    assert path
    try:
        mysqlsh.globals.util.load_dump(path, options)
        logger.info("Load_dump finished")
    except mysqlsh.Error as e:
        logger.error(f"Error loading dump: {e}")
        raise
