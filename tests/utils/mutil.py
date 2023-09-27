# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import time
from . import kutil
import mysql.connector
from kubernetes import client
from kubernetes.stream import stream
from kubernetes.client.rest import ApiException
import logging

logger = logging.getLogger("mutil")

class MySQLDbResult:
    def __init__(self, cursor):
        self._cursor = cursor

    def fetch_one(self):
        result = self._cursor.fetchone()
        self._cursor.close()
        return result

    def fetch_all(self):
        result = self._cursor.fetchall()
        self._cursor.close()
        return result


class MySQLDbSession:
    def __init__(self, user, password, host, port, database, **kwargs):
        self._session = mysql.connector.connect(user=user, password=password,
                            host=host, port=port, database=database,
                            **kwargs)

    def close(self):
        if self._session:
            self._session.close()
            self._session = None

    def exec_sql(self, query, params=None):
        cursor = self._session.cursor()
        cursor.execute(query, params)
        cursor.close()

    def query_sql(self, query, params=None):
        cursor = self._session.cursor()
        cursor.execute(query, params)
        return MySQLDbResult(cursor)


class MySQLPodSession:
    def __init__(self, ns, podname, user, password, port=3306,
                 target_type="pod", **kwargs):
        self.session = None
        self.proc = None
        for retries in range(6):
            try:
                self.proc, self.port = kutil.portfw(ns, podname, port,
                                                    target_type)
                self.session = MySQLDbSession(user=user, password=password,
                                host='127.0.0.1',
                                port=self.port,
                                database='mysql',
                                **kwargs)
                break
            except Exception as e:
                logger.error(f"{ns}/{podname}, port={port}, pass={password}, {e}")
                if retries == 5:
                    raise
                time.sleep(1)
                logger.debug("init mysql session retrying...")

    def __del__(self):
        self.close()

    def __enter__(self, *args):
        return self.session

    def __exit__(self, *args):
        self.close()

    def close(self):
        if self.session:
            self.session.close()
            self.session = None
        if self.proc:
            self.proc.terminate()
            self.proc = None

def load_script(ns, podname, script, user='root', password='sakila'):
    if type(podname) is str or len(podname) == 1:
        args = [podname, "-c", "mysql"]
    else:
        args = [podname[0], "-c", podname[1]]
    kutil.feed_kubectl(script, "exec", args=args +
                       [f"-n{ns}", "-i", "--", "mysql", f"-u{user}", f"-p{password}"], check=True)


def load_file(ns, podname, script_path, user='root', password='sakila'):
    script = open(script_path).read()
    load_script(ns, podname, script, user, password)


# Emulate an interactive MySQL session directly in a pod (localroot@localhost)
class MySQLInteractivePodSession:
    def __init__(self, ns, pod, *, host="localhost", user=None, password=None, args=[]):
        container = None
        if type(pod) is str:
            pod = pod
        elif len(pod) == 1:
            pod = pod[0]
        else:
            pod = pod[0]
            container = pod[1]
        exec_command = ["env", "MYSQLSH_PROMPT_THEME=",
                        "mysqlsh", "--sql", "--tabbed"]
        if user:
            exec_command += [f"{user}:{password}@{host}"]
        if args:
            exec_command += args

        logger.debug("%s", exec_command)

        api_core = client.CoreV1Api()

        if container:
            self.resp = stream(api_core.connect_get_namespaced_pod_exec,
                               pod,
                               ns,
                               container=container,
                               command=exec_command,
                               stderr=True, stdin=True,
                               stdout=True, tty=True,
                               _preload_content=False)
        else:
            self.resp = stream(api_core.connect_get_namespaced_pod_exec,
                               pod,
                               ns,
                               command=exec_command,
                               stderr=True, stdin=True,
                               stdout=True, tty=True,
                               _preload_content=False)
        assert self.resp.is_open()
        self.resp.update(timeout=1)

        self.read_until_prompt()


    def __del__(self):
        self.close()


    def close(self):
        try:
            self.resp.write_stdin("\\quit\n")
        except:
            pass
        try:
            self.resp.close()
        except:
            pass


    def __enter__(self, *args):
        return self


    def __exit__(self, *args):
        self.close()


    def read_until_prompt(self):
        out = []
        ok = False
        buf = ""
        iteration = 0
        MAX_ITERATION = 200
        while not ok and self.resp.is_open() and iteration < MAX_ITERATION:
            self.resp.update(timeout=1)
            while self.resp.peek_stdout():
                buf += self.resp.read_stdout(timeout=1)
                if not buf:
                    break
                if "\n" in buf:
                    last_nl = buf.rfind("\n") + 1
                    out.append(buf[:last_nl])
                    buf = buf[last_nl:]
                if buf.startswith("mysql-sql ") and buf.strip().endswith(">"):
                    out.append(buf)
                    buf = ""
                    ok = True
                    break
            while self.resp.peek_stderr():
                line = self.resp.read_stderr(timeout=1)
                if not line:
                    break
            iteration += 1
            if iteration * 2 == MAX_ITERATION:
                # sometimes we don't receive prompt, provoke a reaction by sending a newline char
                time.sleep(3)
                self.resp.write_stdin("\n")
        if not ok:
            raise RuntimeError("Prompt not met while processing mysql session output")
        return "".join(out)

    def read_response(self):
        out = self.read_until_prompt().split(
            "\r\n")[1:]  # 1st line is the cmd we sent
        if out and out[-1].startswith("mysql-sql ") and out[-1].strip().endswith(">"):
            del out[-1]  # delete prompt
        return out

    def execute(self, sql):
        self.resp.write_stdin(sql + "\n")
        return self.read_response()[:-1]

    def query_raw(self, sql):
        self.resp.write_stdin(sql + "\n")
        return "\n".join(self.read_response())

    def query(self, sql):
        self.resp.write_stdin(sql + "\n")
        out = self.read_response()
        status = out[-1]
        names = out[0].split("\t")
        rows = [r.split("\t") for r in out[1:-1]]
        return names, rows

    def query_dict(self, sql):
        names, rows = self.query(sql)
        return [dict(zip(names, row)) for row in rows]


def router_rest_api_create_user(session: MySQLDbSession, username: str, host="%"):
    """Take the password hash from a MySQL user and make it a Router REST API user"""
    session.query_sql(
        """INSERT INTO mysql_innodb_cluster_metadata.router_rest_accounts
                    (cluster_id, user, authentication_string)
            VALUES ((SELECT cluster_id FROM mysql_innodb_cluster_metadata.v2_clusters LIMIT 1),
                    "%s",
                    (select authentication_string from mysql.user where user = "%s" and host = "%s"))""" % (username, username, host))
    session.query_sql("commit")


if __name__ == "__main__":
    session = MySQLInteractivePodSession("testns", "mycluster-0")

    logger.info("RAW")
    logger.info(session.query_raw("show processlist;"))
    logger.info("NORMAL")
    logger.info(session.query("show processlist;"))
    logger.info("DICT")
    logger.info(session.query_dict("show processlist;"))

    del session
