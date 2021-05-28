# Copyright (c) 2020, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


DEFAULT_MYSQL_ACCOUNTS = ["mysql.infoschema@localhost", "mysql.session@localhost", "mysql.sys@localhost"]


COMMON_OPERATOR_ERRORS = ["Default peering object not found",
    "Handler .* failed temporarily",
    "Error executing .* giving up:",
    "functools.partial",
    # The following 2 errors happen because of the exception with status 422 (kopf bug)
    "\[[^]]*-0\] Owner cluster for [^ ]* does not exist anymore",
    "Handler 'on_pod_delete' failed permanently: Cluster object deleted before Pod",
    # Can be thrown by the shell when the PRIMARY changes during a topology change
    "force remove_instance failed. error=Error 51102: Cluster.remove_instance: Metadata cannot be updated: MySQL Error 1290"]


