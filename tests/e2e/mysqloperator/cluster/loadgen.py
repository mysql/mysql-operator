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

import time
from threading import Thread
from mysqlsh import mysql

from utils import kutil


class SessionStats:
    def __init__(self):
        self.start_time = time.time()
        self.end_time = None

        self.num_selects = 0
        self.num_inserts = 0
        self.num_updates = 0

        self.num_stmt_ok = 0
        self.num_stmt_err = 0

        self.mysql_errors = []
        self.consistency_errors = []


class ClientStats:
    def __init__(self):
        self.sessions = []


class LoadGenerator:
    def __init__(self, namespace, cluster_name, num_clients):
        # clients that are connected to the same session the whole time
        self._permanent_rw_clients = []
        self._permanent_ro_clients = []
        # clients that connect and disconnect frequently
        self._short_rw_clients = []
        self._short_ro_clients = []

        # create an ingress from the host to routers

    def start(self):
        pass

    def stop(self):
        pass

    @property
    def num_connections(self):
        return

    @property
    def num_errors(self):
        pass

    @property
    def qps(self):
        pass


class LoadClient(Thread):
    def __init__(self, connect_options):
        super().__init__()

        self._session = None
        self._connect_options = connect_options

    def connect(self):
        if self._session:
            self._session.close()
        self._session = mysql.get_session(self._connect_options)

    def run(self):
        pass


class ConnectChecker:
    pass


class ReadQPSChecker:
    pass


class ReadWriteQPSChecker:
    pass


class ConsistencyChecker:
    pass
