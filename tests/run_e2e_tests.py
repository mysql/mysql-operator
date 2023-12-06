#!/usr/bin/python3
# Copyright (c) 2020, 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from typing import Tuple
from setup import config
from setup.config import g_ts_cfg
from utils.ote import get_driver
from utils.ote.base import BaseEnvironment
from utils import kutil, ociutil
from utils import tutil
import unittest
import os
import sys
import logging
import io
import base64
from utils import testsuite


def setup_k8s():
    from kubernetes import config

    try:
        # outside k8s
        config.load_kube_config(context=g_ts_cfg.k8s_context)
    except config.config_exception.ConfigException:
        try:
            # inside a k8s pod
            config.load_incluster_config()
        except config.config_exception.ConfigException:
            raise Exception(
                "Could not configure kubernetes python client")


def list_tests(suites):
    for suite in suites:
        for test in suite:
            print(f"    {test.id()}")


def setup_logging(verbose: bool):
    gray = ""
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        stream=sys.stdout,
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

    tutil.g_test_data_dir = os.path.join(basedir, "data")

    DEFAULT_OPERATOR_DEBUG_LEVEL = 3

    opt_include = []
    opt_exclude = []
    opt_suite_path = None
    opt_verbose = False
    opt_debug = False
    opt_verbosity = 2
    opt_nodes = None
    opt_node_memory = None
    opt_kube_version = None
    opt_setup = True
    opt_load_images = False
    opt_deploy = True
    opt_mount_operator_path = None
    opt_mounts = []
    opt_custom_dns = None
    opt_cleanup = True
    opt_cfg_path = None
    opt_ip_family = None
    opt_xml_report_path = None

    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    allowed_commands = ["run", "list", "setup", "test", "clean"]
    if cmd not in allowed_commands:
        print(
            f"Unknown command '{cmd}': must be one of '{','.join(allowed_commands)}'")
        sys.exit(1)

    for arg in sys.argv[2:]:
        if arg.startswith("--env="):
            g_ts_cfg.env = arg.partition("=")[-1]
        elif arg.startswith("--env-binary-path="):
            g_ts_cfg.env_binary_path = arg.partition("=")[-1]
        elif arg.startswith("--kube-version="):
            opt_kube_version = arg.split("=")[-1]
        elif arg.startswith("--kubectl-path="):
            g_ts_cfg.kubectl_path = arg.partition("=")[-1]
        elif arg.startswith("--nodes="):
            opt_nodes = int(arg.split("=")[-1])
        elif arg.startswith("--node-memory="):
            opt_node_memory = int(arg.split("=")[-1])
        elif arg.startswith("--ip-family="):
            opt_ip_family = arg.partition("=")[-1]
        elif arg.startswith("--cluster="):
            g_ts_cfg.k8s_cluster = arg.partition("=")[-1]
        elif arg.startswith("--cluster-domain-alias="):
            g_ts_cfg.k8s_cluster_domain_alias = arg.partition("=")[-1]
        elif arg == "--use-current-context":
            g_ts_cfg.k8s_context = kutil.get_current_context()
            g_ts_cfg.k8s_cluster = g_ts_cfg.k8s_context
            opt_setup = False
        elif arg == "--verbose" or arg == "-v":
            opt_verbose = True
        elif arg == "-vv":
            opt_verbose = True
            tutil.debug_adminapi_sql = 1
        elif arg == "-vvv":
            opt_verbose = True
            tutil.debug_adminapi_sql = 2
        elif arg == "--debug" or arg == "-d":
            opt_debug = True
        elif arg == "--trace" or arg == "-t":
            tutil.tracer.enabled = True
        elif arg in ("--nosetup", "--no-setup"):
            opt_setup = False
            opt_cleanup = False
        elif arg in ("--noclean", "--no-clean"):
            opt_cleanup = False
        elif arg == "--load":
            opt_load_images = True
        elif arg in ("--nodeploy", "--no-deploy"):
            opt_deploy = False
        elif arg == "--dkube":
            kutil.debug_kubectl = True
        elif arg == "--doperator":
            BaseEnvironment.opt_operator_debug_level = DEFAULT_OPERATOR_DEBUG_LEVEL
        elif arg.startswith("--doperator="):
            BaseEnvironment.opt_operator_debug_level = int(arg.split("=")[-1])
        elif arg == "--doci":
            ociutil.debug_ocicli = True
        elif arg == "--mount-operator" or arg == "-O":
            opt_mount_operator_path = os.path.join(os.path.dirname(basedir), "mysqloperator")
        elif arg.startswith("--mount="):
            opt_mounts += [arg.partition("=")[-1]]
        elif arg.startswith("--custom-dns="):
            opt_custom_dns = arg.partition("=")[-1]
        elif arg.startswith("--registry="):
            g_ts_cfg.image_registry = arg.partition("=")[-1]
        elif arg.startswith("--cfg-path="):
            opt_cfg_path = arg.partition("=")[-1]
        elif arg.startswith("--repository="):
            g_ts_cfg.image_repository = arg.partition("=")[-1]
        elif arg.startswith("--operator-tag="):
            g_ts_cfg.operator_version_tag = arg.partition("=")[-1]
        elif arg.startswith("--old-operator-tag="):
            g_ts_cfg.operator_old_version_tag = arg.partition("=")[-1]
        elif arg.startswith("--operator-pull-policy="):
            g_ts_cfg.operator_pull_policy = arg.partition("=")[-1]
        elif arg == "--skip-enterprise":
            g_ts_cfg.enterprise_skip = True
        elif arg == "--skip-audit-log":
            g_ts_cfg.audit_log_skip = True
        elif arg == "--skip-oci":
            g_ts_cfg.oci_skip = True
        elif arg.startswith("--oci-config="):
            g_ts_cfg.oci_config_path = arg.partition("=")[-1]
        elif arg.startswith("--oci-bucket="):
            g_ts_cfg.oci_bucket_name = arg.partition("=")[-1]
        elif arg == "--skip-azure":
            g_ts_cfg.azure_skip = True
        elif arg == "--start-azure":
            g_ts_cfg.start_azure = True
        elif arg.startswith("--azure-config="):
            g_ts_cfg.azure_config_file = arg.partition("=")[-1]
        elif arg.startswith("--azure-container="):
            g_ts_cfg.azure_container_name = arg.partition("=")[-1]
        elif arg.startswith("--vault-cfg="):
            g_ts_cfg.vault_cfg_path=arg.partition("=")[-1]
        elif arg.startswith("--custom-secret="):
            g_ts_cfg.set_custom_secret(arg.partition("=")[-1])
        elif arg.startswith("--suite="):
            opt_suite_path = arg.partition("=")[-1]
        elif arg.startswith("--xml="):
            opt_xml_report_path = arg.partition("=")[-1]
        elif arg.startswith("--work-dir=") or arg.startswith("--workdir="):
            g_ts_cfg.work_dir = arg.split("=")[-1]
        elif arg == "--store-operator-log":
            g_ts_cfg.store_operator_log = True
        elif arg.startswith("--test-ns-label"):
            label_kv = (arg.split("=", 1)[1]).split("=")
            g_ts_cfg.custom_test_ns_labels[label_kv[0]] = label_kv[1]
        elif arg.startswith("--operator-ns-label"):
            label_kv = (arg.split("=", 1)[1]).split("=")
            g_ts_cfg.custom_operator_ns_labels[label_kv[0]] = label_kv[1]
        elif arg.startswith("--sts-label"):
            label_kv = (arg.split("=", 1)[1]).split("=")
            g_ts_cfg.custom_sts_labels[label_kv[0]] = label_kv[1]
        elif arg.startswith("--sts-podspec"):
            g_ts_cfg.custom_sts_podspec = arg.partition("=")[-1]
            try:
                g_ts_cfg.custom_sts_podspec = base64.b64decode(arg.partition("=")[-1]).decode("utf8")
            except:
                pass
        elif arg.startswith("--ic-server-version"):
            g_ts_cfg.custom_ic_server_version = arg.partition("=")[-1]
        elif arg.startswith("--ic-server-version-override"):
            g_ts_cfg.custom_ic_server_version = arg.partition("=")[-1]
            g_ts_cfg.custom_ic_server_version_override = arg.partition("=")[-1]
        elif arg.startswith("--ic-router-version"):
            g_ts_cfg.custom_ic_router_version = arg.partition("=")[-1]
        elif arg.startswith("--router-extra-containers-per-pod"):
            g_ts_cfg.router_extra_containers_per_pod = int(arg.partition("=")[-1])
        elif arg.startswith("--local-path-provisioner"):
            g_ts_cfg.local_path_provisioner_install = arg.partition("=")[-1].lower() in ['true', '1', 'on']
        elif arg.startswith("--local-path-provisioner-shared-path"):
            g_ts_cfg.local_path_provisioner_shared_path = arg.partition("=")[-1]
        elif arg.startswith("--local-path-provisioner-manifest-url"):
            g_ts_cfg.local_path_provisioner_manifest_url = arg.partition("=")[-1]
        elif arg.startswith("-"):
            print(f"Invalid option {arg}")
            sys.exit(1)
        else:
            inc, exc = parse_filter(arg)
            opt_include += inc
            opt_exclude += exc

    g_ts_cfg.commit()

    if g_ts_cfg.store_operator_log:
        tutil.g_store_log_operator = tutil.StoreOperatorLog()

    if opt_suite_path:
        with open(opt_suite_path, 'r') as f:
            opt_include += f.read().splitlines()
    print(f"opt_include: {opt_include}")
    print(g_ts_cfg)

    image_dir = os.getenv("DOCKER_IMAGE_DIR") or "/tmp/docker-images"
    images = ["mysql-server:8.0.25", "mysql-router:8.0.25",
              "mysql-server:8.0.24", "mysql-router:8.0.24",
              "mysql-operator:8.0.25-2.0.1", "mysql-operator-commercial:8.0.25-2.0.1"]

    suites = testsuite.load_test_suite(basedir, opt_include, opt_exclude)
    if not suites or suites.countTestCases() == 0:
        print("No tests matched")
        sys.exit(0)

    if cmd == "list":
        print("Listing tests and exiting...")
        list_tests(suites)
        sys.exit(0)

    setup_logging(opt_verbose)

    print(
        f"Using environment {g_ts_cfg.env} with kubernetes version {opt_kube_version or 'latest'}...")

    deploy_dir = os.path.join(basedir, "../deploy")
    deploy_files = [os.path.join(deploy_dir, f) for f in deploy_files]

    if opt_mount_operator_path:
        print(f"Overriding mysqloperator code with local copy at {opt_mount_operator_path}")

    assert len(deploy_files) == len(
        [f for f in deploy_files if os.path.isfile(f)]), "deploy files check"

    with get_driver(g_ts_cfg.env) as driver:
        if cmd in ("run", "setup"):
            if opt_mount_operator_path:
                driver.mount_operator_path(opt_mount_operator_path)

            driver.setup_cluster(
                nodes=opt_nodes, node_memory=opt_node_memory, version=opt_kube_version, cfg_path=opt_cfg_path,
                perform_setup=opt_setup, mounts=opt_mounts, custom_dns=opt_custom_dns, cleanup=opt_cleanup, ip_family=opt_ip_family)

            if opt_load_images:
                driver.cache_images(image_dir, images)

            if opt_deploy:
                driver.setup_operator(deploy_files)

        if cmd in ("run", "test"):
            setup_k8s()

            tutil.g_full_log.set_target(open("/tmp/operator_log.txt", "w+"))
            # tutil.g_full_log.watch_operator_pod("mysql-operator", "testpod")

            tutil.tracer.basedir = basedir
            tutil.tracer.install()

            try:
                if opt_debug:
                    suites.debug()
                else:
                    if (opt_xml_report_path):
                        import xmlrunner
                        from xmlrunner.extra.xunit_plugin import transform
                        xml_report_output = io.BytesIO()
                        runner = xmlrunner.XMLTestRunner(stream=sys.stdout,output=xml_report_output)
                        runner.run(suites)
                        with open(opt_xml_report_path, 'wb') as xml_report:
                           xml_report.write(transform(xml_report_output.getvalue()))
                    else:
                        runnerClass = unittest.TextTestRunner
                        if sys.stdout.isatty():
                            try:
                                from colour_runner.runner import ColourTextTestRunner
                                runnerClass = ColourTextTestRunner
                            except ImportError:
                                pass
                        runner = runnerClass(stream=sys.stdout,verbosity=opt_verbosity)
                        runner.run(suites)
            except:
                tutil.g_full_log.shutdown()
                raise
