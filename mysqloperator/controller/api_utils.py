# Copyright (c) 2020, 2021, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

from enum import Enum
import typing
from typing import Any, Generic, Optional, Type, cast

T = typing.TypeVar("T")

E = typing.TypeVar("E")


class ApiSpecError(Exception):
    pass


class ImagePullPolicy(Enum):
    Never = "Never"
    IfNotPresent = "IfNotPresent"
    Always = "Always"


class Edition(Enum):
    community = "community"
    enterprise = "enterprise"


def typename(type: type) -> str:
    CONTENT_TYPE_NAMES = {"dict": "Map", "str": "String",
                          "int": "Integer", "bool": "Boolean", "list": "List"}
    if type.__name__ not in CONTENT_TYPE_NAMES:
        return type.__name__
    return CONTENT_TYPE_NAMES[type.__name__]


def _dget(d: dict, key: str, what: str, default_value: Optional[T], expected_type: Type[T]) -> T:
    if default_value is None and key not in d:
        raise ApiSpecError(f"{what}.{key} is mandatory, but is not set")
    value = d.get(key, default_value)
    if not isinstance(value, expected_type):
        raise ApiSpecError(
            f"{what}.{key} expected to be a {typename(expected_type)} but is {typename(type(value)) if value is not None else 'not set'}")
    return cast(T, value)


def dget_dict(d: dict, key: str, what: str, default_value: Optional[dict] = None) -> dict:
    return _dget(d, key, what, default_value, dict)


def dget_list(d: dict, key: str, what: str, default_value: Optional[list] = None, content_type: Optional[type] = None) -> list:
    l = _dget(d, key, what, default_value, list)
    if l and content_type is not None:
        for i, elem in enumerate(l):
            if not isinstance(elem, content_type):
                raise ApiSpecError(
                    f"{what}.{key}[{i}] expected to be a {typename(content_type)} but is {typename(type(elem))}")
    return l


def dget_str(d: dict, key: str, what: str, *, default_value: Optional[str] = None) -> str:
    return _dget(d, key, what, default_value, str)


def dget_enum(d: dict, key: str, what: str, *, default_value: Optional[E], enum_type: Type[Enum]) -> E:
    s = _dget(d, key, what, default_value, str)
    for v in enum_type:
        if v.name == s:
            return cast(E, v)
    raise ApiSpecError(
        f"{what}.{key} has invalid value '{s}' but must be one of {','.join([x.name for x in enum_type])}")


def dget_int(d: dict, key: str, what: str, *, default_value: Optional[int] = None) -> int:
    return _dget(d, key, what, default_value, int)

def dget_float(d: dict, key: str, what: str, *, default_value: Optional[float] = None) -> int:
    return _dget(d, key, what, default_value, float)

def dget_bool(d: dict, key: str, what: str, *, default_value: Optional[bool] = None) -> bool:
    return _dget(d, key, what, default_value, bool)
