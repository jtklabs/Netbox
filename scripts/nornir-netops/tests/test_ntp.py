import argparse

import pytest

from netops.core import MODE_ADD, MODE_REPLACE, InvalidValue, render, scrub
from netops.credentials import CredentialError
from netops.features.ntp import (
    EOS_SAMPLE,
    FEATURE,
    IOS_SAMPLE,
    key_variable,
    parse_ntp,
    plan_ntp,
)
from netops.standards import Standards, StandardsError

KEY = "ntp-key-material"


def parse_args(argv=(), document=None):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    parser.add_argument("--aws-region", default=None)
    args = parser.parse_args(list(argv))
    args.standards = Standards(path="test", document=document or {})
    return args


AUTH_DOCUMENT = {
    "ntp": {
        "servers": ["10.50.0.10", "10.50.0.11"],
        "authentication": {"key_id": 1, "type": "md5", "trusted": True, "enable": True},
    }
}


@pytest.fixture
def key(monkeypatch):
    monkeypatch.setenv("NETOPS_NTP_KEY_1", KEY)
    return KEY


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def test_parses_ios_servers():
    """The key carries how the server is configured, not just its address: a
    stale `source` would otherwise read as compliant forever."""
    keys = [e.key for e in parse_ntp(IOS_SAMPLE) if e.data["kind"] == "server"]
    assert keys == [
        "server:10.10.10.1:key:1",
        "server:10.10.10.2",
        "server:192.168.5.5:source:GigabitEthernet0/0",
    ]


def test_parses_eos_servers_including_a_hostname():
    keys = [e.key for e in parse_ntp(EOS_SAMPLE) if e.data["kind"] == "server"]
    assert keys == [
        "server:10.10.10.1:key:1",
        "server:192.168.5.5",
        "server:time.example.net",
    ]


def test_the_source_interface_is_part_of_a_servers_identity():
    plain = parse_ntp("ntp server 10.1.1.1")[0]
    sourced = parse_ntp("ntp server 10.1.1.1 source Loopback0")[0]
    assert plain.key != sourced.key
    assert sourced.data["source"] == "Loopback0"


def test_a_server_line_is_kept_verbatim_for_removal():
    entries = {e.key: e for e in parse_ntp(IOS_SAMPLE)}
    assert entries["server:192.168.5.5:source:GigabitEthernet0/0"].line == (
        "ntp server vrf MGMT 192.168.5.5 source GigabitEthernet0/0"
    )


def test_the_key_binding_is_part_of_a_servers_identity():
    """A server configured without its key is not the same as one with it."""
    unkeyed = parse_ntp("ntp server 10.10.10.1")[0]
    keyed = parse_ntp("ntp server 10.10.10.1 key 1")[0]
    assert unkeyed.key != keyed.key
    assert unkeyed.data["host"] == keyed.data["host"]


def test_parses_authentication():
    entries = {e.key: e for e in parse_ntp(IOS_SAMPLE)}
    assert "key:1:md5" in entries
    assert "trusted-key:1" in entries
    assert "authenticate" in entries


def test_the_key_material_is_never_carried_out_of_the_parser():
    """IOS stores it encrypted; it is not ours to show either way."""
    entry = {e.key: e for e in parse_ntp(IOS_SAMPLE)}["key:1:md5"]
    assert "072C285F4D06" not in entry.shown
    assert "072C285F4D06" not in entry.line
    assert entry.shown == "ntp authentication-key 1 md5 <hidden>"


def test_eos_orders_the_key_arguments_differently_but_parses_the_same():
    ios = {e.key for e in parse_ntp("ntp authentication-key 1 md5 072C285F 7")}
    eos = {e.key for e in parse_ntp("ntp authentication-key 1 md5 7 072C285F")}
    assert ios == eos == {"key:1:md5"}


def test_multiple_trusted_keys_on_one_line():
    keys = [e.key for e in parse_ntp("ntp trusted-key 1 2 3")]
    assert keys == ["trusted-key:1", "trusted-key:2", "trusted-key:3"]


def test_parser_ignores_other_ntp_config():
    """`ntp source`, `ntp master` and friends are never parsed, so --replace
    can never remove them."""
    output = "ntp source Loopback0\nntp master 3\nntp access-group peer 10\nntp server 10.1.1.1\n"
    assert [e.key for e in parse_ntp(output)] == ["server:10.1.1.1"]


def test_parser_tolerates_junk_and_empty_output():
    assert parse_ntp("") == []
    assert [e.key for e in parse_ntp("  ntp server 10.1.1.1  \nntp server\n\n")] == [
        "server:10.1.1.1"
    ]


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


def context(document=None, args=()):
    desired = FEATURE.build_desired(parse_args(args, document))
    return desired, {"platform": "cisco_ios", "variables": desired.variables}


def test_a_missing_server_is_added(key):
    desired, ctx = context(AUTH_DOCUMENT)
    add, _ = plan_ntp([], desired.keys, MODE_ADD, ctx)
    assert "server:10.50.0.10:key:1" in add


def test_an_already_correct_fleet_is_a_no_op(key):
    desired, ctx = context(AUTH_DOCUMENT)
    current = parse_ntp(
        "ntp authentication-key 1 md5 x 7\n"
        "ntp trusted-key 1\n"
        "ntp authenticate\n"
        "ntp server 10.50.0.10 key 1\n"
        "ntp server 10.50.0.11 key 1\n"
    )
    assert plan_ntp(current, desired.keys, MODE_ADD, ctx) == ([], [])


def test_a_server_missing_its_key_is_reissued_not_removed(key):
    """Re-issuing the line replaces it. Negating it afterwards would delete the
    server that was just corrected."""
    desired, ctx = context(AUTH_DOCUMENT)
    current = parse_ntp("ntp server 10.50.0.10\nntp server 10.50.0.11\n")
    add, remove = plan_ntp(current, desired.keys, MODE_REPLACE, ctx)
    assert "server:10.50.0.10:key:1" in add
    assert remove == []


def test_replace_still_removes_a_server_that_is_not_wanted(key):
    desired, ctx = context(AUTH_DOCUMENT)
    current = parse_ntp("ntp server 10.9.9.9 prefer\n")
    _, remove = plan_ntp(current, desired.keys, MODE_REPLACE, ctx)
    assert [e.line for e in remove] == ["ntp server 10.9.9.9 prefer"]


def test_authentication_is_left_alone_when_the_standard_is_silent():
    """Silence is not a statement: a file that says nothing about
    authentication is not asking for it to be torn off devices that have it."""
    desired, ctx = context({"ntp": {"servers": ["10.50.0.10"]}})
    _, remove = plan_ntp(parse_ntp(IOS_SAMPLE), desired.keys, MODE_REPLACE, ctx)
    assert not any(e.data["kind"] != "server" for e in remove)


def test_a_declared_standard_does_manage_authentication(key):
    desired, ctx = context(AUTH_DOCUMENT)
    current = parse_ntp("ntp authentication-key 9 md5 x 7\nntp trusted-key 9\n")
    _, remove = plan_ntp(current, desired.keys, MODE_REPLACE, ctx)
    assert {e.line for e in remove} == {
        "ntp authentication-key 9",
        "ntp trusted-key 9",
    }


def test_rewrite_keys_reissues_a_key_that_looks_present(key):
    """The material is stored encrypted, so nothing else would ever push it."""
    desired, ctx = context(AUTH_DOCUMENT, args=["--rewrite-keys"])
    current = parse_ntp("ntp authentication-key 1 md5 x 7\n")
    add, _ = plan_ntp(current, desired.keys, MODE_ADD, ctx)
    assert "key:1:md5" in add


def test_without_the_flag_a_present_key_is_left_alone(key):
    desired, ctx = context(AUTH_DOCUMENT)
    current = parse_ntp("ntp authentication-key 1 md5 x 7\n")
    add, _ = plan_ntp(current, desired.keys, MODE_ADD, ctx)
    assert "key:1:md5" not in add


# --------------------------------------------------------------------------- #
# desired state
# --------------------------------------------------------------------------- #


def test_servers_split_and_deduped():
    desired = FEATURE.build_desired(parse_args(["-s", "10.1.1.1,10.1.1.2", "-s", "10.1.1.1"]))
    assert desired.keys == ["server:10.1.1.1", "server:10.1.1.2"]


def test_servers_are_normalized():
    desired = FEATURE.build_desired(parse_args(["-s", "010.1.1.1, TIME.example.NET"]))
    assert desired.keys == ["server:10.1.1.1", "server:time.example.net"]


def test_servers_come_from_the_standards_file(key):
    assert FEATURE.build_desired(parse_args((), AUTH_DOCUMENT)).keys[2:4] == [
        "server:10.50.0.10:key:1",
        "server:10.50.0.11:key:1",
    ]


def test_a_flag_overrides_the_file(key):
    desired = FEATURE.build_desired(parse_args(["-s", "10.9.9.9"], AUTH_DOCUMENT))
    assert "server:10.9.9.9:key:1" in desired.keys
    assert not any("10.50.0.10" in k for k in desired.keys)


def test_no_servers_anywhere_is_an_error():
    with pytest.raises(ValueError, match="no NTP servers given"):
        FEATURE.build_desired(parse_args())


def test_prefer_must_be_one_of_the_servers():
    with pytest.raises(ValueError, match="not one of the desired servers"):
        FEATURE.build_desired(parse_args(["-s", "10.1.1.1", "--prefer", "10.9.9.9"]))


def test_injection_in_servers_is_rejected():
    with pytest.raises(InvalidValue):
        FEATURE.build_desired(parse_args(["-s", "10.1.1.1 ; reload in 1"]))


def test_the_order_keeps_a_device_from_authenticating_too_early(key):
    """Key, then trusted-key, then the servers that use it, then authenticate."""
    keys = FEATURE.build_desired(parse_args((), AUTH_DOCUMENT)).keys
    assert keys[0] == "key:1:md5"
    assert keys[1] == "trusted-key:1"
    assert keys[-1] == "authenticate"
    assert all(k.startswith("server:") for k in keys[2:-1])


def test_trusted_and_enable_can_be_switched_off(key):
    document = {
        "ntp": {"servers": ["10.50.0.10"], "authentication": {"key_id": 1, "trusted": False,
                                                              "enable": False}}
    }
    keys = FEATURE.build_desired(parse_args((), document)).keys
    assert "authenticate" not in keys
    assert not any(k.startswith("trusted-key") for k in keys)


def test_authentication_without_a_key_id_is_rejected():
    with pytest.raises(StandardsError, match="needs a key_id"):
        FEATURE.build_desired(parse_args((), {"ntp": {"servers": ["10.1.1.1"],
                                                      "authentication": {"type": "md5"}}}))


# --------------------------------------------------------------------------- #
# the key material
# --------------------------------------------------------------------------- #


def test_key_variable_naming():
    assert key_variable(1) == "NETOPS_NTP_KEY_1"
    assert key_variable("mgmt-1") == "NETOPS_NTP_KEY_MGMT_1"


def test_the_key_is_a_secret(key):
    assert FEATURE.build_desired(parse_args((), AUTH_DOCUMENT)).secrets == [KEY]


def test_the_key_can_come_from_a_secrets_manager_map(monkeypatch):
    monkeypatch.setattr(
        "netops.credentials.fetch_json_secret", lambda name, region=None: {"1": KEY}
    )
    desired = FEATURE.build_desired(parse_args(["--key-secret", "prod/ntp"], AUTH_DOCUMENT))
    assert desired.secrets == [KEY]


def test_a_missing_key_names_the_variable(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(CredentialError, match="NETOPS_NTP_KEY_1"):
        FEATURE.build_desired(parse_args((), AUTH_DOCUMENT))


def test_a_short_key_is_rejected(monkeypatch):
    monkeypatch.setenv("NETOPS_NTP_KEY_1", "short")
    with pytest.raises(ValueError, match="shorter than"):
        FEATURE.build_desired(parse_args((), AUTH_DOCUMENT))


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def test_renders_ios_in_the_safe_order(key):
    desired = FEATURE.build_desired(parse_args((), AUTH_DOCUMENT))
    assert render("ntp", "cisco_ios", desired.keys, [], desired.variables) == [
        f"ntp authentication-key 1 md5 {KEY}",
        "ntp trusted-key 1",
        "ntp server 10.50.0.10 key 1",
        "ntp server 10.50.0.11 key 1",
        "ntp authenticate",
    ]


def test_renders_eos_with_iburst(key):
    desired = FEATURE.build_desired(parse_args((), AUTH_DOCUMENT))
    commands = render("ntp", "arista_eos", desired.keys, [], desired.variables)
    assert "ntp server 10.50.0.10 key 1 iburst" in commands
    assert f"ntp authentication-key 1 md5 {KEY}" in commands


def test_the_key_is_scrubbed_from_what_gets_shown(key):
    desired = FEATURE.build_desired(parse_args((), AUTH_DOCUMENT))
    commands = render("ntp", "cisco_ios", desired.keys, [], desired.variables)
    shown = [scrub(command, desired.secrets) for command in commands]
    assert not any(KEY in command for command in shown)
    assert "ntp authentication-key 1 md5 <redacted>" in shown


def test_renders_without_authentication_when_none_is_declared():
    desired = FEATURE.build_desired(parse_args(["-s", "10.1.1.1", "--prefer", "10.1.1.1"]))
    assert render("ntp", "cisco_ios", desired.keys, [], desired.variables) == [
        "ntp server 10.1.1.1 prefer"
    ]


def test_ios_options_and_removal():
    from netops.core import Entry

    desired = FEATURE.build_desired(
        parse_args(["-s", "10.1.1.1,10.1.1.2", "--prefer", "10.1.1.2", "--vrf", "MGMT",
                    "--source", "Vlan10"])
    )
    commands = render(
        "ntp",
        "cisco_ios",
        desired.keys,
        [Entry("server:10.9.9.9", "ntp server vrf MGMT 10.9.9.9 source Vlan10")],
        desired.variables,
    )
    assert commands == [
        "ntp server vrf MGMT 10.1.1.1 source Vlan10",
        "ntp server vrf MGMT 10.1.1.2 source Vlan10 prefer",
        "no ntp server vrf MGMT 10.9.9.9 source Vlan10",
    ]


def test_every_platform_template_exists():
    from netops.core import template_dir

    for platform in FEATURE.platforms:
        assert (template_dir() / platform / f"{FEATURE.name}.j2").is_file()
