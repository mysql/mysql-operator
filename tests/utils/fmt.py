# Copyright (c) 2020, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#


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
