# Copyright (c) 2020, Oracle and/or its affiliates.
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



class ApiSpecError(Exception):
    pass

def typename(type):
    CONTENT_TYPE_NAMES = {"dict": "Map", "str": "String", "int": "Integer", "bool": "Boolean", "list": "List"}
    if type.__name__ not in CONTENT_TYPE_NAMES:
        return type.__name__
    return CONTENT_TYPE_NAMES[type.__name__]


def _dget(d, key, what, default_value, expected_type):
    if default_value is None and key not in d:
        raise ApiSpecError(f"{what}.{key} is mandatory, but is not set")
    value = d.get(key, default_value)
    if not isinstance(value, expected_type):
        raise ApiSpecError(f"{what}.{key} expected to be a {typename(expected_type)} but is {typename(type(value)) if value is not None else 'not set'}")
    return value


def dget_dict(d, key, what, default_value=None):
    return _dget(d, key, what, default_value, dict)


def dget_list(d, key, what, default_value=None, content_type=None):
    l = _dget(d, key, what, default_value, list)
    if l and content_type is not None:
        for i, elem in enumerate(l):
            if not isinstance(elem, content_type):
                raise ApiSpecError(f"{what}.{key}[{i}] expected to be a {typename(content_type)} but is {typename(type(elem))}")
    return l


def dget_str(d, key, what, default_value=None):
    return _dget(d, key, what, default_value, str)


def dget_int(d, key, what, default_value=None):
    return _dget(d, key, what, default_value, int)


def dget_bool(d, key, what, default_value=None):
    return _dget(d, key, what, default_value, bool)
