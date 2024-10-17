#!/bin/bash
# Copyright (c) 2021, 2024 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

SUFFIX='aarch64'; [ -n "$1" ] && SUFFIX=${1}

echo "9.1.0-2.2.2$SUFFIX"
