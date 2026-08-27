import argparse

import pytest

from netops.core import MODE_ADD, MODE_REPLACE, render
from netops.features.banner import (
    EOS_SAMPLE,
    FEATURE,
    IOS_SAMPLE,
    normalize_body,
    parse_banners,
    plan_banner,
)
from netops.standards import Standards


def parse_args(argv, document=None):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    args = parser.parse_args(argv)
    args.standards = Standards(path="test", document=document or {})
    return args


def test_parses_an_ios_banner_between_its_delimiters():
    entries = parse_banners(IOS_SAMPLE)
    assert [e.key for e in entries] == ["motd"]
    assert entries[0].data["body"] == (
        "  Authorised access only. Activity is logged and monitored."
    )


def test_parses_an_eos_banner_terminated_by_eof():
    assert parse_banners(EOS_SAMPLE)[0].data["body"] == (
        "Authorised access only. Activity is logged and monitored."
    )


def test_normalize_ignores_surrounding_blank_lines_and_trailing_space():
    assert normalize_body(["", "  text   ", "", ""]) == "  text"


def test_normalize_keeps_blank_lines_inside_the_body():
    assert normalize_body(["one", "", "two"]) == "one\n\ntwo"


def test_display_does_not_dump_the_whole_banner():
    assert parse_banners(IOS_SAMPLE)[0].shown == "banner motd (1 line(s))"


def test_a_banner_that_already_matches_is_left_alone():
    """Rendered, re-parsed and compared: the same text is not a change."""
    variables = {"delimiter": None}
    body = render("banner", "cisco_ios", ["motd"], [], variables, keep_blank=True)
    current = parse_banners("\n".join(body))
    add, remove = plan_banner(
        current, ["motd"], MODE_ADD, {"platform": "cisco_ios", "variables": variables}
    )
    assert (add, remove) == ([], [])


def test_a_different_banner_is_rewritten():
    current = parse_banners(IOS_SAMPLE)
    add, _ = plan_banner(
        current, ["motd"], MODE_ADD, {"platform": "cisco_ios", "variables": {"delimiter": None}}
    )
    assert add == ["motd"]


def test_a_missing_banner_is_created():
    add, remove = plan_banner(
        [], ["motd"], MODE_ADD, {"platform": "cisco_ios", "variables": {"delimiter": None}}
    )
    assert (add, remove) == (["motd"], [])


def test_replace_removes_a_banner_the_standard_does_not_mention():
    current = parse_banners("banner login ^C\nold\n^C\n")
    _, remove = plan_banner(
        current, ["motd"], MODE_REPLACE, {"platform": "cisco_ios", "variables": {"delimiter": None}}
    )
    assert [e.line for e in remove] == ["banner login"]


def test_blank_lines_in_the_body_survive_rendering():
    lines = render("banner", "cisco_ios", ["motd"], [], {"delimiter": None}, keep_blank=True)
    assert lines[0] == "banner motd ^C"
    assert lines[-1] == "^C"
    assert "" in lines, "the blank line between paragraphs is part of the text"


def test_eos_uses_eof_and_no_delimiter():
    lines = render("banner", "arista_eos", ["motd"], [], {"delimiter": None}, keep_blank=True)
    assert lines[0] == "banner motd"
    assert lines[-1] == "EOF"


def test_a_custom_delimiter_is_used_on_ios():
    lines = render("banner", "cisco_ios", ["motd"], [], {"delimiter": "#"}, keep_blank=True)
    assert lines[0] == "banner motd #"
    assert lines[-1] == "#"


def test_selected_from_the_standards_file():
    document = {"banner": {"motd": True, "login": False}}
    assert FEATURE.build_desired(parse_args([], document)).keys == ["motd"]


def test_a_flag_overrides_the_file():
    document = {"banner": {"motd": True}}
    assert FEATURE.build_desired(parse_args(["-b", "login"], document)).keys == ["login"]


def test_no_banner_selected_is_an_error():
    with pytest.raises(ValueError, match="no banners selected"):
        FEATURE.build_desired(parse_args([], {"banner": {"motd": False}}))


def test_the_config_push_disables_command_verification():
    """netmiko waits for a prompt that does not come until the banner ends."""
    assert FEATURE.config_options == {"cmd_verify": False}
