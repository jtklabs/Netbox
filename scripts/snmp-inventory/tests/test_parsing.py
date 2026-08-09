"""Parsing of net-snmp output — the layer everything else is built on.

The cases here are the ones that actually bite in the field: values that span
several lines, MACs printed as Hex-STRING, empty subtrees that must vanish
rather than become empty strings, and enum values printed numerically.
"""

from __future__ import annotations

import sys

from snmpinv.snmp import Credential, collect_column, column_index, parse_varbinds


def test_simple_varbind():
    binds = parse_varbinds('.1.3.6.1.2.1.1.5.0 = STRING: "core-sw-01"')
    assert len(binds) == 1
    assert binds[0].oid == "1.3.6.1.2.1.1.5.0"
    assert binds[0].type == "STRING"
    assert binds[0].value == "core-sw-01"


def test_multiline_value_is_joined():
    """Cisco's sysDescr is a paragraph. A parser that stops at the first line
    truncates it, and the software version is on line one only by luck."""
    text = (
        '.1.3.6.1.2.1.1.1.0 = STRING: "Cisco IOS Software [Amsterdam], Catalyst L3 Switch\n'
        "Software (CAT9K_IOSXE), Version 17.03.04a, RELEASE SOFTWARE (fc3)\n"
        'Copyright (c) 1986-2021 by Cisco Systems, Inc."\n'
        ".1.3.6.1.2.1.1.3.0 = Timeticks: (123456700) 14 days, 6:56:07.00"
    )
    binds = parse_varbinds(text)
    assert len(binds) == 2
    assert "Version 17.03.04a" in binds[0].value
    assert binds[0].value.count("\n") == 2
    # Timeticks keep only the raw tick count.
    assert binds[1].value == "123456700"


def test_hex_string_becomes_colon_mac():
    binds = parse_varbinds(".1.3.6.1.2.1.2.2.1.6.1 = Hex-STRING: AC F2 C5 11 22 01 ")
    assert binds[0].value == "AC:F2:C5:11:22:01"


def test_empty_subtree_markers_are_dropped():
    """"No Such Object" must not become a varbind with a junk value."""
    text = (
        ".1.3.6.1.2.1.47.1.1.1.1.13.1 = No Such Object available on this agent at this OID\n"
        '.1.3.6.1.2.1.1.5.0 = STRING: "sw1"'
    )
    binds = parse_varbinds(text)
    assert [b.oid for b in binds] == ["1.3.6.1.2.1.1.5.0"]


def test_oid_values_lose_leading_dot():
    binds = parse_varbinds(".1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.9.1.2494")
    assert binds[0].value == "1.3.6.1.4.1.9.1.2494"


def test_integer_and_gauge():
    binds = parse_varbinds(
        ".1.3.6.1.2.1.2.2.1.3.1 = INTEGER: 6\n.1.3.6.1.2.1.31.1.1.1.15.1 = Gauge32: 1000"
    )
    assert binds[0].as_int() == 6
    assert binds[1].as_int() == 1000


def test_empty_string_value():
    binds = parse_varbinds('.1.3.6.1.2.1.31.1.1.1.18.1 = STRING: ""')
    assert binds[0].value == ""


def test_column_index_and_collect_column():
    binds = parse_varbinds(
        '.1.3.6.1.2.1.31.1.1.1.1.1 = STRING: "Gi1/0/1"\n'
        '.1.3.6.1.2.1.31.1.1.1.1.2 = STRING: "Gi1/0/2"\n'
        '.1.3.6.1.2.1.31.1.1.1.18.1 = STRING: "uplink"'
    )
    assert column_index("1.3.6.1.2.1.31.1.1.1.1.7", "1.3.6.1.2.1.31.1.1.1.1") == "7"
    assert column_index("1.3.6.1.2.1.2.2.1.1.1", "1.3.6.1.2.1.31.1.1.1.1") is None
    names = collect_column(binds, "1.3.6.1.2.1.31.1.1.1.1")
    assert sorted(names) == ["1", "2"]
    assert names["2"].value == "Gi1/0/2"


def test_column_index_does_not_match_sibling_columns():
    """1.3.6.1...1.15 must not be read as a row of column 1. The prefix has to
    end at a dot, or ifHighSpeed rows get collected as ifName rows."""
    assert column_index("1.3.6.1.2.1.31.1.1.1.15.1", "1.3.6.1.2.1.31.1.1.1.1") is None


class TestCredentialConfig:
    def test_authpriv_level_is_derived(self):
        cred = Credential("c", "netops", auth_passphrase="a", priv_passphrase="p")
        assert cred.level() == "authPriv"

    def test_auth_only(self):
        cred = Credential("c", "netops", auth_passphrase="a")
        assert cred.level() == "authNoPriv"

    def test_no_auth(self):
        assert Credential("c", "netops").level() == "noAuthNoPriv"

    def test_explicit_level_wins(self):
        cred = Credential("c", "netops", auth_passphrase="a", priv_passphrase="p",
                          security_level="authNoPriv")
        assert cred.level() == "authNoPriv"

    def test_config_text_contains_passphrases_and_no_cli_flags(self):
        cred = Credential("c", "netops", auth_protocol="SHA-256", auth_passphrase="secret-auth",
                          priv_protocol="AES", priv_passphrase="secret-priv")
        text = cred.config_text()
        assert "defSecurityName netops" in text
        assert "defAuthType SHA-256" in text
        assert "defAuthPassphrase secret-auth" in text
        assert "defPrivPassphrase secret-priv" in text
        assert "defSecurityLevel authPriv" in text


def test_credential_session_writes_private_config(tmp_path):
    """The passphrases must land in a 0600 file and be gone afterwards."""
    import os
    import stat

    from snmpinv.snmp import CredentialSession

    cred = Credential("c", "netops", auth_passphrase="a-secret", priv_passphrase="p-secret")
    session = CredentialSession(cred)
    with session:
        config_dir = session._dir
        config_path = os.path.join(config_dir, "snmp.conf")
        assert os.path.exists(config_path)
        mode = stat.S_IMODE(os.stat(config_path).st_mode)
        assert mode == 0o600, f"snmp.conf mode is {oct(mode)}, expected 0600"
        env = session._env()
        assert env["SNMPCONFPATH"] == config_dir
        # The operator's own ~/.snmp/snmp.conf must not be in the search path,
        # or a stray community/user there could change what a scan really used.
        assert os.path.expanduser("~/.snmp") not in env["SNMPCONFPATH"]
        assert "a-secret" in open(config_path).read()
    assert not os.path.exists(config_dir)
