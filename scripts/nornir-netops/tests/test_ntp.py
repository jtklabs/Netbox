import argparse

import pytest

from netops.core import InvalidValue
from netops.features.ntp import FEATURE, EOS_SAMPLE, IOS_SAMPLE, parse_ntp_servers


def parse_args(argv):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    return parser.parse_args(argv)


def test_parses_ios_sample():
    entries = parse_ntp_servers(IOS_SAMPLE)
    assert [e.key for e in entries] == ["10.10.10.1", "10.10.10.2", "192.168.5.5"]
    # the vrf line is kept verbatim so `no <line>` removes it cleanly
    assert entries[2].line == "ntp server vrf MGMT 192.168.5.5 source GigabitEthernet0/0"


def test_parses_eos_sample_including_hostname():
    assert [e.key for e in parse_ntp_servers(EOS_SAMPLE)] == [
        "10.10.10.1",
        "192.168.5.5",
        "time.example.net",
    ]


def test_parser_ignores_other_ntp_config():
    output = """
ntp source Loopback0
ntp authenticate
ntp access-group peer 10
ntp server 10.1.1.1
"""
    assert [e.key for e in parse_ntp_servers(output)] == ["10.1.1.1"]


def test_parser_tolerates_indentation_and_junk():
    output = "  ntp server 10.1.1.1  \nntp server\n\nbanner\n"
    entries = parse_ntp_servers(output)
    assert [e.key for e in entries] == ["10.1.1.1"]
    assert entries[0].line == "ntp server 10.1.1.1"  # stripped for a clean `no`


def test_parser_handles_empty_output():
    assert parse_ntp_servers("") == []


def test_servers_split_and_deduped():
    desired = FEATURE.build_desired(
        parse_args(["-s", "10.1.1.1,10.1.1.2", "-s", "10.1.1.1", "-s", "10.1.1.3"])
    )
    assert desired.keys == ["10.1.1.1", "10.1.1.2", "10.1.1.3"]


def test_servers_are_normalized():
    desired = FEATURE.build_desired(parse_args(["-s", "010.1.1.1, TIME.example.NET"]))
    assert desired.keys == ["10.1.1.1", "time.example.net"]


def test_prefer_must_be_one_of_the_servers():
    with pytest.raises(ValueError, match="not one of --servers"):
        FEATURE.build_desired(parse_args(["-s", "10.1.1.1", "--prefer", "10.9.9.9"]))


def test_prefer_matches_after_normalization():
    desired = FEATURE.build_desired(parse_args(["-s", "10.1.1.1", "--prefer", "010.1.1.1"]))
    assert desired.variables["prefer"] == "10.1.1.1"


def test_injection_in_servers_is_rejected():
    with pytest.raises(InvalidValue):
        FEATURE.build_desired(parse_args(["-s", "10.1.1.1 ; reload in 1"]))


def test_injection_in_vrf_is_rejected():
    with pytest.raises(InvalidValue):
        FEATURE.build_desired(parse_args(["-s", "10.1.1.1", "--vrf", "MGMT\nreload"]))


def test_iburst_defaults_on_and_can_be_disabled():
    assert FEATURE.build_desired(parse_args(["-s", "10.1.1.1"])).variables["iburst"] is True
    assert (
        FEATURE.build_desired(parse_args(["-s", "10.1.1.1", "--no-iburst"])).variables["iburst"]
        is False
    )


def test_every_platform_template_exists():
    from netops.core import template_dir

    for platform in FEATURE.platforms:
        assert (template_dir() / platform / f"{FEATURE.name}.j2").is_file()
