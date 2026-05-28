# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/

import re
from typing import Optional

BACKUP_EXTRA_OPTIONS = "backup"
RESTORE_EXTRA_OPTIONS = "restore"

_OPTION_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_NUMBER_RE = re.compile(r"^[0-9]+(\.[0-9]+)?$")


class MebOptionError(ValueError):
    pass


def validate_extra_options(options: list, mode: str, prefix: str) -> list[str]:
    if not isinstance(options, list):
        raise MebOptionError(f"{prefix} must be a list")

    if mode not in _OPTION_SPECS:
        raise MebOptionError(f"unsupported MEB extraOptions mode: {mode}")

    specs = _OPTION_SPECS[mode]
    for i, option in enumerate(options):
        where = f"{prefix}[{i}]"
        name, value = _parse_option(option, where)
        validate = specs.get(name)
        if not validate:
            raise MebOptionError(
                f"{where}: mysqlbackup option '--{name}' is not allowed")
        validate(name, value, where)

    return options


def _parse_option(option, where: str) -> tuple[str, Optional[str]]:
    if not isinstance(option, str):
        raise MebOptionError(f"{where} must be a string")
    if not option:
        raise MebOptionError(f"{where} must not be empty")
    if option != option.strip():
        raise MebOptionError(f"{where} must not contain leading or trailing whitespace")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in option):
        raise MebOptionError(f"{where} must not contain control characters")
    if option == "--":
        raise MebOptionError(f"{where}: option terminator is not allowed")
    if not option.startswith("--"):
        raise MebOptionError(f"{where}: mysqlbackup commands and split option values are not allowed")

    token = option[2:]
    if "=" in token:
        name, value = token.split("=", 1)
        if value == "":
            raise MebOptionError(f"{where}: option value must not be empty")
    else:
        name = token
        value = None

    name = name.lower().replace("_", "-")
    if name.startswith("loose-"):
        raise MebOptionError(f"{where}: loose mysqlbackup options are not allowed")
    if not _OPTION_NAME_RE.match(name):
        raise MebOptionError(f"{where}: invalid mysqlbackup option name")

    return name, value


def _flag(name: str, value: Optional[str], where: str) -> None:
    if value is not None:
        raise MebOptionError(f"{where}: '--{name}' does not take a value")


def _text(max_len: int):
    def validate(name: str, value: Optional[str], where: str) -> None:
        if value is None:
            raise MebOptionError(f"{where}: '--{name}' requires a value")
        if len(value) > max_len:
            raise MebOptionError(f"{where}: '--{name}' value is too long")

    return validate


def _integer(min_value: int, max_value: int):
    def validate(name: str, value: Optional[str], where: str) -> None:
        if value is None:
            raise MebOptionError(f"{where}: '--{name}' requires a value")
        if not value.isdigit():
            raise MebOptionError(f"{where}: '--{name}' requires an integer value")
        parsed = int(value)
        if parsed < min_value or parsed > max_value:
            raise MebOptionError(
                f"{where}: '--{name}' must be between {min_value} and {max_value}")

    return validate


def _number(min_value: float, max_value: float):
    def validate(name: str, value: Optional[str], where: str) -> None:
        if value is None:
            raise MebOptionError(f"{where}: '--{name}' requires a value")
        if not _NUMBER_RE.match(value):
            raise MebOptionError(f"{where}: '--{name}' requires a numeric value")
        parsed = float(value)
        if parsed < min_value or parsed > max_value:
            raise MebOptionError(
                f"{where}: '--{name}' must be between {min_value:g} and {max_value:g}")

    return validate


def _optional_integer(min_value: int, max_value: int):
    integer = _integer(min_value, max_value)

    def validate(name: str, value: Optional[str], where: str) -> None:
        if value is not None:
            integer(name, value, where)

    return validate


def _enum(*values: str):
    def validate(name: str, value: Optional[str], where: str) -> None:
        if value is None:
            raise MebOptionError(f"{where}: '--{name}' requires a value")
        if value not in values:
            raise MebOptionError(
                f"{where}: '--{name}' must be one of {', '.join(values)}")

    return validate


def _show_progress(name: str, value: Optional[str], where: str) -> None:
    if value is not None and value not in ("stderr", "stdout", "table", "variable"):
        raise MebOptionError(
            f"{where}: '--{name}' must be stderr, stdout, table, or variable")


_COMMON_OPTIONS = {
    "exclude-tables": _text(512),
    "free-os-buffers": _optional_integer(0, 5),
    "include-tables": _text(512),
    "limit-memory": _integer(1, 1048576),
    "number-of-buffers": _integer(1, 4096),
    "process-threads": _integer(1, 1024),
    "progress-interval": _integer(1, 86400),
    "read-threads": _integer(1, 1024),
    "show-progress": _show_progress,
    "trace": _integer(0, 3),
    "verbose": _flag,
    "write-threads": _integer(1, 1024),
}

_BACKUP_OPTIONS = {
    **_COMMON_OPTIONS,
    "comments": _text(1024),
    "compress": _flag,
    "compress-level": _integer(0, 9),
    "compress-method": _enum("zlib", "lz4", "lzma"),
    "lock-wait-timeout": _integer(0, 86400),
    "no-history-logging": _flag,
    "no-redo-log-archive": _flag,
    "only-innodb": _flag,
    "only-known-file-types": _flag,
    "page-reread-count": _integer(0, 1000000),
    "page-reread-time": _number(0, 3600000),
    "safe-replica-backup-timeout": _integer(0, 86400),
    "safe-slave-backup-timeout": _integer(0, 86400),
    "skip-unused-pages": _flag,
}

_RESTORE_OPTIONS = {
    **_COMMON_OPTIONS,
    "compress-method": _enum("punch-hole"),
    "uncompress": _flag,
}

_OPTION_SPECS = {
    BACKUP_EXTRA_OPTIONS: _BACKUP_OPTIONS,
    RESTORE_EXTRA_OPTIONS: _RESTORE_OPTIONS,
}
