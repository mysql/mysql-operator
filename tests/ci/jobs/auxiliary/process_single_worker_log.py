# Copyright (c) 2020, 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import sys
import process_workers_logs

if len(sys.argv) != 3:
	print("usage: <expected-failues-path> <log-path>")
	sys.exit(1)

expected_failures_path = sys.argv[1]
log_path = sys.argv[2]
log_paths = [log_path]

if not process_workers_logs.run(expected_failures_path, log_paths, None):
	sys.exit(2)
