#!/bin/bash
# Copyright (c) 2022, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

set -vx

docker image prune -f

docker rmi $(docker images -f dangling=true -q)

# remove old development images (created to use in a dev or a gerrit branch)
docker image ls --format='{{.Repository}}:{{.Tag}}' \
	| grep -E 'mysql-operator|enterprise-operator|community-operator' \
	| grep -E 'dev|gerrit' \
	| xargs -r -n 1 docker image inspect -f '{{.Id}} {{.Created}}' \
	| awk -v cut_off_date="$(date -d '4 weeks ago' -Ins)" '$2 <= cut_off_date {print $1}' \
	| sort | uniq \
	| xargs -r -n 1 docker image rm -f
