import argparse

import pytest

from netops.core import MODE_ADD, MODE_REPLACE, render
from netops.features.syslog import (
    EOS_SAMPLE,
    FEATURE,
    IOS_SAMPLE,
    parse_logging,
    plan_syslog,
)
from netops.standards import Standards


def parse_args(argv, document=None):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    args = parser.parse_args(argv)
    args.standards = Standards(path="test", document=document or {})
    return args


def test_parses_the_three_kinds_of_line():
    assert [e.key for e in parse_logging(IOS_SAMPLE)] == [
        "trap:notifications",
        "source:Loopback0",
        "host:10.1.1.50:514",
        "host:10.9.9.9:1514",
        "origin:string OLD-NAME",
    ]


def test_ignores_logging_lines_it_does_not_manage():
    """`logging buffered` is never parsed, so --replace can never remove it."""
    assert not any("buffered" in e.line for e in parse_logging(IOS_SAMPLE))


def test_eos_positional_port():
    entries = {e.key: e for e in parse_logging(EOS_SAMPLE)}
    assert "host:10.9.9.9:1514" in entries


def test_the_key_carries_the_value_so_a_change_is_visible():
    """`trap:notifications` and `trap:informational` are different keys, which
    is what makes a severity change show up as a change."""
    current = parse_logging(IOS_SAMPLE)
    add, _ = plan_syslog(current, ["trap:informational"], MODE_ADD)
    assert add == ["trap:informational"]


def test_matching_severity_is_no_change():
    add, remove = plan_syslog(parse_logging(EOS_SAMPLE), ["trap:informational"], MODE_ADD)
    assert (add, remove) == ([], [])


def test_replace_removes_an_unwanted_collector():
    current = parse_logging(IOS_SAMPLE)
    _, remove = plan_syslog(current, ["host:10.1.1.50:514"], MODE_REPLACE)
    assert [e.line for e in remove] == ["logging host 10.9.9.9 transport udp port 1514"]


def test_replace_never_negates_a_scalar():
    """`no logging trap notifications` clears the setting whatever argument it
    is given, so negating a stale one would undo the new value."""
    current = parse_logging(IOS_SAMPLE)
    _, remove = plan_syslog(current, ["trap:informational"], MODE_REPLACE)
    assert not any("trap" in e.line or "source-interface" in e.line for e in remove)


DOCUMENT = {
    "syslog": {
        "destinations": [
            "10.1.1.50",
            {"host": "10.1.1.51", "port": 1514},
        ],
        "severity": "informational",
        "source": "Loopback0",
    }
}


def test_reads_the_standards_file():
    desired = FEATURE.build_desired(parse_args([], DOCUMENT))
    assert desired.keys == [
        "host:10.1.1.50:514",
        "host:10.1.1.51:1514",
        "trap:informational",
        "source:Loopback0",
    ]


def test_a_flag_overrides_the_file():
    desired = FEATURE.build_desired(parse_args(["-d", "10.9.9.9"], DOCUMENT))
    assert desired.keys[0] == "host:10.9.9.9:514"
    assert not any(k.startswith("host:10.1.1.50") for k in desired.keys)


def test_addr_port_shorthand():
    desired = FEATURE.build_desired(parse_args(["-d", "10.9.9.9:1514"], DOCUMENT))
    assert desired.keys[0] == "host:10.9.9.9:1514"


def test_nothing_configured_is_an_error():
    with pytest.raises(ValueError, match="nothing to configure"):
        FEATURE.build_desired(parse_args([], {}))


def test_an_unknown_severity_is_rejected():
    with pytest.raises(ValueError, match="unknown syslog severity"):
        FEATURE.build_desired(parse_args([], {"syslog": {"severity": "chatty"}}))


def test_renders_ios():
    desired = FEATURE.build_desired(parse_args([], DOCUMENT))
    assert render("syslog", "cisco_ios", desired.keys, [], desired.variables) == [
        "logging host 10.1.1.50",
        "logging host 10.1.1.51 transport udp port 1514",
        "logging trap informational",
        "logging source-interface Loopback0",
    ]


def test_renders_eos():
    desired = FEATURE.build_desired(parse_args([], DOCUMENT))
    assert render("syslog", "arista_eos", desired.keys, [], desired.variables) == [
        "logging host 10.1.1.50",
        "logging host 10.1.1.51 1514",
        "logging trap informational",
        "logging source-interface Loopback0",
    ]


def test_vrf_from_the_file():
    document = {"syslog": {"destinations": ["10.1.1.50"], "vrf": "MGMT"}}
    desired = FEATURE.build_desired(parse_args([], document))
    assert render("syslog", "cisco_ios", desired.keys, [], desired.variables) == [
        "logging host vrf MGMT 10.1.1.50"
    ]


# --------------------------------------------------------------------------- #
# origin-id -- Cisco only
# --------------------------------------------------------------------------- #


def test_origin_id_keyword_from_the_standards_file():
    document = {"syslog": {"destinations": ["10.1.1.50"], "origin_id": "hostname"}}
    desired = FEATURE.build_desired(parse_args([], document))
    assert "origin:hostname" in desired.keys
    assert render("syslog", "cisco_ios", desired.keys, [], desired.variables) == [
        "logging host 10.1.1.50",
        "logging origin-id hostname",
    ]


def test_any_other_text_is_sent_as_a_string():
    document = {"syslog": {"destinations": ["10.1.1.50"], "origin_id": "ATL-DC1"}}
    desired = FEATURE.build_desired(parse_args([], document))
    assert "origin:string ATL-DC1" in desired.keys
    assert "logging origin-id string ATL-DC1" in render(
        "syslog", "cisco_ios", desired.keys, [], desired.variables
    )


def test_a_changed_origin_id_is_detected():
    """The key carries the value, so a different one is a different key."""
    document = {"syslog": {"destinations": ["10.1.1.50"], "origin_id": "hostname"}}
    desired = FEATURE.build_desired(parse_args([], document))
    add, _ = plan_syslog(parse_logging(IOS_SAMPLE), desired.keys, MODE_ADD,
                         {"variables": desired.variables})
    assert "origin:hostname" in add


def test_origin_id_is_never_negated():
    """Like the other scalars: setting it replaces the old value."""
    document = {"syslog": {"destinations": ["10.1.1.50"], "origin_id": "hostname"}}
    desired = FEATURE.build_desired(parse_args([], document))
    _, remove = plan_syslog(parse_logging(IOS_SAMPLE), desired.keys, MODE_REPLACE,
                            {"variables": desired.variables})
    assert not any("origin-id" in e.line for e in remove)


def test_eos_never_sees_it():
    """EOS has no `logging origin-id`. Leaving it in the desired set would make
    every EOS device look out of compliance forever."""
    document = {"syslog": {"destinations": ["10.1.1.50"], "origin_id": "hostname"}}
    desired = FEATURE.build_desired(parse_args([], document))
    add, _ = plan_syslog(
        parse_logging(EOS_SAMPLE),
        desired.keys,
        MODE_ADD,
        {"variables": desired.variables, "ignores": ("origin",)},
    )
    assert not any(key.startswith("origin:") for key in add)


def test_an_injected_origin_id_is_rejected():
    from netops.core import InvalidValue

    document = {"syslog": {"destinations": ["10.1.1.50"],
                           "origin_id": "DC1\nlogging host 10.6.6.6"}}
    with pytest.raises(InvalidValue):
        FEATURE.build_desired(parse_args([], document))
