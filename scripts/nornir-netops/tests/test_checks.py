import argparse

import pytest

from netops.checks import (
    EOS_SAMPLE,
    FAIL,
    IOS_SAMPLE,
    NTP,
    OK,
    WARN,
    evaluate_ntp,
    parse_ntp_status,
)
from netops.standards import Standards

def parse_args(argv=(), document=None):
    parser = argparse.ArgumentParser()
    NTP.add_arguments(parser)
    args = parser.parse_args(list(argv))
    args.standards = Standards(path="test", document=document or {})
    return args


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def test_reads_ios_status_and_associations():
    state = parse_ntp_status(IOS_SAMPLE)
    assert state["synchronized"] is True
    assert state["stratum"] == 2
    assert state["reference"] == "10.50.0.10"
    assert [a["address"] for a in state["associations"]] == [
        "10.50.0.10",
        "10.50.0.11",
        "10.9.9.9",
    ]


def test_reads_eos_despite_its_extra_column():
    """EOS prints an ntpq-style table with a `t` column that IOS does not."""
    state = parse_ntp_status(EOS_SAMPLE)
    assert state["synchronized"] is True
    assert state["stratum"] == 3
    assert state["reference"] == "10.50.0.10"
    peer = state["associations"][0]
    assert (peer["address"], peer["state"], peer["reach"]) == ("10.50.0.10", "sys.peer", 255)


def test_reach_is_octal_as_ntp_prints_it():
    """377 is not three hundred and seventy-seven; it is eight polls out of
    eight."""
    state = parse_ntp_status(IOS_SAMPLE)
    peer = state["associations"][0]
    assert peer["reach_octal"] == "377"
    assert peer["reach"] == 255


def test_selection_flags():
    states = {a["address"]: a["state"] for a in parse_ntp_status(IOS_SAMPLE)["associations"]}
    assert states["10.50.0.10"] == "sys.peer"     # *
    assert states["10.50.0.11"] == "candidate"    # +
    assert states["10.9.9.9"] == "rejected"       # configured only


def test_offsets_and_delays_are_numbers():
    peer = parse_ntp_status(IOS_SAMPLE)["associations"][0]
    assert peer["offset"] == pytest.approx(0.123)
    assert peer["delay"] == pytest.approx(1.234)


def test_the_header_and_legend_are_not_associations():
    assert len(parse_ntp_status(IOS_SAMPLE)["associations"]) == 3


def test_an_unsynchronised_device():
    state = parse_ntp_status(
        "Clock is unsynchronized, stratum 16, no reference clock\n"
        "  address         ref clock       st   when   poll reach  delay  offset   disp\n"
        " ~10.50.0.10      .INIT.          16      -   1024     0  0.000   0.000 15937.\n"
    )
    assert state["synchronized"] is False
    assert state["stratum"] == 16
    assert state["reference"] is None
    assert state["associations"][0]["reach"] == 0


def test_empty_output_is_not_a_crash():
    state = parse_ntp_status("")
    assert state == {
        "synchronized": False,
        "stratum": None,
        "reference": None,
        "associations": [],
    }


# --------------------------------------------------------------------------- #
# judgement
# --------------------------------------------------------------------------- #


EXPECTED = ["10.50.0.10", "10.50.0.11"]
NO_LIMIT = {"max_offset": None}


def test_a_healthy_device_is_ok():
    verdict = evaluate_ntp(parse_ntp_status(IOS_SAMPLE), EXPECTED, NO_LIMIT)
    assert verdict.status == OK
    assert "synchronised to 10.50.0.10" in verdict.summary
    assert "stratum 2" in verdict.summary


def test_missed_polls_are_a_warning_not_a_failure():
    """reach 177 means seven of the last eight polls came back. Working, but
    worth knowing about."""
    degraded = IOS_SAMPLE.replace("377  1.234   0.123", "177  1.234   0.123")
    verdict = evaluate_ntp(parse_ntp_status(degraded), EXPECTED, NO_LIMIT)
    assert verdict.status == WARN
    assert "missed polls (reach 177)" in verdict.summary


def test_an_unreachable_server_is_a_failure():
    verdict = evaluate_ntp(parse_ntp_status(IOS_SAMPLE), EXPECTED + ["10.9.9.9"], NO_LIMIT)
    assert verdict.status == FAIL
    assert "10.9.9.9 unreachable (reach 0)" in verdict.summary


def test_a_server_that_is_not_associated_at_all():
    verdict = evaluate_ntp(parse_ntp_status(IOS_SAMPLE), ["10.60.0.1"], NO_LIMIT)
    assert verdict.status == FAIL
    assert "10.60.0.1 is not associated" in verdict.summary


def test_an_unsynchronised_clock_fails_whatever_else_is_true():
    output = IOS_SAMPLE.replace("Clock is synchronized", "Clock is unsynchronized")
    verdict = evaluate_ntp(parse_ntp_status(output), EXPECTED, NO_LIMIT)
    assert verdict.status == FAIL
    assert "not synchronised" in verdict.summary


def test_no_sys_peer_means_nothing_was_selected():
    """Associations can all be reachable and still none chosen."""
    output = IOS_SAMPLE.replace("*~10.50.0.10", " ~10.50.0.10")
    verdict = evaluate_ntp(parse_ntp_status(output), EXPECTED, NO_LIMIT)
    assert verdict.status == FAIL
    assert "no association selected as sys.peer" in verdict.summary


def test_a_large_offset_is_a_warning():
    verdict = evaluate_ntp(parse_ntp_status(IOS_SAMPLE), EXPECTED, {"max_offset": 0.05})
    assert verdict.status == WARN
    assert "exceeds" in verdict.summary


def test_an_offset_inside_the_limit_is_fine():
    verdict = evaluate_ntp(parse_ntp_status(IOS_SAMPLE), EXPECTED, {"max_offset": 1000})
    assert verdict.status == OK


def test_a_failure_outranks_a_warning():
    output = IOS_SAMPLE.replace("377  1.234   0.123", "177  1.234   0.123")
    verdict = evaluate_ntp(parse_ntp_status(output), EXPECTED + ["10.60.0.1"], NO_LIMIT)
    assert verdict.status == FAIL


# --------------------------------------------------------------------------- #
# what to expect
# --------------------------------------------------------------------------- #


def test_expected_servers_come_from_the_standards_file():
    document = {"ntp": {"servers": ["10.50.0.10", {"host": "10.50.0.11"}]}}
    assert NTP.expected(parse_args((), document)) == ["10.50.0.10", "10.50.0.11"]


def test_a_flag_overrides_the_file():
    document = {"ntp": {"servers": ["10.50.0.10"]}}
    assert NTP.expected(parse_args(["-s", "10.9.9.9,10.9.9.10"], document)) == [
        "10.9.9.9",
        "10.9.9.10",
    ]


def test_no_expectation_at_all_still_checks_synchronisation():
    """Without a list to compare against, "is the clock actually set" is still
    a useful answer."""
    verdict = evaluate_ntp(parse_ntp_status(IOS_SAMPLE), [], NO_LIMIT)
    assert verdict.status == OK


def test_an_unsupported_platform_is_named():
    from netops.core import UnsupportedPlatform

    with pytest.raises(UnsupportedPlatform, match="juniper_junos"):
        NTP.support_for("juniper_junos")
