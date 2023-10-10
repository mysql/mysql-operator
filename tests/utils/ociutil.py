# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# OCI (oci-cli) utilities

import json
import logging
import subprocess
from setup.config import g_ts_cfg

debug_ocicli = False

logger = logging.getLogger("oci-cli")

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
    return ocicli(profile, "os", subcmd=["object", "bulk-delete"],
        args=["--force", "--bucket-name", bucket_name, "--prefix", prefix])

def list_objects(profile, bucket_name, prefix = None):
    args = ["--bucket-name", bucket_name ]
    if prefix:
         args += ["--prefix", prefix]

    runresult = ocicli(profile, "os", subcmd=["object", "list"], args=args)
    result = json.loads(runresult.stdout)

    # if no elements with the given prefix are found oci will return {prefix:[]}
    if not "data" in result:
        return []

    return result["data"]

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
