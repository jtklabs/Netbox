import pytest

from netops.core import (
    MODE_ADD,
    MODE_REPLACE,
    Entry,
    InvalidValue,
    canonical_platform,
    normalize,
    plan_changes,
    render,
    validate_address,
)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("10.1.1.1", "10.1.1.1"),
        ("010.001.001.001", "10.1.1.1"),  # zero padded is the same server
        (" 10.1.1.1 ", "10.1.1.1"),
        ("2001:0db8::0001", "2001:db8::1"),
        ("Time.Example.NET", "time.example.net"),
    ],
)
def test_normalize(value, expected):
    assert normalize(value) == expected


@pytest.mark.parametrize("value", ["ios", "IOS-XE", "cisco_ios", "Cisco_IOS"])
def test_canonical_platform_ios(value):
    assert canonical_platform(value) == "cisco_ios"


def test_canonical_platform_blank():
    assert canonical_platform(None) == ""


@pytest.mark.parametrize(
    "value",
    [
        "10.1.1.1\nusername backdoor privilege 15",  # command injection
        "10.1.1.1 ; reload",
        "-leading-dash",
        "",
        "   ",
        "a" * 260,
    ],
)
def test_validate_address_rejects_junk(value):
    with pytest.raises(InvalidValue):
        validate_address(value)


@pytest.mark.parametrize("value", ["10.1.1.1", "time.example.net", "2001:db8::1"])
def test_validate_address_accepts(value):
    assert validate_address(value) == value


def _current():
    return [
        Entry("10.10.10.1", "ntp server 10.10.10.1"),
        Entry("10.10.10.2", "ntp server 10.10.10.2 prefer"),
    ]


def test_plan_add_only_adds_missing():
    add, remove = plan_changes(_current(), ["10.10.10.1", "10.20.20.1"], MODE_ADD)
    assert add == ["10.20.20.1"]
    assert remove == []


def test_plan_add_is_idempotent():
    add, remove = plan_changes(_current(), ["10.10.10.1", "10.10.10.2"], MODE_ADD)
    assert (add, remove) == ([], [])


def test_plan_replace_removes_the_rest():
    add, remove = plan_changes(_current(), ["10.10.10.1", "10.20.20.1"], MODE_REPLACE)
    assert add == ["10.20.20.1"]
    assert [e.line for e in remove] == ["ntp server 10.10.10.2 prefer"]


def test_plan_replace_keeps_everything_when_desired_matches():
    add, remove = plan_changes(_current(), ["10.10.10.1", "10.10.10.2"], MODE_REPLACE)
    assert (add, remove) == ([], [])


def test_plan_compares_on_normalized_addresses():
    current = [Entry("010.010.010.001", "ntp server 010.010.010.001")]
    add, remove = plan_changes(current, ["10.10.10.1"], MODE_REPLACE)
    assert (add, remove) == ([], [])


def test_plan_replace_dedupes_identical_lines():
    current = [
        Entry("10.10.10.9", "ntp server 10.10.10.9"),
        Entry("10.10.10.9", "ntp server 10.10.10.9"),
    ]
    _, remove = plan_changes(current, ["10.1.1.1"], MODE_REPLACE)
    assert len(remove) == 1


VARIABLES = {"vrf": None, "prefer": None, "source": None, "iburst": True}


def test_render_ios_plain():
    assert render("ntp", "cisco_ios", ["10.1.1.1"], [], VARIABLES) == [
        "ntp server 10.1.1.1"
    ]


def test_render_ios_options_and_removal():
    commands = render(
        "ntp",
        "cisco_ios",
        ["10.1.1.1", "10.1.1.2"],
        [Entry("10.9.9.9", "ntp server vrf MGMT 10.9.9.9 source Vlan10")],
        {"vrf": "MGMT", "prefer": "10.1.1.2", "source": "Vlan10", "iburst": True},
    )
    assert commands == [
        "ntp server vrf MGMT 10.1.1.1 source Vlan10",
        "ntp server vrf MGMT 10.1.1.2 source Vlan10 prefer",
        # removal negates the device's own line, options and all
        "no ntp server vrf MGMT 10.9.9.9 source Vlan10",
    ]


def test_render_eos_uses_iburst():
    assert render("ntp", "arista_eos", ["10.1.1.1"], [], VARIABLES) == [
        "ntp server 10.1.1.1 iburst"
    ]
    assert render("ntp", "arista_eos", ["10.1.1.1"], [], {**VARIABLES, "iburst": False}) == [
        "ntp server 10.1.1.1"
    ]


def test_render_emits_no_blank_lines():
    commands = render("ntp", "cisco_ios", ["10.1.1.1"], [], VARIABLES)
    assert all(command.strip() for command in commands)


def test_render_unknown_platform_raises():
    from jinja2 import TemplateNotFound

    with pytest.raises(TemplateNotFound):
        render("ntp", "juniper_junos", ["10.1.1.1"], [], VARIABLES)


def test_template_dir_can_be_overridden(tmp_path, monkeypatch):
    """A non-editable install points $NETOPS_TEMPLATES at a copy."""
    from netops.core import template_dir

    (tmp_path / "cisco_ios").mkdir()
    (tmp_path / "cisco_ios" / "ntp.j2").write_text(
        "{% for s in add %}custom {{ s }}\n{% endfor %}", encoding="utf-8"
    )
    monkeypatch.setenv("NETOPS_TEMPLATES", str(tmp_path))

    assert template_dir() == tmp_path
    assert render("ntp", "cisco_ios", ["10.1.1.1"], [], {}) == ["custom 10.1.1.1"]


def test_template_dir_defaults_to_the_checkout(monkeypatch):
    from netops.core import template_dir

    monkeypatch.delenv("NETOPS_TEMPLATES", raising=False)
    assert (template_dir() / "cisco_ios" / "ntp.j2").is_file()
