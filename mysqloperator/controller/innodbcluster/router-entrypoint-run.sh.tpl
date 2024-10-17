#!/bin/bash
# Copyright (c) 2018, 2024, Oracle and/or its affiliates.
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

if [[ -n $MYSQL_USER_FILE && -f $MYSQL_USER_FILE ]]; then
    MYSQL_USER=$(cat $MYSQL_USER_FILE)
fi

if [ "$1" = 'mysqlrouter' ]; then
    if [[ -z $MYSQL_HOST || -z $MYSQL_PORT || -z $MYSQL_USER || (-z $MYSQL_PASSWORD && -z $MYSQL_PASSWORD_FILE) ]]; then
	    echo "We require all of"
	    echo "    MYSQL_HOST"
	    echo "    MYSQL_PORT"
	    echo "    MYSQL_USER or MYSQL_USER_FILE"
	    echo "    MYSQL_PASSWORD or MYSQL_PASSWORD_FILE"
	    echo "to be set."
	    echo "In addition you can set"
	    echo "    MYSQL_INNODB_CLUSTER_MEMBERS"
	    echo "    MYSQL_CREATE_ROUTER_USER"
	    echo "    MYSQL_ROUTER_BOOTSTRAP_EXTRA_OPTIONS"
	    echo "Exiting."
	    exit 1
    fi

    PASSFILE=$(mktemp)
    if  [[ -n $MYSQL_PASSWORD_FILE && -f "$MYSQL_PASSWORD_FILE" ]]; then
        cat "$MYSQL_PASSWORD_FILE" > "$PASSFILE"
        echo "" >> "$PASSFILE"
    else
        echo "$MYSQL_PASSWORD" > "$PASSFILE"
    fi
    if [ -z $MYSQL_CREATE_ROUTER_USER ]; then
      if  [[ -n $MYSQL_PASSWORD_FILE && -f "$MYSQL_PASSWORD_FILE" ]]; then
        cat "$MYSQL_PASSWORD_FILE" >> "$PASSFILE"
        echo "" >> "$PASSFILE"
      else
        echo "$MYSQL_PASSWORD" >> "$PASSFILE"
      fi
      MYSQL_CREATE_ROUTER_USER=1
      echo "[Entrypoint] MYSQL_CREATE_ROUTER_USER is not set, Router will generate a new account to be used at runtime."
      echo "[Entrypoint] Set it to 0 to reuse $MYSQL_USER instead."
    elif [ "$MYSQL_CREATE_ROUTER_USER" = "0" ]; then
      if  [[ -n $MYSQL_PASSWORD_FILE && -f "$MYSQL_PASSWORD_FILE" ]]; then
        cat "$MYSQL_PASSWORD_FILE" >> "$PASSFILE"
        echo "" >> "$PASSFILE"
      else
        echo "$MYSQL_PASSWORD" >> "$PASSFILE"
      fi
      echo "[Entrypoint] MYSQL_CREATE_ROUTER_USER is 0, Router will reuse $MYSQL_USER account at runtime"
    else
      echo "[Entrypoint] MYSQL_CREATE_ROUTER_USER is not 0, Router will generate a new account to be used at runtime"
    fi

    if  [[ -n $MYSQL_PASSWORD_FILE && -f "$MYSQL_PASSWORD_FILE" ]]; then
      DEFAULTS_EXTRA_FILE=$(mktemp)
      cat >"$DEFAULTS_EXTRA_FILE" <<EOF
[client]
password="$(cat $MYSQL_PASSWORD_FILE)"
EOF
    else
      DEFAULTS_EXTRA_FILE=$(mktemp)
      cat >"$DEFAULTS_EXTRA_FILE" <<EOF
[client]
password="$MYSQL_PASSWORD"
EOF
    fi
    unset MYSQL_PASSWORD

    max_tries=12
    attempt_num=0
    until (echo > "/dev/tcp/$MYSQL_HOST/$MYSQL_PORT") >/dev/null 2>&1; do
      echo "[Entrypoint] Waiting for mysql server $MYSQL_HOST ($attempt_num/$max_tries)"
      sleep $(( attempt_num++ ))
      if (( attempt_num == max_tries )); then
        exit 1
      fi
    done
    echo "[Entrypoint] Succesfully contacted mysql server at $MYSQL_HOST:$MYSQL_PORT. Checking for cluster state."
    if ! [[ "$(mysql --defaults-extra-file="$DEFAULTS_EXTRA_FILE" -u "$MYSQL_USER" -h "$MYSQL_HOST" -P "$MYSQL_PORT" -e "show status;" 2> /dev/null)" ]]; then
      echo "[Entrypoint] ERROR: Can not connect to database. Exiting."
      exit 1
    fi
    if [[ -n $MYSQL_INNODB_CLUSTER_MEMBERS ]]; then
      attempt_num=0
      echo $attempt_num
      echo $max_tries
      until [ "$(mysql --defaults-extra-file="$DEFAULTS_EXTRA_FILE" -u "$MYSQL_USER" -h "$MYSQL_HOST" -P "$MYSQL_PORT" -N performance_schema -e "select count(MEMBER_STATE) >= $MYSQL_INNODB_CLUSTER_MEMBERS from replication_group_members where MEMBER_STATE = 'ONLINE';" 2> /dev/null)" -eq 1 ]; do
             echo "[Entrypoint] Waiting for $MYSQL_INNODB_CLUSTER_MEMBERS cluster instances to become available via $MYSQL_HOST ($attempt_num/$max_tries)"
             sleep $(( attempt_num++ ))
             if (( attempt_num == max_tries )); then
                     exit 1
             fi
      done
      echo "[Entrypoint] Successfully contacted cluster with $MYSQL_INNODB_CLUSTER_MEMBERS members. Bootstrapping."
    fi
    if [ $(id -u) = "0" ]; then
      opt_user=--user=mysqlrouter
    fi
    if [ "$MYSQL_CREATE_ROUTER_USER" = "0" ]; then
        echo "[Entrypoint] Succesfully contacted mysql server at $MYSQL_HOST. Trying to bootstrap reusing account \"$MYSQL_USER\"."
        mysqlrouter --bootstrap "$MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT" --directory /tmp/mysqlrouter --force --account-create=never --account=$MYSQL_USER $opt_user $MYSQL_ROUTER_BOOTSTRAP_EXTRA_OPTIONS < "$PASSFILE" || exit 1
    else
        echo "[Entrypoint] Succesfully contacted mysql server at $MYSQL_HOST. Trying to bootstrap."
        mysqlrouter --bootstrap "$MYSQL_USER@$MYSQL_HOST:$MYSQL_PORT" --directory /tmp/mysqlrouter --force $opt_user $MYSQL_ROUTER_BOOTSTRAP_EXTRA_OPTIONS < "$PASSFILE" || exit 1
    fi

    rm "$DEFAULTS_EXTRA_FILE"

    sed -i -e 's/logging_folder=.*$/logging_folder=/' /tmp/mysqlrouter/mysqlrouter.conf
    echo "[Entrypoint] Starting mysql-router."
    exec "$@" --config /tmp/mysqlrouter/mysqlrouter.conf

    rm $PASSFILE
else
    exec "$@"
fi
