#!/bin/bash
# Copyright (c) 2022, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

./purge_containers.sh

./purge_volumes.sh

./purge_networks.sh

./purge_images.sh
