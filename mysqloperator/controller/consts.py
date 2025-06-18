# Copyright (c) 2020, 2025, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

GROUP = "mysql.oracle.com"
VERSION = "v2"
API_VERSION = GROUP+"/"+VERSION

INNODBCLUSTER_KIND = "InnoDBCluster"
INNODBCLUSTER_PLURAL = "innodbclusters"

MYSQLBACKUP_KIND = "MySQLBackup"
MYSQLBACKUP_PLURAL = "mysqlbackups"


# This would be a bit better located in config.py, but we put
# it here to avoid a cyclic import
TLS_VALID_CIPHERS = [
    'ECDHE-ECDSA-AES128-GCM-SHA256',
    'ECDHE-ECDSA-AES256-GCM-SHA384',
    'ECDHE-RSA-AES128-GCM-SHA256',
    'TLS_AES_128_GCM_SHA256',
    'TLS_AES_128_GCM_SHA256',
    'TLS_CHACHA20_POLY1305_SHA256',
    'TLS_AES_128_CCM_SHA256',
    'ECDHE-RSA-AES256-GCM-SHA384',
    'ECDHE-ECDSA-CHACHA20-POLY1305',
    'ECDHE-RSA-CHACHA20-POLY1305',
    'ECDHE-ECDSA-AES256-CCM',
    'ECDHE-ECDSA-AES128-CCM',
    '!NULL',
    '!eNULL',
    '!MD5'
]