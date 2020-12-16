# Copyright (c) 2017, 2020, Oracle and/or its affiliates.
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
FROM mysql/mysql-server:@VERSION@

ENV MYSQL_UNIX_PORT /var/lib/mysql/mysql.sock

VOLUME /var/lib/mysql

# We don't want the default healthcheck since we override
# it from the Pod.
HEALTHCHECK NONE

COPY docker-entrypoint.sh /entrypoint.sh

COPY mysql-commercial-backup-8.0.21-1.1.el7.x86_64.rpm /

RUN yum localinstall /mysql-commercial-backup-8.0.21-1.1.el7.x86_64.rpm\
     && rm -f /mysql-commercial-backup-8.0.21-1.1.el7.x86_64.rpm

EXPOSE 3306 33060 33061
CMD ["mysqld"]
