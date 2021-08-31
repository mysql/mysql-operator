#!/usr/bin/env mysqlsh --py -f
# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import Tuple
from utils.ote import get_driver
from utils.ote.base import BaseEnvironment
from utils import kutil
from utils import tutil
import unittest
import os
import sys
import logging


def setup_k8s():
    from kubernetes import config

    try:
        # outside k8s
        config.load_kube_config()
    except config.config_exception.ConfigException:
        try:
            # inside a k8s pod
            config.load_incluster_config()
        except config.config_exception.ConfigException:
            raise Exception(
                "Could not configure kubernetes python client")


def load_test_suite(basedir: str, include: list, exclude: list):
    loader = unittest.TestLoader()

    tests = loader.discover("e2e", pattern="*_t.py", top_level_dir=basedir)
    if loader.errors:
        print("Errors found loading tests:")
        for err in loader.errors:
            print(err)
        sys.exit(1)

    suite = unittest.TestSuite()

    def strclass(cls):
        return "%s.%s" % (cls.__module__, cls.__qualname__)

    def match_any(name, patterns):
        import re
        for p in patterns:
            p = p.replace("*", ".*")
            if re.match(f"^{p}$", name):
                return True
        return False

    for ts in tests:
        for test in ts:
            for case in test:
                name = strclass(case.__class__)
                if ((not include or match_any(name, include)) and
                        (not exclude or not match_any(name, exclude))):
                    suite.addTest(test)
                else:
                    print("skipping", name)
                break

    if suite.countTestCases() > 0:
        return suite


def list_tests(suites):
    for suite in suites:
        for test in suite:
            print(f"    {test.id()}")


def setup_logging(verbose: bool):
    gray = ""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format="\033[1;34m%(asctime)s  %(name)-10s  [%(levelname)-8s]\033[0m   %(message)s")


def parse_filter(f: str) -> Tuple[list, list]:
    """
    Parse gtest style test filter:
        include1:include2:-exclude1:exclude2
    """
    inc = []
    exc = []
    l = inc
    for s in f.split(":"):
        if s.startswith("-"):
            l = exc
            s = s[1:]
        l.append(s)
    return inc, exc


if __name__ == '__main__':
    deploy_files = ["deploy-crds.yaml", "deploy-operator.yaml"]

    basedir: str = os.path.dirname(os.path.abspath(__file__))
    os.chdir(basedir)

    tutil.g_test_data_dir = "../unittest/data"

    opt_include = []
    opt_exclude = []
    opt_verbose = False
    debug = False
    no_cleanup = False
    verbosity = 2
    opt_nodes = 1
    opt_kube_version = None
    opt_setup = True
    opt_load_images = True
    opt_deploy = True
    env_name = "minikube"
    registry = "local"  # local | <registry-name>

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    allowed_commands = ["run", "list", "setup", "test", "clean"]
    if cmd not in allowed_commands:
        print(
            f"Unknown command '{cmd}': must be one of '{','.join(allowed_commands)}'")
        sys.exit(1)

    for arg in sys.argv[2:]:
        if arg.startswith("--env="):
            env_name = arg.partition("=")[-1]
        elif arg.startswith("--registry="):
            registry = arg.partition("=")[-1]
        elif arg == "--kube-version=":
            opt_kube_version = arg.split("=")[-1]
        elif arg.startswith("--nodes="):
            opt_nodes = int(arg.split("=")[-1])
        elif arg == "--verbose" or arg == "-v":
            opt_verbose = True
        elif arg == "-vv":
            opt_verbose = True
            tutil.debug_adminapi_sql = 1
        elif arg == "-vvv":
            opt_verbose = True
            tutil.debug_adminapi_sql = 2
        elif arg == "--debug" or arg == "-d":
            debug = True
        elif arg == "--trace" or arg == "-t":
            tutil.tracer.enabled = True
        elif arg == "--noclean":
            no_cleanup = True
        elif arg == "--nosetup":
            opt_setup = False
        elif arg == "--noload":
            opt_load_images = False
        elif arg == "--nodeploy":
            opt_deploy = False
        elif arg == "--dkube":
            kutil.debug_kubectl = True
        elif arg == "--doperator":
            BaseEnvironment.opt_operator_debug_level = 3
        elif arg.startswith("-"):
            print(f"Invalid option {arg}")
            sys.exit(1)
        else:
            inc, exc = parse_filter(arg)
            opt_include += inc
            opt_exclude += exc

    image_dir = os.getenv("DOCKER_IMAGE_DIR") or "/tmp/docker-images"
    images = ["mysql-server:8.0.25", "mysql-router:8.0.25",
              "mysql-server:8.0.24", "mysql-router:8.0.24",
              "mysql-server:8.0.23", "mysql-router:8.0.23",
              "mysql-operator:8.0.25-2.0.1", "mysql-operator-commercial:8.0.25-2.0.1",
              "mysql-shell:8.0.25-2.0.2", "mysql-shell-commercial:8.0.25-2.0.2"]

    suites = load_test_suite(basedir, opt_include, opt_exclude)
    if not suites or suites.countTestCases() == 0:
        print("No tests matched")
        sys.exit(0)

    if cmd == "list":
        print("Listing tests and exiting...")
        list_tests(suites)
        sys.exit(0)

    setup_logging(opt_verbose)

    print(
        f"Using environment {env_name} with kubernetes version {opt_kube_version or 'latest'}...")

    deploy_dir = os.path.join(basedir, "./deploy")
    deploy_files = [os.path.join(deploy_dir, f) for f in deploy_files]
    assert len(deploy_files) == len(
        [f for f in deploy_files if os.path.isfile(f)]), "deploy files check"

    with get_driver(env_name) as driver:
        if cmd in ("run", "setup"):
            driver.setup_cluster(
                nodes=opt_nodes, version=opt_kube_version, perform_setup=opt_setup, skip_cleanup=no_cleanup)

            if opt_load_images and registry == "local":
                driver.cache_images(image_dir, images)

            if opt_deploy:
                driver.setup_operator(registry, deploy_files)

        if cmd in ("run", "test"):
            setup_k8s()

            tutil.g_full_log.set_target(open("/tmp/operator_log.txt", "w+"))
            # tutil.g_full_log.watch_operator_pod("mysql-operator", "testpod")

            tutil.tracer.basedir = basedir
            tutil.tracer.install()

            try:
                if debug:
                    suites.debug()
                else:
                    runner = unittest.TextTestRunner(verbosity=verbosity)
                    runner.run(suites)
            except:
                tutil.g_full_log.shutdown()
                raise
