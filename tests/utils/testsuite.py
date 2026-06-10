# Copyright (c) 2023, 2026 Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# test suite utils - get list of tests, divide suite into portions to run on many instances

import importlib
import os
import unittest
from unittest.util import strclass


MULTI_NODE_CLUSTER_ATTR = "_ote_requires_multi_node_cluster"


def requires_multi_node_cluster(cls):
    setattr(cls, MULTI_NODE_CLUSTER_ATTR, True)
    return cls


def get_test_class(test_class_name: str):
    parts = test_class_name.split(".")
    import_error = None

    for module_part_count in range(len(parts) - 1, 0, -1):
        module_name = ".".join(parts[:module_part_count])
        try:
            module = importlib.import_module(module_name)
        except ImportError as err:
            import_error = err
            continue

        obj = module
        try:
            for part in parts[module_part_count:]:
                obj = getattr(obj, part)
        except AttributeError:
            continue
        return obj

    raise ImportError(f"Cannot import test class {test_class_name}") from import_error


def is_multi_node_cluster_test(test_class_name: str) -> bool:
    return bool(getattr(get_test_class(test_class_name), MULTI_NODE_CLUSTER_ATTR, False))


def iter_test_cases(suite: unittest.TestSuite):
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from iter_test_cases(test)
        else:
            yield test


def suite_requires_multi_node_cluster(suite: unittest.TestSuite) -> bool:
    for test in iter_test_cases(suite):
        if getattr(test.__class__, MULTI_NODE_CLUSTER_ATTR, False):
            return True
    return False

def load_test_suite(basedir: str, include: list, exclude: list,
                    silent: bool = True, runner: int = 0, runners: int = 0):
    loader = unittest.TestLoader()

    tests = loader.discover("e2e", pattern="*_t.py", top_level_dir=basedir)
    if loader.errors:
        print("Errors found loading tests:")
        for err in loader.errors:
            print(err)
        raise Exception("Could not load test suite")

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

    all_i = 0
    for ts in tests:
        for test in ts:
            for case in test:
                name = strclass(case.__class__)
                if ((not include or match_any(name, include)) and
                        (not exclude or not match_any(name, exclude))):
                    all_i += 1
                    if runners > 0 and runner > 0:
                        if (all_i - 1) % runners != (runner - 1):
                            if not silent:
                                print(f"Skipping test #{all_i:2} {name}")
                            break
                    if not silent:
                        print(f"Adding test #{all_i:2} {name}")
                    suite.addTest(test)
                else:
                    print(f"Skipping {name}")
                break

    if suite.countTestCases() > 0:
        return suite

    return None

def prepare_test_suite(base_dir, pattern_include: list=[], pattern_exclude: list=[], sort_cases=False):
    suites = load_test_suite(base_dir, pattern_include, pattern_exclude)
    if not suites or suites.countTestCases() == 0:
        return None

    testset = set()
    for suite in suites:
        for subtest in suite:
            testset.add(strclass(subtest.__class__))

    test_suite = list(testset)
    if sort_cases:
        test_suite.sort()
    return test_suite

def divide_test_suite(test_suite, portion_count):
    tests_count = len(test_suite)
    tests_per_worker, tests_remainder = divmod(tests_count, portion_count)

    worker_index = 0
    test_index = 0
    portions = []
    while worker_index < portion_count and test_index < tests_count:
        begin = test_index
        end = begin + tests_per_worker
        if worker_index < tests_remainder:
            end += 1
        portion = test_suite[begin:end]
        portions.append(portion)
        worker_index += 1
        test_index = end

    return portions


def generate_test_suite_subsets(test_suite_base_dir, subset_count, output_dir, subset_file_prefix):
    test_suite = prepare_test_suite(test_suite_base_dir)
    if not test_suite or len(test_suite) == 0:
        return -1

    portions = divide_test_suite(test_suite, subset_count)
    subset_index = 0
    for portion in portions:
        subset_path = os.path.join(output_dir, f"{subset_file_prefix}-{subset_index:02}.txt")
        with open(subset_path, 'w') as f:
            for test_case in portion:
                f.write(f"{test_case}\n")
        subset_index += 1

    return subset_index
