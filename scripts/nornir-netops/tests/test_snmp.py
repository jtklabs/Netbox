import argparse

import pytest

from netops.core import MODE_ADD, MODE_REPLACE, render, scrub
from netops.credentials import CredentialError
from netops.features.snmp import (
    EOS_SAMPLE,
    FEATURE,
    IOS_SAMPLE,
    parse_snmp,
    passphrase_variables,
    plan_snmp,
)
from netops.standards import Standards, StandardsError

DOCUMENT = {
    "snmp": {
        "allow": ["10.1.1.0/24"],
        "acl": "SNMP-POLLERS",
        "communities": [],
        "location": "ATL DC1 - row 4",
        "contact": "netops@example.com",
        "views": [{"name": "NMS-VIEW", "oid": "iso", "action": "included"}],
        "groups": [{"name": "NMS-RO", "security": "priv", "read": "NMS-VIEW"}],
        "users": [{"name": "nmsuser", "group": "NMS-RO", "auth": "sha", "priv": "aes 128"}],
        "hosts": [{"host": "10.1.1.50", "user": "nmsuser", "security": "priv"}],
    }
}

AUTH = "auth-passphrase-1"
PRIV = "priv-passphrase-1"


@pytest.fixture
def passphrases(monkeypatch):
    monkeypatch.setenv("NETOPS_SNMP_AUTH_NMSUSER", AUTH)
    monkeypatch.setenv("NETOPS_SNMP_PRIV_NMSUSER", PRIV)


def parse_args(argv=(), document=None):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    parser.add_argument("--aws-region", default=None)
    args = parser.parse_args(list(argv))
    args.standards = Standards(path="test", document=document or DOCUMENT)
    return args


# --------------------------------------------------------------------------- #
# reading state from two different commands
# --------------------------------------------------------------------------- #


def test_v3_users_come_from_show_snmp_user_on_ios():
    """They are never in the running config, which is the whole reason the
    feature reads a second command."""
    users = {e.key: e for e in parse_snmp(IOS_SAMPLE)}
    assert users["user:nmsuser"].data["group"] == "NMS-RO"
    assert users["user:nmsuser"].data["auth"] == "md5"
    assert users["user:nmsuser"].data["priv"] == "des"


def test_v3_users_come_from_the_config_on_eos():
    users = {e.key: e for e in parse_snmp(EOS_SAMPLE)}
    assert users["user:nmsuser"].data == {
        "kind": "user",
        "group": "NMS-RO",
        "auth": "sha",
        "priv": "aes128",
    }


def test_the_feature_reads_both_commands_on_ios():
    assert FEATURE.platforms["cisco_ios"].commands == (
        "show running-config | include ^snmp-server",
        "show snmp user",
    )


@pytest.mark.parametrize(
    "written,normalized",
    [("aes128", "aes128"), ("aes 128", "aes128"), ("AES-128", "aes128"), ("des", "des")],
)
def test_privacy_protocol_spellings_are_the_same_protocol(written, normalized):
    """IOS writes `priv aes 128` as two tokens; EOS writes `aes128` as one."""
    parsed = parse_snmp(f"snmp-server user u G v3 auth sha authpass priv {written} privpass")
    assert parsed[0].data["priv"] == normalized


def test_an_all_digit_passphrase_is_not_read_as_part_of_the_protocol():
    parsed = parse_snmp("snmp-server user u G v3 auth sha 12345678 priv aes 128 87654321")
    assert parsed[0].data["auth"] == "sha"
    assert parsed[0].data["priv"] == "aes128"


def test_parses_groups_views_hosts_and_scalars():
    keys = [e.key for e in parse_snmp(IOS_SAMPLE)]
    assert "view:NMS-VIEW" in keys
    assert "group:NMS-RO" in keys
    assert "host:10.1.1.50" in keys
    assert "location:OLD LOCATION" in keys


def test_a_community_string_is_flagged_as_a_secret():
    community = [e for e in parse_snmp(IOS_SAMPLE) if e.data["kind"] == "community"][0]
    assert community.data["secret_value"] == "public"
    assert "public" not in community.shown


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


def context(platform="cisco_ios", ignores=(), document=None, args=()):
    desired = FEATURE.build_desired(parse_args(args, document))
    return desired, {
        "platform": platform,
        "variables": desired.variables,
        "ignores": ignores,
    }


def test_a_user_whose_protocols_differ_is_rebuilt(passphrases):
    """The passphrase is unreadable, so the group and protocols are all there
    is to compare -- md5/des against the standard's sha/aes128 is a change."""
    desired, ctx = context()
    add, remove = plan_snmp(parse_snmp(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "user:nmsuser" in add
    assert "snmp-server user nmsuser NMS-RO v3" in [e.line for e in remove]


def test_a_user_that_matches_is_left_alone(passphrases):
    desired, ctx = context(platform="arista_eos", ignores=("access",))
    add, _ = plan_snmp(parse_snmp(EOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "user:nmsuser" not in add


def test_a_missing_user_is_created_without_a_negation(passphrases):
    desired, ctx = context()
    add, remove = plan_snmp([], desired.keys, MODE_ADD, ctx)
    assert "user:nmsuser" in add
    assert remove == []


def test_a_community_is_removed_even_in_add_mode(passphrases):
    """`communities: []` is a statement that none may exist, not an extra to
    be left alone."""
    desired, ctx = context()
    _, remove = plan_snmp(parse_snmp(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "snmp-server community public" in [e.line for e in remove]


def test_communities_are_untouched_when_the_file_says_nothing(passphrases):
    document = {"snmp": {"location": "somewhere"}}
    desired, ctx = context(document=document)
    _, remove = plan_snmp(parse_snmp(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert not any(e.data["kind"] == "community" for e in remove)


def test_configuring_a_community_is_refused():
    document = {"snmp": {"communities": ["public"]}}
    with pytest.raises(StandardsError, match="may only be empty"):
        FEATURE.build_desired(parse_args((), document))


def test_eos_never_compares_the_acl_it_cannot_express(passphrases):
    """Without this the group would be rebuilt on every run, forever."""
    desired, ctx = context(platform="arista_eos", ignores=("access",))
    add, remove = plan_snmp(parse_snmp(EOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "group:NMS-RO" not in add and remove == []


def test_ios_does_compare_the_acl(passphrases):
    desired, ctx = context()
    without_acl = parse_snmp("snmp-server group NMS-RO v3 priv read NMS-VIEW")
    add, _ = plan_snmp(without_acl, desired.keys, MODE_ADD, ctx)
    assert "group:NMS-RO" in add


def test_a_changed_location_is_detected(passphrases):
    desired, ctx = context()
    add, _ = plan_snmp(parse_snmp(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "location:ATL DC1 - row 4" in add


def test_replace_removes_an_unmanaged_user(passphrases):
    desired, ctx = context()
    stray = parse_snmp("User name: olduser\nGroup-name: OLD\n")
    _, remove = plan_snmp(stray, desired.keys, MODE_REPLACE, ctx)
    assert any("olduser" in e.line for e in remove)


def test_add_mode_leaves_an_unmanaged_user_alone(passphrases):
    desired, ctx = context()
    stray = parse_snmp("User name: olduser\nGroup-name: OLD\n")
    _, remove = plan_snmp(stray, desired.keys, MODE_ADD, ctx)
    assert remove == []


# --------------------------------------------------------------------------- #
# passphrases
# --------------------------------------------------------------------------- #


def test_passphrase_variable_naming():
    assert passphrase_variables("nmsuser") == (
        "NETOPS_SNMP_AUTH_NMSUSER",
        "NETOPS_SNMP_PRIV_NMSUSER",
    )


def test_passphrases_from_the_environment(passphrases):
    desired = FEATURE.build_desired(parse_args())
    assert sorted(desired.secrets) == sorted([AUTH, PRIV])


def test_passphrases_from_a_secrets_manager_map(monkeypatch):
    monkeypatch.setattr(
        "netops.credentials.fetch_json_secret",
        lambda name, region=None: {"nmsuser": {"auth": AUTH, "priv": PRIV}},
    )
    desired = FEATURE.build_desired(parse_args(["--passphrase-secret", "prod/snmp"]))
    assert sorted(desired.secrets) == sorted([AUTH, PRIV])


def test_a_missing_passphrase_names_the_variable(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(CredentialError, match="NETOPS_SNMP_AUTH_NMSUSER"):
        FEATURE.build_desired(parse_args())


def test_a_short_passphrase_is_rejected(monkeypatch):
    monkeypatch.setenv("NETOPS_SNMP_AUTH_NMSUSER", "short")
    monkeypatch.setenv("NETOPS_SNMP_PRIV_NMSUSER", PRIV)
    with pytest.raises(ValueError, match="shorter than"):
        FEATURE.build_desired(parse_args())


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def test_renders_ios_in_dependency_order(passphrases):
    desired = FEATURE.build_desired(parse_args())
    commands = render("snmp", "cisco_ios", desired.keys, [], desired.variables)
    assert commands[0] == "snmp-server view NMS-VIEW iso included"
    assert commands[1] == (
        "snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-POLLERS"
    )
    # No `access` on the user: it inherits its group's, and a user-level ACL
    # would override that.
    assert commands[2] == (
        f"snmp-server user nmsuser NMS-RO v3 auth sha {AUTH} priv aes 128 {PRIV}"
    )
    assert "snmp-server host 10.1.1.50 version 3 priv nmsuser" in commands
    assert "snmp-server location ATL DC1 - row 4" in commands


def test_renders_eos_with_one_token_privacy_and_no_acl(passphrases):
    desired = FEATURE.build_desired(parse_args())
    commands = render("snmp", "arista_eos", desired.keys, [], desired.variables)
    assert f"snmp-server user nmsuser NMS-RO v3 auth sha {AUTH} priv aes128 {PRIV}" in commands
    assert not any("access" in command for command in commands)


def test_passphrases_are_scrubbed_from_what_gets_shown(passphrases):
    desired = FEATURE.build_desired(parse_args())
    commands = render("snmp", "cisco_ios", desired.keys, [], desired.variables)
    shown = [scrub(command, desired.secrets) for command in commands]
    assert not any(AUTH in command or PRIV in command for command in shown)
    assert any("<redacted>" in command for command in shown)


def test_an_injected_location_is_rejected():
    document = {"snmp": {"location": "here\nusername backdoor privilege 15"}}
    from netops.core import InvalidValue

    with pytest.raises(InvalidValue):
        FEATURE.build_desired(parse_args((), document))


# --------------------------------------------------------------------------- #
# an ACL per user
# --------------------------------------------------------------------------- #


MULTI_ACL = {
    "snmp": {
        "acl": "SNMP-POLLERS",
        "groups": [{"name": "NMS-RO", "security": "priv", "read": "NMS-VIEW"}],
        "users": [
            {"name": "nmsuser", "group": "NMS-RO", "auth": "sha", "priv": "aes 128",
             "acl": "SNMP-NMS"},
            {"name": "monuser", "group": "NMS-RO", "auth": "sha", "priv": "aes 128",
             "acl": "SNMP-MONITORING"},
            {"name": "plainuser", "group": "NMS-RO", "auth": "sha", "priv": "aes 128"},
        ],
    },
    "acls": [
        {"name": "SNMP-POLLERS", "permit": ["10.1.1.0/24"]},
        {"name": "SNMP-NMS", "permit": ["10.1.1.50/32"]},
        {"name": "SNMP-MONITORING", "permit": ["10.2.5.0/24"]},
    ],
}


@pytest.fixture
def multi_passphrases(monkeypatch):
    for user in ("NMSUSER", "MONUSER", "PLAINUSER"):
        monkeypatch.setenv(f"NETOPS_SNMP_AUTH_{user}", AUTH)
        monkeypatch.setenv(f"NETOPS_SNMP_PRIV_{user}", PRIV)


def test_each_user_gets_its_own_acl(multi_passphrases):
    entries = FEATURE.build_desired(parse_args((), MULTI_ACL)).variables["entries"]
    assert entries["user:nmsuser"]["access"] == "SNMP-NMS"
    assert entries["user:monuser"]["access"] == "SNMP-MONITORING"


def test_a_user_never_inherits_the_default_acl(multi_passphrases):
    """A user-level ACL overrides its group's on IOS, so a user quietly
    inheriting snmp.acl would defeat the restriction its group imposes."""
    entries = FEATURE.build_desired(parse_args((), MULTI_ACL)).variables["entries"]
    assert entries["user:plainuser"]["access"] is None
    assert entries["group:NMS-RO"]["access"] == "SNMP-POLLERS"  # the group does get it


def test_the_group_keeps_the_default_acl(multi_passphrases):
    entries = FEATURE.build_desired(parse_args((), MULTI_ACL)).variables["entries"]
    assert entries["group:NMS-RO"]["access"] == "SNMP-POLLERS"


def test_a_group_may_name_its_own_acl(multi_passphrases):
    document = {
        "snmp": {
            "acl": "SNMP-POLLERS",
            "groups": [{"name": "NMS-RO", "security": "priv", "acl": "SNMP-NMS"}],
        },
        "acls": MULTI_ACL["acls"],
    }
    entries = FEATURE.build_desired(parse_args((), document)).variables["entries"]
    assert entries["group:NMS-RO"]["access"] == "SNMP-NMS"


def test_each_user_renders_with_its_own_acl(multi_passphrases):
    desired = FEATURE.build_desired(parse_args((), MULTI_ACL))
    commands = render("snmp", "cisco_ios", desired.keys, [], desired.variables)
    assert any(c.endswith("access SNMP-NMS") and "nmsuser" in c for c in commands)
    assert any(c.endswith("access SNMP-MONITORING") and "monuser" in c for c in commands)
    plain = [c for c in commands if "plainuser" in c][0]
    assert "access" not in plain


def test_a_typo_in_an_acl_name_is_caught(multi_passphrases):
    """Binding SNMP to an access list that does not exist is worse than not
    binding it at all."""
    document = {
        "snmp": {"users": [{"name": "nmsuser", "group": "G", "auth": "sha",
                            "acl": "SNMP-NSM"}]},
        "acls": MULTI_ACL["acls"],
    }
    with pytest.raises(StandardsError) as caught:
        FEATURE.build_desired(parse_args((), document))
    assert "SNMP-NSM" in str(caught.value)
    assert "SNMP-NMS" in str(caught.value)  # tells you what is defined


def test_no_acls_section_means_the_acls_live_elsewhere(multi_passphrases):
    """A file that does not define ACLs is not claiming to own them."""
    document = {"snmp": dict(MULTI_ACL["snmp"])}
    entries = FEATURE.build_desired(parse_args((), document)).variables["entries"]
    assert entries["user:nmsuser"]["access"] == "SNMP-NMS"


# --------------------------------------------------------------------------- #
# --rewrite-users: the only way to push what cannot be read back
# --------------------------------------------------------------------------- #


def test_a_matching_user_is_rewritten_when_asked(passphrases):
    """A changed passphrase or a changed per-user ACL is invisible from the
    device, so nothing else would ever trigger the rewrite."""
    desired, ctx = context(
        platform="arista_eos", ignores=("access",), args=["--rewrite-users"]
    )
    add, remove = plan_snmp(parse_snmp(EOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "user:nmsuser" in add
    assert "snmp-server user nmsuser NMS-RO v3" in [e.line for e in remove]


def test_rewrite_users_leaves_groups_and_views_alone(passphrases):
    desired, ctx = context(
        platform="arista_eos", ignores=("access",), args=["--rewrite-users"]
    )
    add, _ = plan_snmp(parse_snmp(EOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert not any(key.startswith(("group:", "view:")) for key in add)


def test_without_the_flag_a_matching_user_is_still_left_alone(passphrases):
    desired, ctx = context(platform="arista_eos", ignores=("access",))
    add, _ = plan_snmp(parse_snmp(EOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "user:nmsuser" not in add


def test_selftest_placeholders_come_from_the_standards_file():
    """Adding a user to the file must not also mean editing this module."""
    from netops.features.snmp import selftest_placeholders
    from netops.standards import Standards

    placeholders = selftest_placeholders(Standards(path="t", document=MULTI_ACL))
    assert "NETOPS_SNMP_AUTH_MONUSER" in placeholders
    assert "NETOPS_SNMP_PRIV_PLAINUSER" in placeholders
    assert all(len(value) >= 8 for value in placeholders.values())



def test_the_group_dependency_rebuilds_its_users(multi_passphrases):
    """A user names its group. Recreating the group without recreating the user
    leaves the user pointing at something that briefly did not exist."""
    desired, ctx = context(document=MULTI_ACL, args=())
    current = parse_snmp(
        "snmp-server group NMS-RO v3 priv read NMS-VIEW access WRONG-ACL\n"
        "snmp-server user nmsuser NMS-RO v3 auth sha x priv aes128 y\n"
    )
    add, remove = plan_snmp(current, desired.keys, MODE_ADD, ctx)

    assert "group:NMS-RO" in add
    assert "user:nmsuser" in add  # dragged along by its group
    # ...and the user is negated before the group it belongs to
    kinds = [entry.data["kind"] for entry in remove]
    assert kinds.index("user") < kinds.index("group")


def test_a_user_in_an_untouched_group_is_left_alone(multi_passphrases):
    desired, ctx = context(document=MULTI_ACL, args=())
    current = parse_snmp(
        # matches what MULTI_ACL asks for: the group takes the section default
        "snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-POLLERS\n"
        "snmp-server user nmsuser NMS-RO v3 auth sha x priv aes128 y\n"
    )
    add, _ = plan_snmp(current, desired.keys, MODE_ADD, ctx)
    assert "group:NMS-RO" not in add
    assert "user:nmsuser" not in add
