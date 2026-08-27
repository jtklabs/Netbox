import argparse

import pytest

from netops.core import MODE_ADD, MODE_REPLACE, NotApplicable, render
from netops.features.snmp_packetsize import (
    DEFAULT_SIZE,
    FEATURE,
    IOS_SAMPLE,
    MAX_SIZE,
    MIN_SIZE,
    parse_packetsize,
    plan_packetsize,
)


def parse_args(argv):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    return parser.parse_args(argv)


def test_parses_the_configured_size():
    entries = parse_packetsize(IOS_SAMPLE)
    assert [e.key for e in entries] == ["1500"]


def test_default_device_has_nothing_configured():
    """IOS does not write the 1500 default into the running config."""
    assert parse_packetsize("") == []


def test_parser_ignores_other_snmp_lines():
    output = "snmp-server community public RO\nsnmp-server packetsize 1300\n"
    assert [e.key for e in parse_packetsize(output)] == ["1300"]


def test_sets_the_size_when_it_differs():
    add, remove = plan_packetsize(parse_packetsize(IOS_SAMPLE), ["1300"], MODE_ADD)
    assert (add, remove) == (["1300"], [])


def test_sets_the_size_when_nothing_is_configured():
    add, remove = plan_packetsize([], ["1300"], MODE_ADD)
    assert (add, remove) == (["1300"], [])


def test_no_change_when_already_correct():
    add, remove = plan_packetsize(parse_packetsize("snmp-server packetsize 1300"), ["1300"],
                                 MODE_ADD)
    assert (add, remove) == ([], [])


def test_replace_behaves_like_add_for_a_scalar():
    """Writing the value replaces it; there is nothing to negate."""
    add, remove = plan_packetsize(parse_packetsize(IOS_SAMPLE), ["1300"], MODE_REPLACE)
    assert (add, remove) == (["1300"], [])


def test_renders_the_ios_command():
    assert render("snmp-packetsize", "cisco_ios", ["1300"], [], {}) == [
        "snmp-server packetsize 1300"
    ]


def test_default_size_is_1300():
    assert FEATURE.build_desired(parse_args([])).keys == [str(DEFAULT_SIZE)]


@pytest.mark.parametrize("size", [MIN_SIZE - 1, MAX_SIZE + 1, 0, -1])
def test_size_outside_the_platform_range_is_rejected(size):
    with pytest.raises(ValueError, match="must be between"):
        FEATURE.build_desired(parse_args(["--size", str(size)]))


@pytest.mark.parametrize("size", [MIN_SIZE, 1300, MAX_SIZE])
def test_size_inside_the_range_is_accepted(size):
    assert FEATURE.build_desired(parse_args(["--size", str(size)])).keys == [str(size)]


def test_arista_is_not_applicable_rather_than_unsupported():
    """A mixed-fleet run should skip EOS, not fail it."""
    with pytest.raises(NotApplicable, match="no `snmp-server packetsize` equivalent"):
        FEATURE.support_for("arista_eos")


def test_an_unknown_platform_is_still_unsupported():
    from netops.core import UnsupportedPlatform

    with pytest.raises(UnsupportedPlatform):
        FEATURE.support_for("juniper_junos")


def test_the_show_command_asks_for_defaults():
    """1500 is the platform default and is not written to the running config,
    so a plain `show running-config` cannot tell unset from set-to-1500."""
    support = FEATURE.platforms["cisco_ios"]
    assert support.show_command == (
        "show running-config all | include ^snmp-server packetsize"
    )


def test_a_size_already_at_the_default_is_compliant():
    current = parse_packetsize("snmp-server packetsize 1500")
    assert plan_packetsize(current, ["1500"], MODE_ADD) == ([], [])
