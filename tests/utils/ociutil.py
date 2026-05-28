# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# OCI utilities

import datetime
import json
import logging
import subprocess
import urllib.parse
from setup.config import g_ts_cfg

debug_ocicli = False

logger = logging.getLogger("oci-cli")

def _load_oci_sdk():
    import oci

    return oci

def _object_storage_client(profile):
    oci = _load_oci_sdk()
    config = oci.config.from_file(g_ts_cfg.oci_config_path, profile)
    return oci, config, oci.object_storage.ObjectStorageClient(config)

def _get_namespace(client):
    return client.get_namespace().data

def ocicli(profile, cmd, subcmd=None, args=None, timeout=None, check=True, ignore=[]):
    argv = ["oci", "--config-file", g_ts_cfg.oci_config_path, "--profile", profile, cmd]
    if subcmd:
        argv += subcmd
    if args:
        argv += args
    if debug_ocicli:
        logger.debug("run %s", " ".join(argv))
    try:
        r = subprocess.run(argv, timeout=timeout,
            check=check, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        for ig in ignore:
            if "(%s)" % ig in e.stderr.decode("utf8"):
                if debug_ocicli:
                    logger.debug("rc = %s, stderr=%s",
                                 e.returncode, e.stderr.decode("utf8"))
                return
        else:
            logger.error("oci-cli %s failed (rc=%s):\n    stderr=%s\n    stdout=%s",
                         e.cmd, e.returncode,
                         e.stderr.decode("utf8"), e.stdout.decode("utf8"))
            raise
    if debug_ocicli:
        logger.debug("rc = %s, stdout = %s", r.returncode,
                     r.stdout.decode("utf8"))
    return r

def bulk_delete(profile, bucket_name, prefix):
    _, _, client = _object_storage_client(profile)
    namespace = _get_namespace(client)
    for object_summary in list_objects(profile, bucket_name, prefix):
        client.delete_object(namespace, bucket_name, object_summary.name)

def list_objects(profile, bucket_name, prefix = None):
    _, _, client = _object_storage_client(profile)
    namespace = _get_namespace(client)
    objects = []
    start = None
    while True:
        result = client.list_objects(
            namespace, bucket_name, prefix=prefix, start=start)
        objects.extend(result.data.objects)
        start = result.data.next_start_with
        if not start:
            return objects

def get_object_storage_namespace(profile):
    _, _, client = _object_storage_client(profile)
    return _get_namespace(client)

def create_preauthenticated_request(profile, bucket_name, name, object_name,
                                    access_type, expires_in_hours=1):
    oci, config, client = _object_storage_client(profile)
    namespace = _get_namespace(client)
    time_expires = datetime.datetime.utcnow() + datetime.timedelta(hours=expires_in_hours)
    request_details = oci.object_storage.models.CreatePreauthenticatedRequestDetails(
        name=name,
        access_type=access_type,
        time_expires=time_expires,
        object_name=object_name)
    response = client.create_preauthenticated_request(
        namespace, bucket_name, request_details)
    data = response.data
    url = f"https://objectstorage.{config['region']}.oraclecloud.com{data.access_uri}"
    return {
        "id": data.id,
        "access_uri": data.access_uri,
        "url": url,
    }

def delete_preauthenticated_request(profile, bucket_name, par_id):
    _, _, client = _object_storage_client(profile)
    namespace = _get_namespace(client)
    client.delete_preauthenticated_request(namespace, bucket_name, par_id)

def create_prefix_par_base_url(profile, bucket_name, name, prefix,
                               expires_in_hours=1):
    par = create_preauthenticated_request(
        profile, bucket_name, name, prefix, "AnyObjectRead", expires_in_hours)
    encoded_prefix = urllib.parse.quote(prefix, safe="/")
    fully_encoded_prefix = urllib.parse.quote(prefix, safe="")
    for term in (prefix, encoded_prefix, fully_encoded_prefix):
        if term and par["url"].endswith(term):
            par["parBaseUrl"] = par["url"][:-len(term)]
            return par
    for term in (prefix, encoded_prefix, fully_encoded_prefix):
        if term and term in par["url"]:
            par["parBaseUrl"] = par["url"].replace(term, "", 1)
            return par
    par["parBaseUrl"] = par["url"]
    return par

def delete_vault_secret_by_id(profile, secret_ocid):
    args = ['--secret-id', secret_ocid]
    return ocicli(profile, 'vault', subcmd=['secret', 'schedule-secret-deletion'], args=args)

def delete_vault_secret_by_name(profile, compartment_id, vault_id, secret_name):
    args = ['--compartment-id', compartment_id, '--vault-id', vault_id]
    result = ocicli(profile, 'vault', subcmd=['secret', 'list'], args=args)
    vault_secrets = json.loads(result.stdout)
    for vault_secret in vault_secrets["data"]:
        if "freeform-tags" not in vault_secret:
            continue
        secret_freeform_tags = vault_secret["freeform-tags"]
        if "name_id" not in secret_freeform_tags:
            continue
        vault_secret_name = secret_freeform_tags["name_id"]
        if vault_secret_name == secret_name:
            secret_id = vault_secret["id"]
            delete_vault_secret_by_id(profile, secret_id)
