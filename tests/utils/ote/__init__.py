# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from .passthrough import PassthroughEnvironment
from .minikube import MinikubeEnvironment
from .k3d import K3dEnvironment
from .kind import KindEnvironment

_drivers = {
    "minikube": MinikubeEnvironment,
    "k3d": K3dEnvironment,
    "kind": KindEnvironment,
    "pass": PassthroughEnvironment
}


def get_driver(name):
    if name in _drivers:
        driver = _drivers[name]
        print(f"Using kubernetes environment {driver.name}")
        return driver()
    raise Exception(f"Invalid driver {name}")
