#!/usr/bin/env python
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


"""
Schedule all secrets not specially marked for deletion

This will mark all currently ACTIVE secrets of a vault for deletion in 24h,
which is the minimum deletion period.

If a secret has a freeform tag "no-delete" set, it will not be deleted. In
addition one can set Secret OCIDs in the OCI_VAULT_CLEAN_IGNORE_LIST environment
variable.

See --help for invocation and review the code. This script is meant for
automated invocation from some cleanup job, but we will change it as need be.
"""

import argparse
from datetime import datetime, timedelta
import os
import sys

try:
    from oci.config import from_file as config_from_file
    from oci.exceptions import ServiceError
    from oci.pagination import list_call_get_all_results_generator
    from oci.vault import VaultsClient
    from oci.vault.models import ScheduleSecretDeletionDetails
except ModuleNotFoundError as exc:
    print(f"Failed to load OCI module: {exc}", file=sys.stderr)
    sys.exit(1)


def eprint(*text, **kwargs):
    """Print to STDERR"""
    print(*text, file=sys.stderr, **kwargs)


def find_and_clean(profile, compartment_id, vault_id, ignore_list, confirm):
    config = config_from_file(profile_name=profile)
    vault_client = VaultsClient(config)
    secrets = list_call_get_all_results_generator(
        vault_client.list_secrets,
        yield_mode="record",
        compartment_id=compartment_id)

    secrets = filter(lambda item: item.vault_id == vault_id, secrets)
    secrets = filter(lambda item: item.lifecycle_state == "ACTIVE", secrets)
    secrets = filter(lambda item: "no-delete" not in item.freeform_tags, secrets)
    secrets = filter(lambda item: item.id not in ignore_list, secrets)

    try:
        # As we are using a generator only here we will do a network call to
        # OCI, thus auth errors etc. are reported from here.
        # We only *have to* make this a list in confirm mode, but this makes
        # things simpler
        todelete = list(secrets)
    except ServiceError as exc:
        eprint("\033[0;31mFailed to load Secrets from OCI. Error:")
        eprint(f"\033[0;32m{exc.message}\033[0m")
        sys.exit(1)

    if not todelete:
        if confirm:
            print("\033[0;31mNothing to do!\033[0m")
        return

    if confirm:
        print("\033[0;31mThese Screts are being marked for deletion in 24h:\033[0m")
        print("\n".join(map(lambda item: f"\033[0;32m{item.secret_name}\n\033[0;33m{item.id}\033[0m", todelete)))
        try:
            input(f'\033[0;31mPress Enter to delete {len(todelete)} Secrets or Ctrl-C to abort\033[0m')
        except KeyboardInterrupt:
            return

        counter = 0

    for item in todelete:
        # using one day and one minute, so that it is guaranteed more than minimal time, also recaclulating
        # in each iteration in case the loop runs longer than a minute
        delete_time = datetime.utcnow() + timedelta(days=1, minutes=1)

        if confirm:
            counter = counter + 1
            print(f'\033[0;33m({counter}/{len(todelete)}) \033[0;32m{item.secret_name :{" "}<{70}.70}\033[0m', end='\r')

        vault_client.schedule_secret_deletion(
                item.id,
                ScheduleSecretDeletionDetails(time_of_deletion=delete_time.strftime('%Y-%m-%dT%TZ'))
                )

    if confirm:
        # Make sure the last update line can be read
        print()


if __name__ == '__main__':
    ignore_list = os.getenv("OCI_VAULT_CLEAN_IGNORE_LIST", "")

    parser = argparse.ArgumentParser(
                        description='Schedule Deletion for Secretsin OCI Vault.')
    parser.add_argument('--profile', dest='profile', default="DEFAULT",
                        help="OCI Configuration Profile (Default: DEFAULT)")
    parser.add_argument('--compartment-id', dest='compartment_id', required=True,
                        help="Compartment")
    parser.add_argument('--vault-id', dest='vault_id', required=True,
                        help="Compartment")
    parser.add_argument('--confirm', dest='confirm', action="store_true",
                        help="Print Secrets to be deleted and ask for confirmation")
    args = parser.parse_args()

    find_and_clean(args.profile, args.compartment_id, args.vault_id, ignore_list, args.confirm)
