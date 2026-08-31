import argparse

import pytest

from netops.core import MODE_ADD, render
from netops.features.nac import (
    EOS_SAMPLE,
    FEATURE,
    IOS_SAMPLE,
    in_scope,
    is_physical,
    parse_interfaces,
    plan_nac,
    required_lines,
)
from netops.standards import Standards, StandardsError

DOCUMENT = {"nac": {"policy": "NAC-POLICY", "scope": {"exclude_description": ["uplink"]}}}


def parse_args(argv=(), document=None):
    parser = argparse.ArgumentParser()
    FEATURE.add_arguments(parser)
    args = parser.parse_args(list(argv))
    args.standards = Standards(path="test", document=document or DOCUMENT)
    return args


def context(platform="cisco_ios", document=None, argv=(), notes=None):
    desired = FEATURE.build_desired(parse_args(argv, document))
    return desired, {
        "platform": platform,
        "variables": desired.variables,
        "advisories": [],
        "notes": notes if notes is not None else [],
    }


# --------------------------------------------------------------------------- #
# reading interfaces
# --------------------------------------------------------------------------- #


def test_each_interface_becomes_an_entry_with_its_lines():
    entries = {e.data["name"]: e for e in parse_interfaces(IOS_SAMPLE)}
    assert "GigabitEthernet1/0/1" in entries
    assert "switchport mode access" in entries["GigabitEthernet1/0/1"].data["lines"]


def test_mode_description_and_shutdown_are_picked_out():
    entries = {e.data["name"]: e.data for e in parse_interfaces(IOS_SAMPLE)}
    assert entries["GigabitEthernet1/0/1"]["mode"] == "access"
    assert entries["GigabitEthernet1/0/48"]["mode"] == "trunk"
    assert entries["GigabitEthernet1/0/48"]["description"] == "uplink to core"
    assert entries["GigabitEthernet1/0/3"]["shutdown"] is True


def test_eos_indentation_is_handled():
    """EOS indents interface config by three spaces, IOS by one."""
    entries = {e.data["name"]: e.data for e in parse_interfaces(EOS_SAMPLE)}
    assert entries["Ethernet1"]["mode"] == "access"
    assert "dot1x pae authenticator" in entries["Ethernet1"]["lines"]


@pytest.mark.parametrize(
    "name,physical",
    [
        ("GigabitEthernet1/0/1", True),
        ("Ethernet1", True),
        ("TenGigabitEthernet1/1/1", True),
        ("Vlan10", False),
        ("Loopback0", False),
        ("Port-channel1", False),
        ("Management1", False),
        ("Tunnel0", False),
    ],
)
def test_what_counts_as_a_port(name, physical):
    assert is_physical(name) is physical


def test_an_empty_config_is_not_a_crash():
    assert parse_interfaces("") == []


# --------------------------------------------------------------------------- #
# scope -- which ports the standard applies to
# --------------------------------------------------------------------------- #


RULES = {"access_only": True, "skip_shutdown": True, "exclude_description": ["uplink"]}


def entry_named(name):
    return {e.data["name"]: e for e in parse_interfaces(IOS_SAMPLE)}[name]


def test_an_access_port_is_in_scope():
    included, _ = in_scope(entry_named("GigabitEthernet1/0/1"), RULES)
    assert included is True


@pytest.mark.parametrize(
    "name,reason",
    [
        # the uplink is caught by its description first, which is also correct
        ("GigabitEthernet1/0/48", "excluded by description"),
        ("GigabitEthernet1/0/3", "shut down"),
        ("Vlan10", "not a physical port"),
    ],
)
def test_what_is_left_alone_and_why(name, reason):
    included, why = in_scope(entry_named(name), RULES)
    assert included is False
    assert reason in why


def test_a_trunk_without_a_telling_description_is_still_out_of_scope():
    included, why = in_scope(entry_named("GigabitEthernet1/0/48"), {"access_only": True})
    assert included is False
    assert "not an access port (mode trunk)" in why


def test_a_description_can_take_a_port_out_of_scope():
    """An uplink is not a user port, whatever its switchport mode says."""
    entries = parse_interfaces(
        "interface GigabitEthernet1/0/47\n description uplink to distribution\n"
        " switchport mode access\n"
    )
    included, why = in_scope(entries[0], RULES)
    assert included is False
    assert "description" in why


def test_a_name_pattern_can_take_a_port_out_of_scope():
    rules = {**RULES, "exclude": ["TenGigabit*"]}
    entries = parse_interfaces(
        "interface TenGigabitEthernet1/1/1\n switchport mode access\n"
    )
    assert in_scope(entries[0], rules)[0] is False


def test_trunks_can_be_opted_into():
    included, _ = in_scope(entry_named("GigabitEthernet1/0/48"), {"access_only": False})
    assert included is True


# --------------------------------------------------------------------------- #
# the audit
# --------------------------------------------------------------------------- #


def test_the_required_block_comes_from_the_template():
    """One copy of the standard, and it is the one that gets pushed."""
    desired, ctx = context()
    lines = required_lines("cisco_ios", desired.variables)
    assert "access-session port-control auto" in lines
    assert "service-policy type control subscriber NAC-POLICY" in lines
    assert not any(line.startswith("interface ") for line in lines)


def test_a_compliant_port_is_not_reported():
    desired, ctx = context()
    add, _ = plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert "GigabitEthernet1/0/1" not in add


def test_a_port_missing_lines_is_reported_with_only_what_it_lacks():
    desired, ctx = context()
    add, _ = plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert add == ["GigabitEthernet1/0/2"]

    missing = desired.variables["missing"]["GigabitEthernet1/0/2"]
    assert "access-session closed" in missing
    assert "mab" not in missing  # it already has that one
    assert "switchport mode access" not in missing


def test_out_of_scope_ports_are_never_reported():
    desired, ctx = context()
    add, _ = plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert not any(name in add for name in ("GigabitEthernet1/0/3", "GigabitEthernet1/0/48", "Vlan10"))


def test_nothing_is_ever_removed():
    """An audit adds what is missing. Stripping a line somebody added on
    purpose is not this feature's business."""
    desired, ctx = context()
    _, remove = plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert remove == []


def test_the_count_is_reported():
    notes = []
    desired, ctx = context(notes=notes)
    plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    assert notes == ["1 of 2 access port(s) are missing NAC configuration"]


def test_a_fully_compliant_switch_says_how_many_it_checked():
    """A note, not an advisory: a clean audit is compliant, not 'needs
    attention', and must not exit non-zero."""
    notes = []
    desired, ctx = context(notes=notes)
    compliant = IOS_SAMPLE.replace(
        "interface GigabitEthernet1/0/2\n description User port\n switchport mode access\n mab\n",
        "",
    )
    add, _ = plan_nac(parse_interfaces(compliant), desired.keys, MODE_ADD, ctx)
    assert add == []
    assert notes == ["1 access port(s) checked, all compliant"]


# --------------------------------------------------------------------------- #
# rendering the fix
# --------------------------------------------------------------------------- #


def test_the_fix_is_the_interface_and_only_the_missing_lines():
    desired, ctx = context()
    add, _ = plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    commands = render("nac", "cisco_ios", add, [], desired.variables)

    assert commands[0] == "interface GigabitEthernet1/0/2"
    assert " access-session closed" in commands
    assert not any(c.strip() == "mab" for c in commands)  # already present


def test_the_policy_name_reaches_the_block():
    desired, _ = context(document={"nac": {"policy": "DOT1X-POLICY"}})
    assert "service-policy type control subscriber DOT1X-POLICY" in required_lines(
        "cisco_ios", desired.variables
    )


def test_no_policy_means_no_service_policy_line():
    desired, _ = context(document={"nac": {}})
    assert not any(
        line.startswith("service-policy") for line in required_lines("cisco_ios", desired.variables)
    )


def test_eos_has_its_own_block():
    desired, _ = context(platform="arista_eos")
    lines = required_lines("arista_eos", desired.variables)
    assert "dot1x port-control auto" in lines
    assert not any("access-session" in line for line in lines)  # that is IOS


def test_an_injected_policy_name_is_rejected():
    from netops.core import InvalidValue

    with pytest.raises(InvalidValue):
        FEATURE.build_desired(parse_args((), {"nac": {"policy": "X\n username backdoor"}}))


def test_scope_must_be_lists():
    with pytest.raises(StandardsError, match="must be a list"):
        FEATURE.build_desired(parse_args((), {"nac": {"scope": {"exclude": "Ten"}}}))


def test_verification_re_runs_the_planner():
    """The desired set is every in-scope port, which is not known until the
    device has been read."""
    assert FEATURE.verify_with_plan is True
    assert FEATURE.build_desired(parse_args()).keys == []
