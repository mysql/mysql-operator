# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License, version 2.0,
# as published by the Free Software Foundation.
#
# This program is also distributed with certain software (including
# but not limited to OpenSSL) that is licensed under separate terms, as
# designated in a particular file or component or in included license
# documentation.  The authors of MySQL hereby grant you an additional
# permission to link the program and your derivative works with the
# separately licensed software that they have included with MySQL.
# This program is distributed in the hope that it will be useful,  but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See
# the GNU General Public License, version 2.0, for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA 02110-1301 USA

from . import kutil
import mysqlsh
from kubernetes import client
from kubernetes.stream import stream
from kubernetes.client.rest import ApiException


class MySQLPodSession:
    def __init__(self, ns, podname, user, password):
        self.session = None
        self.proc = None
        self.proc, self.port = kutil.portfw(ns, podname, 3306)
        try:
            self.session = mysqlsh.globals.mysql.get_session(
                {"scheme": "mysql", "user": user, "host": "127.0.0.1", "port": self.port, "password": password})
        except:
            print(ns, "/", podname, "port=", self.port, "pass=", password)
            raise

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


def load_script(ns, podname, script):
    if type(podname) is str or len(podname) == 1:
        args = [podname, "-c", "mysql"]
    else:
        args = [podname[0], "-c", podname[1]]
    kutil.feed_kubectl(script, "exec", args=args +
                       [f"-n{ns}", "-i", "--", "mysql", "-ulocalroot"], check=True)


# Emulate an interactive MySQL session directly in a pod (localroot@localhost)
class MySQLInteractivePodSession:
    def __init__(self, ns, pod, *, host="localhost", user=None, password=None):
        container = None
        if type(pod) is str:
            pod = pod
        elif len(pod) == 1:
            pod = pod[0]
        else:
            pod = pod[0]
            container = pod[1]
        exec_command = ["env", "MYSQLSH_PROMPT_THEME=none",
                        "mysqlsh", "--tabbed", "--sql"]
        if user:
            exec_command += [f"{user}:{password}@{host}"]

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

        out = self.read_until_prompt()
        print("".join(out))

    def __del__(self):
        try:
            self.resp.write_stdin("\\quit\n")
        except:
            pass
        try:
            self.resp.close()
        except:
            pass

    def read_until_prompt(self):
        out = []
        ok = False
        buf = ""
        while self.resp.is_open() and not ok:
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
                print("STDERR", line)
        return "".join(out)

    def read_response(self):
        out = self.read_until_prompt().split(
            "\r\n")[1:]  # 1st line is the cmd we sent
        if out[-1].startswith("mysql-sql ") and out[-1].strip().endswith(">"):
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


if __name__ == "__main__":
    session = MySQLInteractivePodSession("testns", "mycluster-0")

    print("RAW")
    print(session.query_raw("show processlist;"))
    print("NORMAL")
    print(session.query("show processlist;"))
    print("DICT")
    print(session.query_dict("show processlist;"))

    del session
