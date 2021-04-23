# Copyright (c) 2020, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from kubernetes import client
from kubernetes.client.rest import ApiException

api_core = client.CoreV1Api()


def get_fingerprint(key):
    from hashlib import md5
    from codecs import decode

    # There should be more error checking here, but to keep the example simple
    # the error checking has been omitted.
    m = md5()

    # Strip out the parts of the key that are not used in the fingerprint
    # computation.
    key = key.replace(b'-----BEGIN PUBLIC KEY-----\n', b'')
    key = key.replace(b'\n-----END PUBLIC KEY-----', b'')

    # The key is base64 encoded and needs to be decoded before getting the md5
    # hash
    decoded_key = decode(key, "base64")
    m.update(decoded_key)
    hash = m.hexdigest()

    # Break the hash into 2 character parts.
    length = 2
    parts = list(hash[0 + i:length + i] for i in range(0, len(hash), length))

    # Join the parts with a colon seperator
    fingerprint = ":".join(parts)

    return fingerprint


def create_api_key(secret_name = None, key_name = None):
    # Get the current config
    import oci.identity
    import time
    import os.path
    from pathlib import Path

    # Generate the API Keys
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend()
    )

    private_key = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption())

    public_key = key.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo
    )

    print("PRIVATE", private_key.decode())

    return public_key.decode(), get_fingerprint(public_key)


if __name__ == "__main__":
    pubkey, fp = create_api_key()
    print("public_key=",pubkey, "fingerprint=",fp)


