#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from datetime import datetime
import json
import sys

if len(sys.argv) != 2:
	print("usage: <path_to_vaults_secrets_list_in_json_format>")
	sys.exit(1)

vault_secrets_path = sys.argv[1]

secret_age_limit_in_hours = 4
utc_now = datetime.utcnow().timestamp()

f = open(vault_secrets_path)

vault_secrets = json.load(f)
for vault_secret in vault_secrets["data"]:
	if vault_secret["lifecycle-state"] != "ACTIVE":
		continue

	# "time-created": "2022-09-30T16:50:27.853000+00:00",
	secret_time_created = datetime.fromisoformat(vault_secret["time-created"])
	secret_timestamp = secret_time_created.timestamp()
	secret_age_in_hours = (utc_now - secret_timestamp) / 3600

	if secret_age_in_hours < secret_age_limit_in_hours:
		continue

	print(vault_secret["id"])

f.close()
