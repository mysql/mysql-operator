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
    ret = 0
    try:
        mod = importlib.import_module(entrypoints[sys.argv[1]], "mysqloperator")
        # don't pass the name of the module, thus [2:] istead of [1:]
        ret = mod.main(sys.argv[2:])  # type: ignore

    except Exception as exc:
        print(f"Exception happened in entrypoint {sys.argv[1]}. The message is: {exc}")
        ret = 1
    sys.exit(ret)
elif sys.argv[1] == "pytest":
    import pytest
    sys.exit(pytest.main(sys.argv[2:]))
else:
    print("Invalid args:", sys.argv)
    sys.exit(1)
