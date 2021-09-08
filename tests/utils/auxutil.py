# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import base64
import datetime

def isotime() -> str:
    return datetime.datetime.utcnow().replace(microsecond=0).isoformat()+"Z"

def b64encode(s: str) -> str:
    return base64.b64encode(bytes(s, "utf8")).decode("ascii")
