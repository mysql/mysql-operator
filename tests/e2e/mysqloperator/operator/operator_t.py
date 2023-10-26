# Copyright (c) 2020, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# multiple operator instances in the same cluster

# don't touch mysql pods that aren't ours

# operator crash handling

# check that the MAX_SUPPORTED_MYSQL_VERSION is the same as shell.version

from utils import tutil
from utils import kutil
import logging
from utils.tutil import g_full_log
from utils.optesting import COMMON_OPERATOR_ERRORS

class OperatorTest(tutil.OperatorTest):
    default_allowed_op_errors = COMMON_OPERATOR_ERRORS

    @classmethod
    def setUpClass(cls):
        cls.logger = logging.getLogger(__name__+":"+cls.__name__)
        super().setUpClass()

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()


    def test_1_check_security(self):
        """
        Ensure PodSecurityContext has required restrictions.
        """

        def check_pod(pod, process):
            # kubectl exec runs as the mysql user
            out = kutil.execp("mysql-operator", [pod, "mysql-operator"], ["id"])
            self.assertTrue(out.startswith(b"uid="))
            self.assertNotEqual(f"uid=0(root) gid=0(root) groups=0(root)", out.strip().decode("utf-8"))

            # cmdline of process 1 is mysqld
            out = kutil.execp("mysql-operator", [pod, "mysql-operator"], ["cat", "/proc/1/cmdline"])
            self.assertEqual(process, out.split(b"\0")[0].decode("utf-8"))

            # /proc/1 is owned by (runs as) uid=mysql/27, gid=mysql/27
            out = kutil.execp("mysql-operator", [pod, "mysql-operator"], ["stat", "/proc/1"])
            access = [line for line in out.split(b"\n") if line.startswith(b"Access")][0].strip().decode("utf-8")
            self.assertTrue(access)
            self.assertNotEqual(f"Access: (0555/dr-xr-xr-x)  Uid: ({0:5}/{'root':>8})   Gid: ({0:5}/{'root':>8})", access)

        p = kutil.ls_po("mysql-operator", pattern="mysql-operator-.*")[0]["NAME"]
        check_pod(p, "mysqlsh")
