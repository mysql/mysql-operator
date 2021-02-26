# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import sys
import importlib

entrypoints = {
    "operator": ".operator_main",
    "sidecar": ".sidecar_main",
    "init": ".init_main",
    "backup": ".backup_main",
    "sleep": None
}

if sys.argv[1] in entrypoints:
    if sys.argv[1] == "sleep":
        print("Sleeping...")
        import time
        time.sleep(3600)
        sys.exit(0)
    mod = importlib.import_module(entrypoints[sys.argv[1]], "mysqloperator")
    sys.exit(mod.main(sys.argv[1:]))  # type: ignore
else:
    print("Invalid args:", sys.argv)
    sys.exit(1)
