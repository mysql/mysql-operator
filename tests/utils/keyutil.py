# Copyright (c) 2020, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

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


