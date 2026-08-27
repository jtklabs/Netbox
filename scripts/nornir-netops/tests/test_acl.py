import argparse

import pytest

from netops.core import MODE_ADD, InvalidValue, render, wildcard
from netops.features.acl import (
    FEATURE,
    IOS_SAMPLE,
    parse_acls,
    parse_entry,
    plan_acls,
)
from netops.standards import Standards, StandardsError


def parse_args(argv, document=None):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    args = parser.parse_args(argv)
    args.standards = Standards(path="test", document=document or {})
    return args


DOCUMENT = {
    "snmp": {"allow": ["10.1.1.0/24", "10.2.0.0/16"]},
    "acls": [{"name": "SNMP-POLLERS", "permit": "snmp.allow", "deny_log": True}],
}


# --------------------------------------------------------------------------- #
# entry normalization -- the two platforms write the same rule differently
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("permit 10.1.1.0 0.0.0.255", {"action": "permit", "target": "10.1.1.0/24", "log": False}),
        ("10 permit 10.1.1.0 0.0.0.255", {"action": "permit", "target": "10.1.1.0/24", "log": False}),
        ("permit 10.1.1.0/24", {"action": "permit", "target": "10.1.1.0/24", "log": False}),
        ("permit host 10.1.1.5", {"action": "permit", "target": "10.1.1.5/32", "log": False}),
        ("deny   any log", {"action": "deny", "target": "any", "log": True}),
        ("permit any", {"action": "permit", "target": "any", "log": False}),
    ],
)
def test_entries_normalize_to_one_form(text, expected):
    assert parse_entry(text) == expected


def test_the_two_platforms_spellings_are_equal():
    assert parse_entry("permit 10.1.1.0 0.0.0.255") == parse_entry("permit 10.1.1.0/24")


@pytest.mark.parametrize("text", ["remark this is a comment", "permit", "permit not-an-address"])
def test_junk_entries_are_rejected(text):
    with pytest.raises(InvalidValue):
        parse_entry(text)


@pytest.mark.parametrize(
    "network,expected",
    [
        ("10.1.1.0/24", "10.1.1.0 0.0.0.255"),
        ("10.2.0.0/16", "10.2.0.0 0.0.255.255"),
        ("10.1.1.5/32", "host 10.1.1.5"),
        ("0.0.0.0/0", "0.0.0.0 255.255.255.255"),
    ],
)
def test_the_wildcard_filter(network, expected):
    assert wildcard(network) == expected


# --------------------------------------------------------------------------- #
# parsing and planning
# --------------------------------------------------------------------------- #


def test_parses_each_acl_with_its_entries_in_order():
    entries = {e.key: e for e in parse_acls(IOS_SAMPLE)}
    assert sorted(entries) == ["SNMP-POLLERS", "VTY-ACCESS"]
    assert [e["target"] for e in entries["SNMP-POLLERS"].data["entries"]] == [
        "10.1.1.0/24",
        "any",
    ]


def test_display_summarizes_rather_than_dumping():
    assert parse_acls(IOS_SAMPLE)[0].shown == "ip access-list standard SNMP-POLLERS (2 entries)"


def context(document=None):
    desired = FEATURE.build_desired(parse_args([], document or DOCUMENT))
    return desired, {"platform": "cisco_ios", "variables": desired.variables}


def test_an_acl_that_matches_exactly_is_left_alone():
    desired, ctx = context()
    matching = parse_acls(
        "ip access-list standard SNMP-POLLERS\n"
        " 10 permit 10.1.1.0 0.0.0.255\n"
        " 20 permit 10.2.0.0 0.0.255.255\n"
        " 30 deny any log\n"
    )
    assert plan_acls(matching, desired.keys, MODE_ADD, ctx) == ([], [])


def test_entries_in_the_wrong_order_are_rebuilt():
    """Order is the whole meaning of an ACL, so a reordering is a difference."""
    desired, ctx = context()
    reordered = parse_acls(
        "ip access-list standard SNMP-POLLERS\n"
        " 10 permit 10.2.0.0 0.0.255.255\n"
        " 20 permit 10.1.1.0 0.0.0.255\n"
        " 30 deny any log\n"
    )
    add, remove = plan_acls(reordered, desired.keys, MODE_ADD, ctx)
    assert add == ["SNMP-POLLERS"]
    assert [e.line for e in remove] == ["ip access-list standard SNMP-POLLERS"]


def test_a_missing_entry_is_a_rebuild():
    desired, ctx = context()
    add, remove = plan_acls(parse_acls(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert add == ["SNMP-POLLERS"] and len(remove) == 1


def test_a_missing_acl_is_created_without_a_negation():
    desired, ctx = context()
    add, remove = plan_acls([], desired.keys, MODE_ADD, ctx)
    assert (add, [e.line for e in remove]) == (["SNMP-POLLERS"], [])


def test_an_unmanaged_acl_is_never_touched():
    """Purging every ACL the file does not mention would take out VTY and NAT
    ACLs with it, so --replace deliberately does not offer that."""
    desired, ctx = context()
    from netops.core import MODE_REPLACE

    _, remove = plan_acls(parse_acls(IOS_SAMPLE), desired.keys, MODE_REPLACE, ctx)
    assert all(e.key == "SNMP-POLLERS" for e in remove)


# --------------------------------------------------------------------------- #
# the standards file
# --------------------------------------------------------------------------- #


def test_a_reference_expands_to_the_networks_it_points_at():
    desired = FEATURE.build_desired(parse_args([], DOCUMENT))
    assert [e["target"] for e in desired.variables["acls"]["SNMP-POLLERS"]["entries"]] == [
        "10.1.1.0/24",
        "10.2.0.0/16",
        "any",
    ]


def test_explicit_entries_are_taken_in_order():
    document = {"acls": [{"name": "X", "entries": ["deny host 10.1.1.9", "permit any"]}]}
    desired = FEATURE.build_desired(parse_args([], document))
    assert [e["action"] for e in desired.variables["acls"]["X"]["entries"]] == ["deny", "permit"]


def test_an_extended_acl_is_refused_rather_than_guessed_at():
    document = {"acls": [{"name": "X", "type": "extended", "permit": ["10.1.1.0/24"]}]}
    with pytest.raises(StandardsError, match="not supported yet"):
        FEATURE.build_desired(parse_args([], document))


def test_an_acl_with_no_entries_is_rejected():
    with pytest.raises(StandardsError, match="no entries"):
        FEATURE.build_desired(parse_args([], {"acls": [{"name": "X"}]}))


def test_selecting_one_acl_by_name():
    document = {"acls": [{"name": "A", "permit": ["10.1.1.0/24"]},
                         {"name": "B", "permit": ["10.2.0.0/16"]}]}
    assert FEATURE.build_desired(parse_args(["-a", "B"], document)).keys == ["B"]


def test_selecting_an_acl_that_is_not_defined():
    with pytest.raises(ValueError, match="no ACL named NOPE"):
        FEATURE.build_desired(parse_args(["-a", "NOPE"], DOCUMENT))


def test_no_acls_defined_is_an_error():
    with pytest.raises(ValueError, match="no ACLs defined"):
        FEATURE.build_desired(parse_args([], {}))


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def test_renders_ios_with_wildcard_masks_and_the_negation_first():
    desired, _ = context()
    from netops.core import Entry

    commands = render(
        "acl",
        "cisco_ios",
        ["SNMP-POLLERS"],
        [Entry("SNMP-POLLERS", "ip access-list standard SNMP-POLLERS")],
        desired.variables,
    )
    assert commands == [
        "no ip access-list standard SNMP-POLLERS",
        "ip access-list standard SNMP-POLLERS",
        " permit 10.1.1.0 0.0.0.255",
        " permit 10.2.0.0 0.0.255.255",
        " deny any log",
    ]


def test_renders_eos_with_prefix_lengths():
    desired, _ = context()
    assert render("acl", "arista_eos", ["SNMP-POLLERS"], [], desired.variables) == [
        "ip access-list standard SNMP-POLLERS",
        " permit 10.1.1.0/24",
        " permit 10.2.0.0/16",
        " deny any log",
    ]


def test_eos_entries_round_trip_through_the_parser():
    """What we would send must parse back to what we wanted, or the next run
    would see a difference that is not there."""
    desired, _ = context()
    commands = render("acl", "arista_eos", ["SNMP-POLLERS"], [], desired.variables)
    reparsed = parse_acls("\n".join(commands))[0].data["entries"]
    assert reparsed == desired.variables["acls"]["SNMP-POLLERS"]["entries"]


def test_ios_entries_round_trip_through_the_parser():
    desired, _ = context()
    commands = render("acl", "cisco_ios", ["SNMP-POLLERS"], [], desired.variables)
    reparsed = parse_acls("\n".join(commands))[0].data["entries"]
    assert reparsed == desired.variables["acls"]["SNMP-POLLERS"]["entries"]
