# Copyright (c) 2020, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA


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


