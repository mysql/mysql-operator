# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import base64
import os
import subprocess
import tempfile

def _run(cmd):
    subprocess.run(cmd, check=True)


def prepare_meb_tls_secret(spec: 'InnoDBClusterSpec') -> dict:
    """Create Certificates
    this is only used internally and checked against our CA"""

    with tempfile.TemporaryDirectory() as tmpdir:
        # create CA
        _run(["openssl", "genrsa", "-out", f"{tmpdir}/ca.key", "2048"])
        _run(["openssl", "req", "-x509", "-new", "-nodes", "-key",
              f"{tmpdir}/ca.key", "-sha256", "-days", "365",
              "-out", f"{tmpdir}/ca.pem",
              "-subj", "/C=AU/ST=Some-State/O=My CA/CN=MySQLOperatorRoot"])

        _run(["openssl", "genrsa", "-out", f"{tmpdir}/client.key", "2048"])
        _run(["openssl", "req", "-new", "-key", f"{tmpdir}/client.key",
              "-out", f"{tmpdir}/client.csr",
              "-subj", f"/C=AU/ST=None/O=MySQLOperator/CN=backupclient"])
        _run(["openssl", "x509", "-req", "-in", f"{tmpdir}/client.csr",
              "-CA", f"{tmpdir}/ca.pem", "-CAkey", f"{tmpdir}/ca.key",
              "-CAcreateserial", "-out", f"{tmpdir}/client.pem",
              "-days", "365", "-sha256"])


        secret_data = {}
        for filename in os.listdir(tmpdir):
            full_path = os.path.join(tmpdir, filename)
            if os.path.isfile(full_path):
                with open(full_path, "rb") as f:
                    content = f.read()
                    secret_data[filename] = base64.b64encode(content).decode('ascii')

        secret = {
            "apiVersion": "v1",
            "kind": "Secret",
            "metadata": {
                "name": spec.name+"-meb-tls",
                "tier": "mysql",
                "mysql.oracle.com/cluster": spec.name,
                "app.kubernetes.io/name": "mysql-innodbcluster",
                "app.kubernetes.io/instance": f"idc-{spec.name}",
                "app.kubernetes.io/managed-by": "mysql-operator",
                "app.kubernetes.io/created-by": "mysql-operator"
            },
            "data": secret_data
        }

        return secret
