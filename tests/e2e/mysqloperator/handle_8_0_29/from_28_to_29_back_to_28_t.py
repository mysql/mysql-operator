# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from e2e.mysqloperator.handle_8_0_29 import handle_29_base

# test the following scenario:
# set up a cluster version 8.0.28 -> upgrade to 8.0.29 -> rejected -> stay with 8.0.28 -> running
class From28To29BackTo28(handle_29_base.Handle29Base):
    def test_0_run(self):
        self.create_cluster("8.0.28")
        self.verify_cluster_running()
        self.verify_cluster_version("8.0.28")

        update_time = self.change_cluster_version("8.0.29")
        self.verify_update_rejected(update_time, "8.0.28", "8.0.29")

        self.verify_cluster_running()
        self.verify_cluster_version("8.0.28")

    def test_9_destroy(self):
        self.destroy_cluster()
