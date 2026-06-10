# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import importlib
import sys
import types

import pytest

from mysqloperator.controller.backup.meb.meb_options import (
    BACKUP_EXTRA_OPTIONS,
    RESTORE_EXTRA_OPTIONS,
    MebOptionError,
    validate_extra_options,
)


class _ClientStub(types.SimpleNamespace):
    def __getattr__(self, name):
        value = type(name, (), {})
        setattr(self, name, value)
        return value


@pytest.fixture
def api_modules(monkeypatch):
    for module_name in [
        "mysqloperator.controller.backup.backup_api",
        "mysqloperator.controller.innodbcluster.cluster_api",
        "mysqloperator.controller.kubeutils",
        "kopf",
        "kopf._cogs",
        "kopf._cogs.structs",
        "kopf._cogs.structs.bodies",
    ]:
        sys.modules.pop(module_name, None)

    kopf_stub = types.ModuleType("kopf")
    kopf_bodies_stub = types.ModuleType("kopf._cogs.structs.bodies")
    kopf_stub.TemporaryError = type("TemporaryError", (Exception,), {})
    kopf_stub.PermanentError = type("PermanentError", (Exception,), {})
    kopf_bodies_stub.Body = dict
    monkeypatch.setitem(sys.modules, "kopf", kopf_stub)
    monkeypatch.setitem(sys.modules, "kopf._cogs", types.ModuleType("kopf._cogs"))
    monkeypatch.setitem(
        sys.modules, "kopf._cogs.structs",
        types.ModuleType("kopf._cogs.structs"))
    monkeypatch.setitem(
        sys.modules, "kopf._cogs.structs.bodies", kopf_bodies_stub)

    kubeutils_stub = types.ModuleType("mysqloperator.controller.kubeutils")

    class ApiException(Exception):
        status = None

    kubeutils_stub.ApiException = ApiException
    kubeutils_stub.api_core = types.SimpleNamespace()
    kubeutils_stub.api_apps = types.SimpleNamespace()
    kubeutils_stub.api_customobj = types.SimpleNamespace()
    kubeutils_stub.api_policy = types.SimpleNamespace()
    kubeutils_stub.api_rbac = types.SimpleNamespace()
    kubeutils_stub.api_batch = types.SimpleNamespace()
    kubeutils_stub.api_cron_job = types.SimpleNamespace()
    kubeutils_stub.client = _ClientStub()
    kubeutils_stub.is_ignorable_event_post_error = lambda exc: False
    kubeutils_stub.k8s_cluster_domain = lambda logger: "cluster.local"
    kubeutils_stub.k8s_version = lambda: "1.30"
    monkeypatch.setitem(
        sys.modules, "mysqloperator.controller.kubeutils", kubeutils_stub)

    cluster_api = importlib.import_module(
        "mysqloperator.controller.innodbcluster.cluster_api")
    backup_api = importlib.import_module(
        "mysqloperator.controller.backup.backup_api")
    api_utils = importlib.import_module("mysqloperator.controller.api_utils")
    return types.SimpleNamespace(
        ApiSpecError=api_utils.ApiSpecError,
        MEB=backup_api.MEB,
        MebInitDBSpec=cluster_api.MebInitDBSpec,
    )


@pytest.mark.parametrize("option", [
    "--comments=nightly backup",
    "--compress",
    "--compress-level=9",
    "--compress-method=lz4",
    "--exclude-tables=mysql\\..*",
    "--free-os-buffers",
    "--free-os-buffers=5",
    "--include-tables=app\\..*",
    "--limit-memory=1024",
    "--lock-wait-timeout=120",
    "--no-history-logging",
    "--no-redo-log-archive",
    "--number-of-buffers=16",
    "--only-innodb",
    "--only-known-file-types",
    "--page-reread-count=1000",
    "--page-reread-time=0.5",
    "--process-threads=4",
    "--progress-interval=5",
    "--read-threads=4",
    "--safe-replica-backup-timeout=0",
    "--safe-slave-backup-timeout=0",
    "--show-progress",
    "--show-progress=stderr",
    "--skip-unused-pages",
    "--trace=3",
    "--verbose",
    "--write-threads=4",
])
def test_backup_extra_options_accept_documented_options(option) -> None:
    assert validate_extra_options(
        [option], BACKUP_EXTRA_OPTIONS, "spec.meb.extraOptions") == [option]


@pytest.mark.parametrize("option", [
    "--compress-method=punch-hole",
    "--exclude-tables=mysql\\..*",
    "--free-os-buffers=2",
    "--include-tables=app\\..*",
    "--limit-memory=1024",
    "--number-of-buffers=16",
    "--process-threads=4",
    "--progress-interval=5",
    "--read-threads=4",
    "--show-progress=variable",
    "--trace=1",
    "--uncompress",
    "--verbose",
    "--write-threads=4",
])
def test_restore_extra_options_accept_documented_options(option) -> None:
    assert validate_extra_options(
        [option], RESTORE_EXTRA_OPTIONS, "spec.initDB.meb.extraOptions") == [option]


@pytest.mark.parametrize("option", [
    "--exec-when-locked=id",
    "--exec_when_locked=id",
    "--loose-exec-when-locked=id",
    "--defaults-file=/tmp/my.cnf",
    "--defaults-extra-file=/tmp/my.cnf",
    "--plugin-dir=/tmp/plugins",
    "--sbt-lib-path=/tmp/libobk.so",
    "--backup-dir=/tmp/backup",
    "--backup_dir=/tmp/backup",
    "--backup-image=/tmp/backup.bki",
    "--cloud-service=s3",
    "--cloud-par-url=https://example.invalid/",
    "--datadir=/var/lib/mysql",
    "--incremental",
    "--incremental-base=history:last_backup",
    "--user=root",
    "--host=127.0.0.1",
    "--password=secret",
    "--ssl-ca=/tmp/ca.pem",
    "--skip-binlog",
    "--show-progress=file:/tmp/progress",
    "--comments-file=/tmp/comments.txt",
    "backup-to-image",
    "--",
    "",
])
def test_backup_extra_options_reject_unsafe_options(option) -> None:
    with pytest.raises(MebOptionError):
        validate_extra_options(
            [option], BACKUP_EXTRA_OPTIONS, "spec.meb.extraOptions")


@pytest.mark.parametrize("options", [
    ["--comments", "hello"],
    [" --compress"],
    ["--compress\n"],
    ["--compress-level=10"],
    ["--compress-method=punch-hole"],
])
def test_backup_extra_options_reject_invalid_syntax_or_values(options) -> None:
    with pytest.raises(MebOptionError):
        validate_extra_options(
            options, BACKUP_EXTRA_OPTIONS, "spec.meb.extraOptions")


def test_meb_backup_profile_parse_rejects_exec_when_locked(api_modules) -> None:
    meb = api_modules.MEB()

    with pytest.raises(api_modules.ApiSpecError, match="exec-when-locked"):
        meb.parse({
            "storage": {},
            "extraOptions": ["--exec-when-locked=id"],
        }, "spec.backupProfiles[0].meb")


def test_meb_backup_profile_parse_accepts_valid_extra_options(api_modules) -> None:
    meb = api_modules.MEB()

    meb.parse({
        "storage": {},
        "extraOptions": ["--compress", "--compress-level=1"],
    }, "spec.backupProfiles[0].meb")

    assert meb.extra_options == ["--compress", "--compress-level=1"]


def test_meb_initdb_parse_rejects_exec_when_locked(api_modules) -> None:
    meb = api_modules.MebInitDBSpec()

    with pytest.raises(api_modules.ApiSpecError, match="exec-when-locked"):
        meb.parse({
            "storage": {
                "ociObjectStorage": {
                    "credentials": "restore-par",
                },
            },
            "fullBackup": "full.bki",
            "extraOptions": ["--exec_when_locked=id"],
        }, "spec.initDB.meb")


def test_meb_initdb_parse_accepts_valid_extra_options(api_modules) -> None:
    meb = api_modules.MebInitDBSpec()

    meb.parse({
        "storage": {
            "ociObjectStorage": {
                "credentials": "restore-par",
            },
        },
        "fullBackup": "full.bki",
        "extraOptions": ["--uncompress"],
    }, "spec.initDB.meb")

    assert meb.extra_options == ["--uncompress"]
