#!/bin/bash
# Copyright (c) 2023, Oracle and/or its affiliates. All rights reserved.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 2 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA
set -e

REPO=mysql/mysql-operator; [ -n "$1" ] && REPO=$1
MANIFEST_VERSIONS=("${@:2}")

if [ -z $MANIFEST_VERSIONS ]; then
    VER=$(./tag.sh)
    MAJ_VER=${VER:0:3}
    MANIFEST_VERSIONS=($VER $MAJ_VER)
fi

for MANIFEST_VERSION in ${MANIFEST_VERSIONS[@]}
do
    docker pull "$REPO:$MANIFEST_VERSION-arm64" "$REPO:$MANIFEST_VERSION-amd64"
    docker manifest create "$REPO:$MANIFEST_VERSION" "$REPO:$MANIFEST_VERSION-arm64" "$REPO:$MANIFEST_VERSION-amd64"
    docker manifest add "$REPO:$MANIFEST_VERSION" "$REPO:$MANIFEST_VERSION-arm64" --os linux --arch arm64
    docker manifest add "$REPO:$MANIFEST_VERSION" "$REPO:$MANIFEST_VERSION-amd64" --os linux --arch amd64
    docker manifest push "$REPO:$MANIFEST_VERSION" "docker://$REPO:$MANIFEST_VERSION"
done
