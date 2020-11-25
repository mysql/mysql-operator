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

from typing import TYPE_CHECKING
from .cluster_api import DumpInitDBSpec, MySQLPod, InitDB, CloneInitDBSpec, InnoDBCluster
from ..shellutils import SessionWrap
from .. import mysqlutils
import mysqlsh
import time
import os
from logging import Logger
if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession


def start_clone_seed_pod(session: 'ClassicSession',
                         cluster: InnoDBCluster,
                         seed_pod: MySQLPod, clone_spec: CloneInitDBSpec,
                         logger: Logger) -> bool:
    logger.info(
        f"Initializing seed instance. method=clone  pod={seed_pod}  source={clone_spec.uri}")

    donor_root_co = mysqlsh.globals.shell.parse_uri(clone_spec.uri)
    donor_root_co["user"] = clone_spec.root_user or "root"
    donor_root_co["password"] = clone_spec.get_password(cluster.namespace)

    print(f"CONNECTING WITH {donor_root_co}")

    with SessionWrap(donor_root_co) as donor:
        clone_installed = False
        for row in iter(donor.run_sql("SHOW PLUGINS").fetch_one, None):
            if row[3]:
                logger.info(f"Donor has plugin {row[0]} / {row[3]}")
                if row[0] == "clone":
                    clone_installed = True

        if not clone_installed:
            logger.info(f"Installing clone plugin at {donor.uri}")
            donor.run_sql("install plugin clone soname 'mysql_clone.so'")

        # TODO copy other installed plugins(?)

    # clone
    try:
        donor_co = dict(mysqlsh.globals.shell.parse_uri(clone_spec.uri))
        donor_co["password"] = clone_spec.get_password(cluster.namespace)

        with SessionWrap(donor_co) as donor:
            return mysqlutils.clone_server(donor_co, donor, session, logger)
    except mysqlsh.Error as e:
        if mysqlutils.is_client_error(e.code) or e.code == mysqlsh.mysql.ErrorCode.ER_ACCESS_DENIED_ERROR:
            # TODO check why are we still getting access denied here, the container should have all accounts ready by now
            # rethrow client and retriable errors
            raise
        else:
            raise


def monitor_clone(session: 'ClassicSession', start_time: str, logger: Logger) -> None:
    logger.info("Waiting for clone...")
    while True:
        r = session.run_sql("select * from performance_schema.clone_progress")
        time.sleep(1)


def finish_clone_seed_pod(session: 'ClassicSession', cluster: InnoDBCluster, logger: Logger) -> None:
    return
    logger.info(f"Finalizing clone")

    # copy sysvars that affect data, if any
    # TODO

    logger.info(f"Clone finished successfully")


def load_dump(session: 'ClassicSession', cluster: InnoDBCluster, pod: MySQLPod, init_spec: DumpInitDBSpec, logger: Logger) -> None:
    options = init_spec.loadOptions.copy()

    if init_spec.storage.ociObjectStorage:
        path = init_spec.storage.ociObjectStorage.prefix
        options["osBucketName"] = init_spec.storage.ociObjectStorage.bucketName
        options["ociConfigFile"] = "/.oci/config"
        options["ociProfile"] = "DEFAULT"
    else:
        path = init_spec.path

    logger.info(f"Executing load_dump({path}, {options})")

    assert path
    try:
        mysqlsh.globals.util.load_dump(path, options)
    except mysqlsh.Error as e:
        logger.error(f"Error loading dump: {e}")
        raise
