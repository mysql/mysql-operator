# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from e2e.mysqloperator.handle_8_0_29 import handle_29_base

# test the following scenario:
# set up a cluster version 8.0.29 -> failure -> delete -> all gone
class Setup29ThenDelete(handle_29_base.Handle29Base):
    def test_0_run(self):
        self.create_cluster("8.0.29")
        self.verify_cluster_invalid()

    def test_9_destroy(self):
        self.destroy_cluster()
