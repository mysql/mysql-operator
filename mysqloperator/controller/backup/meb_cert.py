# Copyright (c) 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import base64
import os
import subprocess
import tempfile

MEB_TLS_SECRET_KEYS = ("ca.pem", "client.pem", "client.key",
                       "server.pem", "server.key")
VALIDITY_DAYS = "365"


def _run(cmd):
    subprocess.run(cmd, check=True)


def _write_ca_req(path: str) -> None:
    with open(path, "w") as f:
        f.write("""[req]
distinguished_name = req_distinguished_name
x509_extensions = v3_ca
prompt = no
[req_distinguished_name]
C = AU
ST = Some-State
O = My CA
CN = MySQLOperatorRoot
[v3_ca]
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid:always,issuer
basicConstraints = critical, CA:true
keyUsage = critical, keyCertSign, cRLSign
""")


def _write_cert_req(path: str, common_name: str, usage: str,
                    dns_names: list[str] | None = None) -> None:
    with open(path, "w") as f:
        f.write(f"""[req]
distinguished_name = req_distinguished_name
req_extensions = v3_req
prompt = no
[req_distinguished_name]
C = AU
ST = None
O = MySQLOperator
CN = {common_name}
[v3_req]
basicConstraints = critical, CA:false
keyUsage = critical, digitalSignature
extendedKeyUsage = {usage}
""")
        if dns_names:
            f.write("subjectAltName = @alt_names\n[alt_names]\n")
            for i, dns_name in enumerate(dns_names, 1):
                f.write(f"DNS.{i} = {dns_name}\n")


def _make_key(path: str) -> None:
    _run(["openssl", "genrsa", "-out", path, "2048"])


def _make_ca(tmpdir: str) -> None:
    ca_conf = f"{tmpdir}/ca-req.conf"
    _write_ca_req(ca_conf)
    _make_key(f"{tmpdir}/ca.key")
    _run(["openssl", "req", "-x509", "-new", "-nodes",
          "-key", f"{tmpdir}/ca.key",
          "-sha256", "-days", VALIDITY_DAYS,
          "-out", f"{tmpdir}/ca.pem",
          "-config", ca_conf,
          "-extensions", "v3_ca"])


def _make_cert(tmpdir: str, name: str, common_name: str, usage: str,
               dns_names: list[str] | None = None) -> None:
    req_conf = f"{tmpdir}/{name}-req.conf"
    key = f"{tmpdir}/{name}.key"
    csr = f"{tmpdir}/{name}.csr"
    cert = f"{tmpdir}/{name}.pem"

    _write_cert_req(req_conf, common_name, usage, dns_names)
    _make_key(key)
    _run(["openssl", "req", "-new", "-key", key,
          "-out", csr, "-config", req_conf])
    _run(["openssl", "x509", "-req", "-in", csr,
          "-CA", f"{tmpdir}/ca.pem", "-CAkey", f"{tmpdir}/ca.key",
          "-CAcreateserial", "-out", cert, "-days", VALIDITY_DAYS,
          "-sha256", "-extensions", "v3_req", "-extfile", req_conf])


def _b64_file(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _meb_server_dns_names(spec: 'InnoDBClusterSpec', cluster_domain: str,
                          fqdn_template: str | None) -> list[str]:
    if not fqdn_template:
        fqdn_template = "{service}.{namespace}.svc.{domain}"

    service = spec.headless_service_name
    rendered_service = fqdn_template.format(
        service=service,
        namespace=spec.namespace,
        domain=cluster_domain)

    names = [f"*.{rendered_service}"]
    short_name = f"*.{service}"
    if short_name not in names:
        names.append(short_name)
    return names


def prepare_meb_server_tls_data(spec: 'InnoDBClusterSpec',
                                ca_pem: str,
                                ca_key: str,
                                cluster_domain: str,
                                fqdn_template: str | None = None) -> dict:
    with tempfile.TemporaryDirectory() as tmpdir:
        with open(f"{tmpdir}/ca.pem", "w") as f:
            f.write(ca_pem)
        with open(f"{tmpdir}/ca.key", "w") as f:
            f.write(ca_key)

        _make_cert(
            tmpdir, "server", f"{spec.name}-meb", "serverAuth",
            _meb_server_dns_names(spec, cluster_domain, fqdn_template))

        return {
            "server.pem": _b64_file(f"{tmpdir}/server.pem"),
            "server.key": _b64_file(f"{tmpdir}/server.key"),
        }


def prepare_meb_tls_secret(spec: 'InnoDBClusterSpec',
                           cluster_domain: str = "cluster.local",
                           fqdn_template: str | None = None) -> dict:
    """Create Certificates
    this is only used internally and checked against our CA"""

    with tempfile.TemporaryDirectory() as tmpdir:
        _make_ca(tmpdir)
        _make_cert(tmpdir, "client", "backupclient", "clientAuth")
        _make_cert(
            tmpdir, "server", f"{spec.name}-meb", "serverAuth",
            _meb_server_dns_names(spec, cluster_domain, fqdn_template))

        secret_data = {
            filename: _b64_file(os.path.join(tmpdir, filename))
            for filename in MEB_TLS_SECRET_KEYS
        }

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
