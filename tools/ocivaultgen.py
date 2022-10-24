#!/usr/bin/env python
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

"""
Generate MySQL configuration for usage with OCI Key Vault

This script guides interatively thorugh the selection of OCI Key Vault settings
and will, where need be, offer to create a Vault and Key with some default
settings. Those default settings, like software based key storage, key len,
etc. may not be best practice for production use and are ment for testing
or experimental use. Production grade configuration should be made elsewhere.
This script then may still be useful for generating the MySQL Server or MySQL
Operator configuration.

This script is meant for interactive use.
"""

try:
    import oci
except ModuleNotFoundError as exc:
    import sys
    print(f"Failed to load OCI module: {exc}", file=sys.stderr)
    sys.exit(1)

def menu(items):
    if len(items) == 1:
        return items[0][1]

    i = 0
    for item in items:
        i = i + 1
        print(f"\033[0;32m{i:2}) \033[0;33m{item[0]}\033[0m")

    response = input(f"Pick 1 to {i}: ")
    try:
        selection = int(response)
    except ValueError:
        selection = 0

    if selection < 1 or selection > i:
        return menu(items)

    return items[selection-1][1]


def pick_profile():
    profile = input("\033[0;33mProfile:\033[0m ")
    if not profile:
        profile="DEFAULT"
    try:
        return oci.config.from_file(profile_name=profile)
    except Exception as exc:
        print(f"\033[0;31mFailed to find profile \033[0;33m{profile}\033[0m: {exc}")
        return pick_profile()

def pick_compartment(identity: oci.identity.IdentityClient, current):
    """Let user recursively pick the compartment they need"""
    my = identity.get_compartment(current).data
    print(f"Current compartment {my.name} ({my.description})\n")

    compartments = identity.list_compartments(current)
    choices = [['Current', my]] + list(map(lambda item: [item.name, item], compartments.data))
    pick = menu(choices)
    if pick != my:
        return pick_compartment(identity, pick.id)

    return my

def no_https(input):
    return input.replace("https://", "")


config = pick_profile()

identity_client = oci.identity.IdentityClient(config)
compartment = pick_compartment(identity_client, config["tenancy"])
print(f"\033[0;33mCompartment picked: \033[0;32m{compartment.name} \033[0m({compartment.description})")

print("\033[0;33mPick Vault:\033[0m")
vault_client = oci.key_management.KmsVaultClient(config)
choices = list(map(lambda item: [f"{item.display_name} ({item.lifecycle_state})", item],
                   vault_client.list_vaults(compartment.id).data))
vault = menu(choices + [["Create a New Vault", None]])
if not vault:
    vault_client_composite =  oci.key_management.KmsVaultClientCompositeOperations(vault_client)

    vault_details = oci.key_management.models.CreateVaultDetails(
        compartment_id=compartment.id,
        vault_type="DEFAULT",
        display_name=input("Display Name of new vault: "))

    print("Creating and waiting to be ready ...")
    vault = vault_client_composite.create_vault_and_wait_for_state(
        vault_details,
        wait_for_states=[oci.key_management.models.Vault.LIFECYCLE_STATE_ACTIVE]).data

print(f"Vault picked: {vault.display_name}")

print("\033[0;33mSelect Master Key:\033[0m")
kms_client = oci.key_management.KmsManagementClient(config, vault.management_endpoint)
choices = list(map(lambda item: [f"{item.display_name} ({item.lifecycle_state})", item],
                   kms_client.list_keys(compartment.id).data))
key = menu(choices + [["Create a New Key", None]])
if not key:
    kms_client_composite = oci.key_management.KmsManagementClientCompositeOperations(kms_client)

    key_shape = oci.key_management.models.KeyShape(algorithm="AES", length=32)
    key_details = oci.key_management.models.CreateKeyDetails(
        compartment_id=compartment.id,
        display_name=input("Display Name for new Key: "),
        protection_mode=oci.key_management.models.CreateKeyDetails.PROTECTION_MODE_SOFTWARE,
        key_shape=key_shape)

    print("Creating key and waiting to be ready ...")
    key = kms_client_composite.create_key_and_wait_for_state(key_details,
                         wait_for_states=[oci.key_management.models.Key.LIFECYCLE_STATE_ENABLED]).data


print(f"Key picked: {key.display_name}")

def ask_format():
    while True:
        result = menu([["my.cnf", "mycnf"], ["Operator YAML", "yaml"], ["End", "end"]])
        if result == "end":
            return
        yield result


for format in ask_format():
    if format == "mycnf":
        print("\n\033[0;32mYour MySQL Config:\033[0m")

        print(f"""
[mysqld]
early-plugin-load=keyring_oci.so
keyring_oci_user={config['user']}
keyring_oci_tenancy={config['tenancy']}
keyring_oci_compartment={compartment.id}
keyring_oci_virtual_vault={vault.id}
keyring_oci_master_key={key.id}
keyring_oci_encryption_endpoint={no_https(vault.crypto_endpoint)}
keyring_oci_management_endpoint={no_https(vault.management_endpoint)}
keyring_oci_vaults_endpoint=vaults.{config['region']}.oci.oraclecloud.com
keyring_oci_secrets_endpoint=secrets.vaults.{config['region']}.oci.oraclecloud.com
keyring_oci_key_file={config['key_file']}
keyring_oci_key_fingerprint={config['fingerprint']}
        """)

    if format == "yaml":
        print("\n\033[0;32mYour MySQL Operator Config:\033[0m")

        print(f"""
keyring:
    oci:
        user: {config['user']}
        keySecret: oci-vault-key
        keyFingerprint: {config['fingerprint']}
        tenancy: {config['tenancy']}
        compartment: {compartment.id}

        virtualVault: {vault.id}
        masterKey: {key.id}
        endpoints:
            encryption: {no_https(vault.crypto_endpoint)}
            management: {no_https(vault.management_endpoint)}
            vaults: vaults.{config['region']}.oci.oraclecloud.com
            secrets: secrets.vaults.{config['region']}.oci.oraclecloud.com

\n\033[0;32mRun this command to create the secret containing the key:\033[0m

  kubectl create secret generic         \\
    -n YOUR_K8S_NAMESPACE oci-vault-key \\
    --from-file=privatekey={config['key_file']}
        """)
