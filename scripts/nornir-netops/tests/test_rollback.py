import json

import pytest

from netops.core import Entry
from netops.rollback import (
    Journal,
    Reversal,
    RollbackError,
    default_reversal,
    journal_directory,
    latest,
    load,
)


def entry(key, line, **data):
    return Entry(key=key, line=line, data=data)


# --------------------------------------------------------------------------- #
# working out the reversal
# --------------------------------------------------------------------------- #


def test_what_was_added_is_negated():
    reversal = default_reversal(["ntp server 10.50.0.10"], [], [])
    assert reversal.commands == ["no ntp server 10.50.0.10"]


def test_undoing_a_negation_is_not_a_double_negative():
    """`no no logging host ...` is not a command. Putting the line back is the
    current-state replay's job."""
    current = [entry("host:10.9.9.9", "logging host 10.9.9.9")]
    reversal = default_reversal(["no logging host 10.9.9.9"], current, [])
    assert reversal.commands == ["logging host 10.9.9.9"]


def test_a_scalar_is_restored_by_replaying_what_was_there():
    """`logging trap` is never negated -- setting it replaces it -- so the only
    way back is to send the old severity again."""
    current = [entry("trap:notifications", "logging trap notifications")]
    reversal = default_reversal(["logging trap informational"], current, [])
    assert reversal.commands == [
        "no logging trap informational",
        "logging trap notifications",
    ]


def test_the_order_is_negate_then_restore():
    current = [entry("a", "line a")]
    reversal = default_reversal(["line b"], current, [])
    assert reversal.commands.index("no line b") < reversal.commands.index("line a")


def test_a_command_carrying_a_secret_is_not_recorded():
    """Undoing it would mean writing the secret into a journal on disk, and the
    value it replaced is unreadable anyway."""
    reversal = default_reversal(
        ["ntp authentication-key 1 md5 s3cr3t-key"], [], [], secrets=["s3cr3t-key"]
    )
    assert reversal.commands == []
    assert "s3cr3t-key" not in json.dumps(reversal.unsupported)
    assert "carries a secret" in reversal.unsupported[0]


def test_an_unrestorable_line_is_never_replayed():
    """`username admin` on its own would create the account with no password."""
    current = [entry("admin", "username admin", restorable=False)]
    reversal = default_reversal(["username admin secret x"], current, [])
    assert "username admin" not in reversal.commands


def test_removing_something_unrestorable_is_reported():
    removed = [
        Entry("nmsuser", "snmp-server user nmsuser G v3",
              display="snmp-server user nmsuser (auth md5)", data={"restorable": False})
    ]
    reversal = default_reversal([], [], removed)
    assert reversal.unsupported == ["snmp-server user nmsuser (auth md5)"]


def test_a_reversal_with_no_commands_is_not_possible():
    assert Reversal().possible is False
    assert Reversal(commands=["no x"]).possible is True


# --------------------------------------------------------------------------- #
# the journal
# --------------------------------------------------------------------------- #


def test_a_journal_round_trips(tmp_path):
    journal = Journal(feature="ntp", mode="add")
    journal.add("sw1", {"hostname": "10.1.1.1", "platform": "cisco_ios",
                        "rollback": ["no ntp server 10.50.0.10"]})
    path = journal.save(tmp_path)

    reloaded = load(path)
    assert reloaded.feature == "ntp"
    assert reloaded.devices["sw1"]["rollback"] == ["no ntp server 10.50.0.10"]
    assert reloaded.recorded_at


def test_nothing_changed_writes_no_journal(tmp_path):
    assert Journal(feature="ntp").save(tmp_path) is None
    assert list(tmp_path.glob("*.json")) == []


def test_a_journal_is_not_world_readable(tmp_path):
    """It names devices and their configuration."""
    journal = Journal(feature="ntp")
    journal.add("sw1", {"rollback": ["no x"]})
    path = journal.save(tmp_path)
    assert (path.stat().st_mode & 0o077) == 0


def test_devices_with_nothing_to_undo_are_not_offered():
    journal = Journal(devices={"sw1": {"rollback": ["no x"]}, "sw2": {"rollback": []}})
    assert list(journal.restorable) == ["sw1"]


def test_the_latest_journal_is_the_most_recent(tmp_path):
    for stamp in ("20260101T000000Z", "20260301T000000Z", "20260201T000000Z"):
        (tmp_path / f"{stamp}-ntp.json").write_text("{}", encoding="utf-8")
    assert latest(tmp_path).name.startswith("20260301")


def test_no_journal_at_all_says_where_they_come_from(tmp_path):
    with pytest.raises(RollbackError, match="written by each --apply"):
        latest(tmp_path)


def test_a_missing_journal_is_named(tmp_path):
    with pytest.raises(RollbackError, match="no rollback journal at"):
        load(tmp_path / "nope.json")


def test_something_that_is_not_a_journal(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"something": "else"}', encoding="utf-8")
    with pytest.raises(RollbackError, match="does not look like"):
        load(path)


def test_the_directory_can_come_from_the_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("NETOPS_ROLLBACK_DIR", str(tmp_path / "elsewhere"))
    assert journal_directory(None, tmp_path) == tmp_path / "elsewhere"


# --------------------------------------------------------------------------- #
# the features that cannot use the default
# --------------------------------------------------------------------------- #


def test_acl_is_rebuilt_with_the_entries_it_had():
    """Negating the commands one at a time would `no` the context line and
    delete the whole list."""
    from netops.features.acl import parse_acls, reverse

    current = parse_acls(
        "ip access-list standard SNMP-POLLERS\n 10 permit 10.1.1.0 0.0.0.255\n"
    )
    reversal = reverse(
        ["no ip access-list standard SNMP-POLLERS", "ip access-list standard SNMP-POLLERS"],
        current,
        [],
        {"platform": "cisco_ios", "added": ["SNMP-POLLERS"]},
    )
    assert reversal.commands == [
        "no ip access-list standard SNMP-POLLERS",
        "ip access-list standard SNMP-POLLERS",
        " permit 10.1.1.0 0.0.0.255",
    ]


def test_an_acl_that_did_not_exist_is_simply_removed():
    from netops.features.acl import reverse

    reversal = reverse([], [], [], {"platform": "cisco_ios", "added": ["NEW-ACL"]})
    assert reversal.commands == ["no ip access-list standard NEW-ACL"]


def test_a_banner_is_put_back_in_the_templates_own_wrapper():
    from netops.features.banner import parse_banners, reverse

    current = parse_banners("banner motd ^C\n  the old notice\n^C\n")
    reversal = reverse(
        [], current, [], {"platform": "cisco_ios", "variables": {"delimiter": None},
                          "added": ["motd"]}
    )
    assert reversal.commands[0] == "banner motd ^C"
    assert "  the old notice" in reversal.commands
    assert reversal.commands[-1] == "^C"


def test_a_banner_that_did_not_exist_is_removed():
    from netops.features.banner import reverse

    reversal = reverse(
        [], [], [], {"platform": "cisco_ios", "variables": {"delimiter": None},
                     "added": ["motd"]}
    )
    assert reversal.commands == ["no banner motd"]


def test_nac_negates_inside_the_interface_it_changed():
    from netops.features.nac import reverse

    reversal = reverse(
        [], [], [], {"variables": {"missing": {"GigabitEthernet1/0/2": ["mab", "dot1x pae authenticator"]}}}
    )
    assert reversal.commands == [
        "interface GigabitEthernet1/0/2",
        " no mab",
        " no dot1x pae authenticator",
    ]


def test_users_declares_itself_irreversible():
    """A password hash is not a password."""
    from netops.features.users import FEATURE

    assert FEATURE.reversible is False
    assert "cannot be undone" in FEATURE.rollback_note
