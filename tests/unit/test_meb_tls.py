# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import ast
import base64
import subprocess

from pathlib import Path

from mysqloperator.controller.backup import meb_cert


class _Spec:
    name = "mebtest"
    namespace = "mebns"
    headless_service_name = "mebtest-instances"


def _write_secret_file(tmp_path, secret, key):
    path = tmp_path / key
    path.write_bytes(base64.b64decode(secret["data"][key]))
    return path


def test_prepare_meb_tls_secret_has_verifiable_server_and_client_certs(tmp_path):
    secret = meb_cert.prepare_meb_tls_secret(
        _Spec(), "cluster.local", "{service}.{namespace}.svc.{domain}")

    assert set(secret["data"].keys()) == set(meb_cert.MEB_TLS_SECRET_KEYS)

    ca = _write_secret_file(tmp_path, secret, "ca.pem")
    server = _write_secret_file(tmp_path, secret, "server.pem")
    client = _write_secret_file(tmp_path, secret, "client.pem")

    subprocess.run([
        "openssl", "verify", "-purpose", "sslserver",
        "-verify_hostname",
        "mebtest-0.mebtest-instances.mebns.svc.cluster.local",
        "-CAfile", str(ca), str(server),
    ], check=True)
    subprocess.run([
        "openssl", "verify", "-purpose", "sslclient",
        "-CAfile", str(ca), str(client),
    ], check=True)


def test_execute_meb_never_disables_tls_verification():
    source = Path("mysqloperator/backup_main.py").read_text()
    tree = ast.parse(source)
    execute_meb = next(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "execute_meb")

    post_calls = [
        node for node in ast.walk(execute_meb)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "post"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "requests"
    ]
    assert post_calls

    verify_keywords = [
        keyword.value
        for call in post_calls
        for keyword in call.keywords
        if keyword.arg == "verify"
    ]
    assert verify_keywords
    assert all(
        not (isinstance(value, ast.Constant) and value.value is False)
        for value in verify_keywords)
    assert any(
        isinstance(value, ast.Name) and value.id == "ca"
        for value in verify_keywords)
