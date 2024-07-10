# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#
# auxiliary script to merge k8s job stats per given k8s-environment (minikube|k3d|kind)
# parse input like:
# <intput>
# k3d: 60 tests, 58 passed, 2 failed, 0 skipped [1h 40m 10.541000s (6010.541s)]
# minikube: 75 tests, 55 passed, 7 failed, 13 skipped [0h 59m 53.088000s (3593.088s)]
# k3d: 75 tests, 52 passed, 3 failed, 20 skipped [0h 58m 20.792000s (3500.792s)]
# minikube: 89 tests, 68 passed, 4 failed, 17 skipped [1h 54m 21.090000s (6861.09s)]
# </intput>
# to generate summary output:
# <output>
# k3d: 135 tests, 110 passed, 5 failed, 20 skipped [2h 38m 31s (9,511.33s)]
# minikube: 164 tests, 123 passed, 11 failed, 30 skipped [2h 54m 14s (10,454.18s)]
# </output>

from collections import defaultdict
import re
import sys
import datetime

def format_duration(total_seconds):
    td = datetime.timedelta(seconds=total_seconds)
    hours, minutes_seconds = divmod(td.seconds, 3600)
    minutes, seconds = divmod(minutes_seconds, 60)
    return f"{hours}h {minutes}m {seconds}s ({total_seconds:,.2f}s)"

k8s_job_stats = sys.stdin.readlines()

k8s_env_to_stats = defaultdict(lambda: {'tests': 0, 'passed': 0, 'failed': 0, 'skipped': 0, 'time': 0})

# the input for regex, e.g.:
# minikube: 10 tests, 7 passed, 2 failed, 1 skipped [0h 16m 02.135000s (962.135s)]
# k3d: 18 tests, 18 passed, 0 failed, 0 skipped [0h 15m 29.762000s (929.762s)]
k8s_job_stats_format = r'(.+): (\d+) tests, (\d+) passed, (\d+) failed, (\d+) skipped \[.* \((\d+.?\d*)s\)\]'

for k8s_worker_stats in k8s_job_stats:
    k8s_env, tests, passed, failed, skipped, time = re.findall(k8s_job_stats_format, k8s_worker_stats)[0]
    k8s_env_to_stats[k8s_env]['tests'] += int(tests)
    k8s_env_to_stats[k8s_env]['passed'] += int(passed)
    k8s_env_to_stats[k8s_env]['failed'] += int(failed)
    k8s_env_to_stats[k8s_env]['skipped'] += int(skipped)
    k8s_env_to_stats[k8s_env]['time'] += float(time)

for k8s_env in sorted(k8s_env_to_stats.keys()):
    k8s_env_stats = k8s_env_to_stats[k8s_env]
    print(f'{k8s_env}: {k8s_env_stats["tests"]} tests, '
        f'{k8s_env_stats["passed"]} passed, '
        f'{k8s_env_stats["failed"]} failed, '
        f'{k8s_env_stats["skipped"]} skipped '
        f'[{format_duration(k8s_env_stats["time"])}]')
