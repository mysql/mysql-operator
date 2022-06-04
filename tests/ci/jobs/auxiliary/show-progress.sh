#!/bin/bash
# Copyright (c) 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# script to avoid timeout ("FATAL: command execution failed") while
# running long-lasting 'muted' commands like 'image save | bzip2' operation
if [ "$#" -ne 2 ]; then
	echo "usage: <iterations> <sleep-interval>"
	exit 1
fi

for i in $(eval echo "{1..$1}"); do
	echo '='
	sleep $2
done
