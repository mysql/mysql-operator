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


def red(*args):
    s = " ".join(args)
    return "\033[0;31m%s\033[0m" % s

def lred(*args):
    s = " ".join(args)
    return "\033[1;31m%s\033[0m" % s

def green(*args):
    s = " ".join(args)
    return "\033[1;32m%s\033[0m" % s

def yellow(*args):
    s = " ".join(args)
    return "\033[0;33m%s\033[0m" % s

def lyellow(*args):
    s = " ".join(args)
    return "\033[1;33m%s\033[0m" % s

def dyellow(*args):
    s = " ".join(args)
    return "\033[2;33m%s\033[0m" % s

def blue(*args):
    s = " ".join(args)
    return "\033[0;34m%s\033[0m" % s

def dblue(*args):
    s = " ".join(args)
    return "\033[2;34m%s\033[0m" % s

def cyan(*args):
    s = " ".join(args)
    return "\033[0;36m%s\033[0m" % s

def lcyan(*args):
    s = " ".join(args)
    return "\033[1;36m%s\033[0m" % s

def purple(*args):
    s = " ".join(args)
    return "\033[0;35m%s\033[0m" % s

def bold(*args):
    s = " ".join(args)
    return "\033[1m%s\033[0m" % s

def dgray(*args):
    s = " ".join(args)
    return "\033[2;37m%s\033[0m" % s
