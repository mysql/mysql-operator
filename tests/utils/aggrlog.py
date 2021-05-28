# Copyright (c) 2020, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import datetime
import re
from . import fmt
import threading
import time
from kubernetes import client, watch
from kubernetes.client.rest import ApiException


def timestamp():
    n = datetime.datetime.now()
    return n.isoformat().replace("T", " ")


class LogAggregator:
    def __init__(self):
        self.file = None
        self.watched_pods = {}

        self.on_operator = lambda s: None
        self.on_mysql = lambda s: None

    def set_target(self, f):
        self.file = f
        self.file.write(fmt.bold("="*80)+"\n")

    def annotate(self, *args):
        ts = timestamp()
        self.file.write(fmt.purple(
            f"[{ts}] ========> " + " ".join(["%s" % s for s in args]).strip())+"\n")

    def watch_pod(self, ns, pod, fn):
        self.annotate(f"Start watching {ns}/{pod}")
        api_core = client.CoreV1Api()
        nspod = ns+"/"+pod
        while nspod in self.watched_pods:
            try:
                w = watch.Watch()
                for line in w.stream(api_core.read_namespaced_pod_log, name=pod, namespace=ns):
                    fn(nspod, line)
            except ApiException as e:
                if e.status in (400, 404):
                    time.sleep(1)
                else:
                    self.annotate("EXCEPTION WATCHING", nspod, e)
            except Exception as e:
                self.annotate("EXCEPTION WATCHING", nspod, e)

    def watch_operator_pod(self, ns, pod):
        thd = threading.Thread(target=self.watch_pod,
                               args=(ns, pod, self.from_operator))
        self.watched_pods[ns+"/"+pod] = thd
        thd.start()

    def watch_mysql_pod(self, ns, pod):
        thd = threading.Thread(target=self.watch_pod,
                               args=(ns, pod, self.from_mysql))
        self.watched_pods[ns+"/"+pod] = thd
        # thd.start()

    def stop_watch(self, ns, pod):
        nspod = ns+"/"+pod
        if nspod in self.watched_pods:
            self.annotate(f"Stopping watch of {ns}/{pod}")
            thd = self.watched_pods[nspod]
            del self.watched_pods[nspod]
            # thd.join()

    def shutdown(self):
        while self.watched_pods:
            self.stop_watch(*self.watched_pods.popitem()[0].split("/"))

    def write(self, ts, src, level, msg):
        if level in ("DEBUG", "NOTE"):
            color = fmt.dgray
        elif level in ("INFO", "SYSTEM"):
            if src.startswith("MySQL"):
                color = fmt.cyan
            else:
                color = fmt.lcyan
        elif level == "OUTPUT":
            if src.startswith("MySQL"):
                color = fmt.dblue
            else:
                def nop(s):
                    return s
                color = nop
        elif level == "WARNING":
            if src.startswith("MySQL"):
                color = fmt.yellow
            else:
                color = fmt.lyellow
        elif level in ("ERROR", "CRITICAL"):
            if src.startswith("MySQL"):
                color = fmt.red
            else:
                color = fmt.lred
        else:
            color = fmt.bold
        self.file.write(
            color(f"[{ts}] {src:14} [{level:8}] ") + "\n\t\t".join(msg.split("\n")) + "\n")
        self.file.flush()

    def from_mysql(self, pod, text):
        if self.on_mysql:
            self.on_mysql(text)

        m = re.match(
            "([^Z]*)Z ([0-9]+) \[([^]]+)\] \[([^]]*)\] \[([^]]*)\] (.*)", text)
        if m:
            ts, num, level, code, mod, msg = m.groups()
            level = level.upper()
            if level == "NOTE":
                pass
            else:
                ts = ts.replace("T", " ").replace("Z", "")
                self.write(ts, "MySQL."+mod, level,
                           fmt.dblue(f"[{pod}] {num} [{code}] {msg}"))
        else:
            self.write(timestamp(), "MySQL", "OUTPUT",
                       fmt.dblue(f"[{pod}] {text}"))

    def from_operator(self, pod, text):
        if self.on_operator:
            self.on_operator(text)

        if "kopf.objects" in text:
            m = re.match('\[([^]]*)\] ([^ ]*)\s*\[([A-Z]+)\s*\]\s(.*)', text)
            if m:
                ts, src, level, msg = m.groups()
                ts = ts.replace(",", ".") + "000"
                self.write(ts, src, level, msg)
                return
        else:
            if "Traceback (most recent call last):" in text:
                text = fmt.bold(text)
            self.write(timestamp(), "operator", "OUTPUT", text)
