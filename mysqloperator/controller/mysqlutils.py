# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import mysqlsh
from typing import List


def is_client_error(code):
    return mysqlsh.mysql.ErrorCode.CR_MIN_ERROR <= code <= mysqlsh.mysql.ErrorCode.CR_MAX_ERROR


def clone_server(donor_co, donor_session, recip_session, logger):
    """
    Clone recipient server from donor.
    If clone already happened, return False, otherwise True.
    Throws exception on any error.
    """
    mysql = mysqlsh.mysql

    donor = f"{donor_co.get('host', 'localhost')}:{donor_co.get('port', 3306)}"
    recip_co = mysqlsh.globals.shell.parse_uri(recip_session.uri)
    recip = f"{recip_co.get('host', 'localhost')}:{recip_co.get('port', 3306)}"

    try:
        logger.debug(f"Installing clone plugin at {recip}")
        recip_session.run_sql("INSTALL PLUGIN clone SONAME 'mysql_clone.so'")
    except mysqlsh.Error as e:
        logger.debug(f"Error installing clone plugin at {recip}: {e}")
        if e.code == mysql.ErrorCode.ER_UDF_EXISTS:
            pass
        else:
            raise

    # Check if clone was already executed from the logs
    res = recip_session.run_sql("""SELECT
        state, begin_time, end_time, source, error_no, error_message
    FROM performance_schema.clone_status
    ORDER BY id DESC LIMIT 1""")
    row = res.fetch_one()
    if row:
        logger.info(
            f"Previous clone execution detected at {recip}: source={row[3]}  status={row[0]}  started={row[1]}  ended={row[2]}  errno={row[4]}  error={row[5]}")
        if row[0] == "Completed" and row[3] == donor:
            return False

    # check if the donor has the GR plugin installed and if so, install it
    res = donor_session.run_sql("show plugins")
    source_plugins = set()
    for row in res.fetch_all():
        if row[1] == "ACTIVE" and row[3] != None:
            source_plugins.add(row[0])
    res = recip_session.run_sql("show plugins")
    dest_plugins = set()
    for row in res.fetch_all():
        if row[1] == "ACTIVE" and row[3] != None:
            dest_plugins.add(row[0])
    missing_plugins = source_plugins - dest_plugins
    if missing_plugins:
        if "group_replication" in missing_plugins:
            try:
                logger.debug(f"Installing group_replication plugin at {recip}")
                recip_session.run_sql(
                    "INSTALL PLUGIN group_replication SONAME 'group_replication.so'")
            except mysqlsh.Error as e:
                logger.debug(
                    f"Error installing group_replication plugin at {recip}: {e}")
                if e.code == mysql.ErrorCode.ER_UDF_EXISTS:
                    pass
                else:
                    raise
            missing_plugins.remove("group_replication")
        if missing_plugins:
            logger.warning(
                f"The following plugins are installed at the donor but not the recipient: {missing_plugins}")

    # do other validations that the clone plugin doesn't
    logger.info(f"Starting clone from {donor} to {recip}")
    try:
        recip_session.run_sql("SET GLOBAL clone_valid_donor_list=?", [donor])

        recip_session.run_sql("CLONE INSTANCE FROM ?@?:? IDENTIFIED BY ?", [
                              donor_co["user"], donor_co["host"], donor_co.get("port", 3306), donor_co["password"]])
    except mysqlsh.Error as e:
        logger.debug(f"Error executing clone from {donor} at {recip}: {e}")
        raise

    # If everything went OK, the server should be restarting now.
    return True


def setup_backup_account(session, user, password):
    session.run_sql(f"DROP USER IF EXISTS {user}")
    session.run_sql(f"CREATE USER {user} IDENTIFIED BY ?", [password])
    session.run_sql(
        f"GRANT select, show databases, show view, lock tables, reload ON *.* TO {user}")
    session.run_sql(
        f"GRANT backup_admin /*!80020 , show_routine */ ON *.* TO {user}")

def setup_metrics_user(session: 'mysqlsh.ClassicSession', user: str,
                       grants: List, max_connections: int) -> None:
    host = "localhost"
    grants = ", ".join(grants)

    session.run_sql("DROP USER IF EXISTS ?@?", [user, host])
    session.run_sql(
        "CREATE USER IF NOT EXISTS ?@? IDENTIFIED WITH auth_socket AS 'daemon' WITH MAX_USER_CONNECTIONS ?", [user, host, max_connections])
    session.run_sql("REVOKE ALL PRIVILEGES, GRANT OPTION FROM ?@?", [user, host])
    session.run_sql(f"GRANT {grants} ON *.* TO ?@? WITH GRANT OPTION", [user, host])


def remove_metrics_user(session: 'mysqlsh.ClassicSession') -> None:
    user = "mysqlmetrics"
    host = "localhost"

    session.run_sql("DROP USER IF EXISTS ?@?", [user, host])


def count_gtids(gtid_set: str) -> int:
    """Return number of transactions in the GTID set"""
    def count_range(r):
        begin, _, end = r.partition("-")
        if not end:
            return 1
        else:
            return int(end)-int(begin)+1
    n = 0
    for g in gtid_set.replace("\n", "").split(","):
        for r in g.split(":")[1:]:
            n += count_range(r)
    return n


