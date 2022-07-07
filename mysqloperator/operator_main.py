# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


from .controller import config as myconfig
import mysqlsh
import asyncio
import kopf
import os
import time
import logging

# this will register operator event handlers
from .controller import operator

from .controller import k8sobject


k8sobject.g_component = "operator"
k8sobject.g_host = os.getenv("HOSTNAME")


def main(argv):
    mysqlsh.globals.shell.options.useWizards = False

    myconfig.config_from_env()

    kopf.configure(verbose=True if myconfig.debug >= 1 else False)

    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s - [%(levelname)s] [%(name)s] %(message)s',
                        datefmt="%Y-%m-%dT%H:%M:%S")

    loop = asyncio.get_event_loop()

    # Priority defines the priority/weight of this instance of the operator for
    # kopf peering. If there are multiple operator instances in the cluster,
    # only the one with the highest priority will actually be active.
    loop.run_until_complete(kopf.operator(
        clusterwide=True,
        priority=int(time.time()*1000000),
        peering_name="mysql-operator" # must be the same as the identified in ClusterKopfPeering
    ))

    return 0


if __name__ == "__main__":
    main([])
