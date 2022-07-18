# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import TYPE_CHECKING

import mysqlsh

if TYPE_CHECKING:
    from mysqlsh.mysql import ClassicSession

SQL_INSTALL_OPENSSL_UDF = [
    "CREATE FUNCTION asymmetric_decrypt         RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION asymmetric_derive          RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION asymmetric_encrypt         RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION asymmetric_sign            RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION asymmetric_verify          RETURNS INTEGER SONAME 'openssl_udf.so'",
    "CREATE FUNCTION create_asymmetric_priv_key RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION create_asymmetric_pub_key  RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION create_dh_parameters       RETURNS STRING  SONAME 'openssl_udf.so'",
    "CREATE FUNCTION create_digest              RETURNS STRING  SONAME 'openssl_udf.so'"
]

SQL_UNINSTALL_OPENSSL_UDF = [
    "DROP FUNCTION asymmetric_decrypt",
    "DROP FUNCTION asymmetric_derive",
    "DROP FUNCTION asymmetric_encrypt",
    "DROP FUNCTION asymmetric_sign",
    "DROP FUNCTION asymmetric_verify",
    "DROP FUNCTION create_asymmetric_priv_key",
    "DROP FUNCTION create_asymmetric_pub_key",
    "DROP FUNCTION create_dh_parameters",
    "DROP FUNCTION create_digest"
]

SQL_INSTALL_MASKING_UDF = [
    "INSTALL PLUGIN data_masking SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_blocklist RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_dictionary RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_dictionary_drop RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_dictionary_load RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_range RETURNS INTEGER  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_rnd_email RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_rnd_pan RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_rnd_ssn RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION gen_rnd_us_phone RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION mask_inner RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION mask_outer RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION mask_pan RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION mask_pan_relaxed RETURNS STRING  SONAME 'data_masking.so'",
    "CREATE FUNCTION mask_ssn RETURNS STRING  SONAME 'data_masking.so'",
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

def install_enterprise_plugins(session: 'ClassicSession', logger) -> None:
    run_plugin_sql(session, SQL_INSTALL_OPENSSL_UDF, logger)
    run_plugin_sql(session, SQL_INSTALL_MASKING_UDF, logger)


def uninstall_enterprise_plugins(session: 'ClassicSession', logger) -> None:
    run_plugin_sql(session, SQL_UNINSTALL_OPENSSL_UDF, logger)
    run_plugin_sql(session, SQL_UNINSTALL_MASKING_UDF, logger)

