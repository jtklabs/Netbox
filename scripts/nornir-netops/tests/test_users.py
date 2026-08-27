import argparse

import pytest

from netops.core import MODE_ADD, MODE_REPLACE, Entry, InvalidValue
from netops.credentials import CredentialError
from netops.features.users import (
    EOS_SAMPLE,
    FEATURE,
    IOS_SAMPLE,
    MIN_PASSWORD_LENGTH,
    parse_users,
    password_variable,
    plan_users,
    resolve_passwords,
)


def parse_args(argv):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    parser.add_argument("--aws-region", default=None)  # supplied by the common parser
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def test_parses_ios_accounts():
    entries = parse_users(IOS_SAMPLE)
    assert [e.key for e in entries] == ["admin", "legacy", "netauto"]


def test_negation_is_by_name_not_by_line():
    """`no username x` removes the account whatever options it carries -- and
    keeps the hash out of the command we build."""
    entries = parse_users(IOS_SAMPLE)
    assert [e.line for e in entries] == [
        "username admin",
        "username legacy",
        "username netauto",
    ]


def test_display_names_the_hash_type_without_the_hash():
    admin = parse_users(IOS_SAMPLE)[0]
    assert admin.shown == "username admin privilege 15 secret 9"
    assert "$9$" not in admin.shown


def test_display_flags_a_weak_type():
    legacy = parse_users(IOS_SAMPLE)[1]
    assert legacy.shown == "username legacy privilege 15 password 7 (weak)"


def test_eos_role_and_nopassword():
    entries = {e.key: e.shown for e in parse_users(EOS_SAMPLE)}
    assert entries["admin"] == "username admin privilege 15 role network-admin secret sha512"
    assert "nopassword" in entries["svc"]


def test_parser_records_the_ssh_key_for_the_planner():
    svc = {e.key: e for e in parse_users(EOS_SAMPLE)}["svc"]
    assert svc.data["ssh_key"] is True


def test_ios_accounts_never_carry_an_ssh_key():
    """IOS has no `username x ssh-key` line, so the flag can never be set."""
    assert not any(e.data["ssh_key"] for e in parse_users(IOS_SAMPLE))


def test_eos_ssh_key_line_merges_into_one_account():
    entries = parse_users(EOS_SAMPLE)
    assert [e.key for e in entries] == ["admin", "legacy", "svc"]  # svc not duplicated
    assert "+ ssh-key" in entries[2].shown


def test_parser_ignores_unrelated_lines():
    assert parse_users("username-prefix thing\nhostname sw1\nusername\n") == []


# --------------------------------------------------------------------------- #
# planning
# --------------------------------------------------------------------------- #


def current():
    return [
        Entry("admin", "username admin", "username admin privilege 15 password 7 (weak)"),
        Entry("legacy", "username legacy", "username legacy privilege 15 password 7 (weak)"),
    ]


def context(**overrides):
    base = {"login_user": "netauto", "platform": "cisco_ios", "variables": {}}
    base.update(overrides)
    return base


def test_existing_account_is_negated_then_rewritten():
    """The type 7 case: it cannot be overwritten in place."""
    add, remove = plan_users(current(), ["admin"], MODE_ADD, context())
    assert add == ["admin"]
    assert [e.line for e in remove] == ["username admin"]


def test_missing_account_is_created_without_a_negation():
    add, remove = plan_users(current(), ["netauto"], MODE_ADD, context())
    assert add == ["netauto"]
    assert remove == []


def test_add_mode_leaves_unmanaged_accounts_alone():
    _, remove = plan_users(current(), ["admin"], MODE_ADD, context())
    assert "username legacy" not in [e.line for e in remove]


def test_replace_purges_unmanaged_accounts():
    add, remove = plan_users(current(), ["admin"], MODE_REPLACE, context())
    assert add == ["admin"]
    assert [e.line for e in remove] == ["username admin", "username legacy"]


def test_replace_never_purges_the_account_we_are_logged_in_as():
    existing = current() + [Entry("netauto", "username netauto")]
    _, remove = plan_users(existing, ["admin"], MODE_REPLACE, context(login_user="netauto"))
    assert "username netauto" not in [e.line for e in remove]


def test_self_purge_can_be_opted_into():
    existing = current() + [Entry("netauto", "username netauto")]
    _, remove = plan_users(
        existing,
        ["admin"],
        MODE_REPLACE,
        context(login_user="netauto", variables={"allow_remove_self": True}),
    )
    assert "username netauto" in [e.line for e in remove]


def test_only_missing_leaves_existing_accounts_untouched():
    add, remove = plan_users(
        current(), ["admin", "netauto"], MODE_ADD, context(variables={"only_missing": True})
    )
    assert add == ["netauto"]  # admin already exists, so it is not rotated
    assert remove == []


def test_only_missing_is_a_no_op_when_everything_exists():
    add, remove = plan_users(
        current(), ["admin"], MODE_ADD, context(variables={"only_missing": True})
    )
    assert (add, remove) == ([], [])


def test_usernames_are_case_sensitive():
    add, remove = plan_users(current(), ["Admin"], MODE_ADD, context())
    assert add == ["Admin"]
    assert remove == []  # 'admin' is a different account


# --------------------------------------------------------------------------- #
# passwords
# --------------------------------------------------------------------------- #


def test_password_variable_naming():
    assert password_variable("admin") == "NETOPS_PW_ADMIN"
    assert password_variable("net-auto.svc") == "NETOPS_PW_NET_AUTO_SVC"


def test_password_from_the_environment(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "envpassword")
    assert resolve_passwords(["admin"], parse_args(["-U", "admin"])) == {
        "admin": "envpassword"
    }


def test_password_from_a_secrets_manager_map(monkeypatch):
    monkeypatch.setattr(
        "netops.credentials.fetch_json_secret",
        lambda name, region=None: {"admin": "awspassword", "other": "x"},
    )
    args = parse_args(["-U", "admin", "--password-secret", "prod/network/local-users"])
    assert resolve_passwords(["admin"], args) == {"admin": "awspassword"}


def test_secrets_manager_beats_the_environment(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "envpassword")
    monkeypatch.setattr(
        "netops.credentials.fetch_json_secret", lambda name, region=None: {"admin": "awspassword"}
    )
    args = parse_args(["-U", "admin", "--password-secret", "s"])
    assert resolve_passwords(["admin"], args)["admin"] == "awspassword"


def test_missing_password_names_the_variable_to_set(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(CredentialError, match=r"NETOPS_PW_ADMIN"):
        resolve_passwords(["admin"], parse_args(["-U", "admin"]))


def test_password_prompt_requires_confirmation(monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(["typedpassword", "typo-instead"])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    with pytest.raises(ValueError, match="did not match"):
        resolve_passwords(["admin"], parse_args(["-U", "admin"]))


def test_short_password_is_rejected(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "short")
    with pytest.raises(ValueError, match=f"shorter than {MIN_PASSWORD_LENGTH}"):
        resolve_passwords(["admin"], parse_args(["-U", "admin"]))


def test_a_hash_without_hash_type_is_rejected(monkeypatch):
    """Sending a hash as if it were plaintext would set the password to the
    literal hash string -- and the platforms spell the keyword differently."""
    monkeypatch.setenv("NETOPS_PW_ADMIN", "$9$saLt$0123456789abcdef")
    with pytest.raises(ValueError, match="--hash-type"):
        resolve_passwords(["admin"], parse_args(["-U", "admin"]))


def test_a_hash_with_hash_type_is_accepted(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "$9$saLt$0123456789abcdef")
    args = parse_args(["-U", "admin", "--hash-type", "9"])
    assert resolve_passwords(["admin"], args)["admin"].startswith("$9$")


def test_password_with_whitespace_is_rejected(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "has a space")
    with pytest.raises(InvalidValue, match="whitespace"):
        resolve_passwords(["admin"], parse_args(["-U", "admin"]))


# --------------------------------------------------------------------------- #
# desired state
# --------------------------------------------------------------------------- #


def test_build_desired_collects_names_and_secrets(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "adminpassword")
    monkeypatch.setenv("NETOPS_PW_NETAUTO", "netautopassword")
    desired = FEATURE.build_desired(parse_args(["-U", "admin,netauto", "-U", "admin"]))
    assert desired.keys == ["admin", "netauto"]  # deduped
    assert sorted(desired.secrets) == ["adminpassword", "netautopassword"]
    assert desired.variables["privilege"] == "15"


def test_build_desired_rejects_an_injected_username(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", "adminpassword")
    with pytest.raises(InvalidValue, match="username"):
        FEATURE.build_desired(parse_args(["-U", "admin\nprivilege 15"]))


def test_rendered_commands_negate_before_writing(monkeypatch):
    from netops.core import render

    monkeypatch.setenv("NETOPS_PW_ADMIN", "adminpassword")
    desired = FEATURE.build_desired(parse_args(["-U", "admin", "--privilege", "15"]))
    commands = render("users", "cisco_ios", ["admin"], [Entry("admin", "username admin")],
                      desired.variables)
    assert commands == [
        "no username admin",
        "username admin privilege 15 secret adminpassword",
    ]


def test_ios_algorithm_type(monkeypatch):
    from netops.core import render

    monkeypatch.setenv("NETOPS_PW_ADMIN", "adminpassword")
    desired = FEATURE.build_desired(parse_args(["-U", "admin", "--algorithm", "scrypt"]))
    assert render("users", "cisco_ios", ["admin"], [], desired.variables) == [
        "username admin privilege 15 algorithm-type scrypt secret adminpassword"
    ]


def test_ios_prehashed(monkeypatch):
    from netops.core import render

    monkeypatch.setenv("NETOPS_PW_ADMIN", "$9$saLt$0123456789abcdef")
    desired = FEATURE.build_desired(parse_args(["-U", "admin", "--hash-type", "9"]))
    assert render("users", "cisco_ios", ["admin"], [], desired.variables) == [
        "username admin privilege 15 secret 9 $9$saLt$0123456789abcdef"
    ]


def test_eos_role_and_secret(monkeypatch):
    from netops.core import render

    monkeypatch.setenv("NETOPS_PW_ADMIN", "adminpassword")
    desired = FEATURE.build_desired(
        parse_args(["-U", "admin", "--role", "network-admin"])
    )
    assert render("users", "arista_eos", ["admin"], [], desired.variables) == [
        "username admin privilege 15 role network-admin secret adminpassword"
    ]


def test_eos_prehashed_sha512(monkeypatch):
    from netops.core import render

    monkeypatch.setenv("NETOPS_PW_ADMIN", "$6$saLt$0123456789")
    desired = FEATURE.build_desired(parse_args(["-U", "admin", "--hash-type", "sha512"]))
    assert render("users", "arista_eos", ["admin"], [], desired.variables) == [
        "username admin privilege 15 secret sha512 $6$saLt$0123456789"
    ]


# --------------------------------------------------------------------------- #
# ssh keys -- an alternative credential that bypasses the managed password
# --------------------------------------------------------------------------- #


def keyed():
    return [
        Entry(
            "admin",
            "username admin",
            "username admin privilege 15 secret sha512 + ssh-key",
            data={"ssh_key": True},
        )
    ]


def test_ssh_key_is_negated_before_the_account():
    """Order matters: the key negation is only valid while the account exists,
    which makes it correct whether or not `no username x` cascades."""
    add, remove = plan_users(keyed(), ["admin"], MODE_ADD, context())
    assert [e.line for e in remove] == ["username admin ssh-key", "username admin"]
    assert add == ["admin"]


def test_only_missing_strips_the_key_without_rotating():
    add, remove = plan_users(
        keyed(), ["admin"], MODE_ADD, context(variables={"only_missing": True})
    )
    assert add == []  # the password is left alone, as asked
    assert [e.line for e in remove] == ["username admin ssh-key"]


def test_replace_strips_the_key_before_purging_an_unmanaged_account():
    _, remove = plan_users(keyed(), ["netauto"], MODE_REPLACE, context(login_user="root"))
    assert [e.line for e in remove] == ["username admin ssh-key", "username admin"]


def test_a_keyless_account_gets_no_key_negation():
    add, remove = plan_users(current(), ["admin"], MODE_ADD, context())
    assert [e.line for e in remove] == ["username admin"]


def test_rendered_key_negation(monkeypatch):
    from netops.core import render

    monkeypatch.setenv("NETOPS_PW_ADMIN", "adminpassword")
    desired = FEATURE.build_desired(parse_args(["-U", "admin"]))
    add, remove = plan_users(keyed(), ["admin"], MODE_ADD, context())
    assert render("users", "arista_eos", add, remove, desired.variables) == [
        "no username admin ssh-key",
        "no username admin",
        "username admin privilege 15 secret adminpassword",
    ]
