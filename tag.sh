#!/bin/bash
# Copyright (c) 2021, 2023 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

SUFFIX=''; [ -n "$1" ] && ARCH="-${1}"

echo "8.0.34-2.0.11$ARCH"
