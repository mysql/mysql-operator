# Copyright (c) 2020, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


from .base import BaseEnvironment


class PassthroughEnvironment(BaseEnvironment):
    name = "Pass-through"

    def load_images(self, images):
        pass

    def start_cluster(self, nodes, version):
        pass

    def stop_cluster(self):
        pass

    def delete_cluster(self):
        pass
