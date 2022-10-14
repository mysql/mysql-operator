# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import TYPE_CHECKING

import mysqlsh

from . import utils

if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession


SQL_INSTALL_MASKING_UDF = [
    "INSTALL PLUGIN data_masking SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_blocklist RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_dictionary RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_dictionary_drop RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_dictionary_load RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_range RETURNS INTEGER  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_rnd_email RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_rnd_pan RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_rnd_ssn RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS gen_rnd_us_phone RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS mask_inner RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS mask_outer RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS mask_pan RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS mask_pan_relaxed RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION IF NOT EXISTS mask_ssn RETURNS STRING  SONAME 'data_masking.so'",
]

SQL_UNINSTALL_MASKING_UDF =[
    "UNINSTALL PLUGIN data_masking",
    "DROP FUNCTION gen_blocklist",
    "DROP FUNCTION gen_dictionary",
    "DROP FUNCTION gen_dictionary_drop",
    "DROP FUNCTION gen_dictionary_load",
    "DROP FUNCTION gen_range",
    "DROP FUNCTION gen_rnd_email",
    "DROP FUNCTION gen_rnd_pan",
    "DROP FUNCTION gen_rnd_ssn",
    "DROP FUNCTION gen_rnd_us_phone",
    "DROP FUNCTION mask_inner",
    "DROP FUNCTION mask_outer",
    "DROP FUNCTION mask_pan",
    "DROP FUNCTION mask_pan_relaxed",
    "DROP FUNCTION mask_ssn",
]

SQL_INSTALL_KEYRING_UDF = [
    "INSTALL PLUGIN keyring_udf SONAME 'keyring_udf.so'",
    "CREATE FUNCTION IF NOT EXISTS keyring_key_generate RETURNS INTEGER SONAME 'keyring_udf.so'",
    "CREATE FUNCTION IF NOT EXISTS keyring_key_fetch RETURNS STRING SONAME 'keyring_udf.so'",
    "CREATE FUNCTION IF NOT EXISTS keyring_key_length_fetch RETURNS INTEGER SONAME 'keyring_udf.so'",
    "CREATE FUNCTION IF NOT EXISTS keyring_key_type_fetch RETURNS STRING SONAME 'keyring_udf.so'",
    "CREATE FUNCTION IF NOT EXISTS keyring_key_store RETURNS INTEGER SONAME 'keyring_udf.so'",
    "CREATE FUNCTION IF NOT EXISTS keyring_key_remove RETURNS INTEGER SONAME 'keyring_udf.so'"
]

SQL_UNINSTALL_KEYRING_UDF = [
    "DROP FUNCTION IF EXISTS keyring_key_generate",
    "DROP FUNCTION IF EXISTS keyring_key_fetch",
    "DROP FUNCTION IF EXISTS keyring_key_length_fetch",
    "DROP FUNCTION IF EXISTS keyring_key_type_fetch",
    "DROP FUNCTION IF EXISTS keyring_key_store",
    "DROP FUNCTION IF EXISTS keyring_key_remove",
    "UNINSTALL PLUGIN keyring_udf"
]

def run_plugin_sql(session: 'ClassicSession', stmts: list[str], logger) -> None:
    """
    Install/Uninstall a plugin by running the defined SQL statements.
    """
    for stmt in stmts:
        try:
            session.run_sql(stmt)
        except mysqlsh.Error as e:
            if e.code == mysqlsh.mysql.ErrorCode.ER_UDF_EXISTS:
                logger.warn(f"UDF Already exists, ignored for \"{stmt}\"")
                continue

            logger.error(f"Failed to run plugin install statement \"{stmt}\": {e}")
            raise

def install_enterprise_encryption(server_version: str, session: 'ClassicSession', logger) -> None:
    min_version = utils.version_to_int("8.0.30")
    installed_version = utils.version_to_int(server_version)
    if installed_version < min_version:
        logger.info(f"Deploying Enterprise Server {server_version}, older than 8.0.30, skipping encryption function installation")
        return

    res = session.run_sql("SELECT * FROM mysql.component WHERE component_urn = 'file://component_enterprise_encryption'")
    row = res.fetch_one()
    if row:
        logger.warn("Enterprise Encryption Component already installed. Skipping.")
        return

    try:
        session.run_sql("INSTALL COMPONENT 'file://component_enterprise_encryption'")
    except mysqlsh.Error as e:
        logger.error(f"Failed to install encryption component: {e}")
        raise

def uninstall_enterprise_encryption(server_version: str, session: 'ClassicSession') -> None:
    min_version = utils.version_to_int("8.0.30")
    installed_version = utils.version_to_int(server_version)
    if installed_version < min_version:
        return

    session.run_sql("UNINSTALL COMPONENT 'file://component_enterprise_encryption'")

def install_enterprise_plugins(server_version: str, session: 'ClassicSession', logger) -> None:
    run_plugin_sql(session, SQL_INSTALL_MASKING_UDF, logger)
    install_enterprise_encryption(server_version, session, logger)

def uninstall_enterprise_plugins(server_version: str, session: 'ClassicSession', logger) -> None:
    run_plugin_sql(session, SQL_UNINSTALL_MASKING_UDF, logger)
    uninstall_enterprise_plugins(server_version, session)

def install_keyring_udf(server_version: str, session: 'ClassicSession', logger) -> None:
    run_plugin_sql(session, SQL_INSTALL_KEYRING_UDF, logger)

def uninstall_keyring_udf(server_version: str, session: 'ClassicSession', logger) -> None:
    run_plugin_sql(session, SQL_UNINSTALL_KEYRING_UDF, logger)
