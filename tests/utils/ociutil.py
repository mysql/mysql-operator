# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# OCI (oci-cli) utilities


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
                           check=check, capture_output=True)
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
