# Copyright (c) 2026, Oracle and/or its affiliates.
#
# Licensed under the Universal Permissive License v 1.0 as shown at https://oss.oracle.com/licenses/upl/
#

import pytest

from mysqloperator.controller.watched_namespaces import WatchedNamespaces


def test_empty_value_means_clusterwide():
    watched = WatchedNamespaces("  , , ")

    assert watched.is_clusterwide() is True
    assert watched.get_namespaces() == []
    assert watched.resolve_operator_namespaces() is None


def test_namespaces_are_trimmed_and_preserve_order():
    watched = WatchedNamespaces(" ns1, ns2 ,,ns3 ")

    assert watched.get_namespaces() == ["ns1", "ns2", "ns3"]
    assert watched.resolve_operator_namespaces() == ["ns1", "ns2", "ns3"]


def test_duplicates_are_removed_while_preserving_first_seen_order():
    watched = WatchedNamespaces(" ns1 , ns2 , ns1 , ns3 , ns2 ")

    assert watched.get_namespaces() == ["ns1", "ns2", "ns3"]
    assert watched.resolve_operator_namespaces() == ["ns1", "ns2", "ns3"]


@pytest.mark.parametrize("raw", ["team-*", "!kube-*", "qa-?"])
def test_validate_rejects_pattern_entries(raw):
    watched = WatchedNamespaces(raw)

    with pytest.raises(
        ValueError,
        match=r"deployment\.namespaces / OPERATOR_NAMESPACES supports explicit namespace names only",
    ):
        watched.validate()


def test_validate_reports_trimmed_invalid_entries_in_order():
    watched = WatchedNamespaces(" team-* , !kube-* , qa-? , team-* ")

    with pytest.raises(
        ValueError,
        match=r"Remove pattern entries: team-\*, !kube-\*, qa-\?",
    ):
        watched.validate()
