# Copyright (c) 2023, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

# test suite utils - get list of tests, divide suite into portions to run on many instances

import os
import unittest
from unittest.util import strclass

def load_test_suite(basedir: str, include: list, exclude: list):
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
