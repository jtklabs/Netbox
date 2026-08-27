"""End to end through the CLI, with netmiko replaced by a fake device.

Everything below `cli.main` is real: argument parsing, the CSV inventory,
nornir's threaded runner, the diff and the templates. Only the SSH session is
faked, so these tests catch wiring mistakes a unit test would not.
"""

import json
import os
import re
from pathlib import Path

import pytest
from nornir.core.task import Result

from netops import cli, runner

CSV = """host,name,platform,site
10.1.1.1,sw1,cisco_ios,atl
10.1.1.2,leaf1,arista_eos,rdu
"""


class FakeDevice:
    """A device that actually holds a running-config.

    Stateful on purpose: the runner reads the config back after applying, so a
    fixture that always returned the pre-change output would make every apply
    look like it failed -- and would hide idempotence bugs.
    """

    def __init__(self, lines, extra=None):
        self.lines = list(lines)
        #: Commands that are not config filters -- `show snmp user` and friends.
        self.extra = dict(extra or {})

    def show(self, command):
        """Emulate `show running-config | include|section <regex>`.

        `section` has to return the whole block, not just the matching line --
        an ACL's entries and a banner's body are the part that matters.
        """
        if command in self.extra:
            return self.extra[command]
        if "section " in command:
            return self._section(command.split("section ", 1)[1].strip())
        if "include " in command:
            pattern = command.split("include ", 1)[1].strip()
            return "\n".join(line for line in self.lines if re.search(pattern, line))
        return "\n".join(self.lines)

    def _section(self, pattern):
        out, inside, banner = [], False, False
        for line in self.lines:
            if re.search(pattern, line):
                out.append(line)
                inside = True
                banner = line.strip().startswith("banner ")
                continue
            if not inside:
                continue
            if banner:
                out.append(line)
                if line.strip() and not line.startswith((" ", "\t")):
                    inside = banner = False  # the delimiter line closes it
            elif line.startswith((" ", "\t")) or not line.strip():
                out.append(line)
            else:
                inside = False
        return "\n".join(out)

    def apply(self, commands):
        for command in commands:
            if command.startswith("no "):
                target = command[3:]
                self.lines = [
                    line
                    for line in self.lines
                    if line != target and not line.startswith(target + " ")
                ]
            else:
                self.lines.append(command)


@pytest.fixture
def device(monkeypatch):
    """Record what would be sent; serve it from a stateful fake device."""
    devices = {
        "sw1": FakeDevice(
            [
                "ntp server 10.10.10.1",
                "ntp server 10.10.10.2 prefer",
                "username admin privilege 15 password 7 070C285F4D06485744",
                "username netauto privilege 15 secret 9 $9$saLt$abcdef",
                "snmp-server packetsize 1500",
            ]
        ),
        "leaf1": FakeDevice(
            [
                "ntp server 10.10.10.1 iburst",
                "username admin privilege 15 role network-admin secret sha512 $6$saLt$abc",
                "username admin ssh-key ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCexample",
            ]
        ),
    }
    sent = {"config": {}, "commands": {}, "devices": devices}

    def send_command(task, command_string, **kwargs):
        sent["commands"].setdefault(task.host.name, []).append(command_string)
        box = devices.setdefault(task.host.name, FakeDevice([]))
        if command_string.startswith("show"):
            return Result(host=task.host, result=box.show(command_string))
        return Result(host=task.host, result="[OK]")

    def send_config(task, config_commands, **kwargs):
        sent["config"].setdefault(task.host.name, []).extend(config_commands)
        devices.setdefault(task.host.name, FakeDevice([])).apply(config_commands)
        return Result(host=task.host, result="\n".join(config_commands))

    monkeypatch.setattr(runner, "netmiko_send_command", send_command)
    monkeypatch.setattr(runner, "netmiko_send_config", send_config)
    return sent


@pytest.fixture
def csv_file(tmp_path):
    path = tmp_path / "hosts.csv"
    path.write_text(CSV, encoding="utf-8")
    return str(path)


@pytest.fixture
def login(monkeypatch):
    monkeypatch.setenv("NET_USER", "netauto")
    monkeypatch.setenv("NET_PASS", "sekrit")


def run(csv_file, *extra):
    return cli.main(["ntp", "--no-env-file", "--csv", csv_file, *extra])


def run_feature(feature, csv_file, *extra):
    return cli.main([feature, "--no-env-file", "--csv", csv_file, *extra])


PASSWORD = "R0tati0n-Passw0rd"


@pytest.fixture
def user_password(monkeypatch):
    monkeypatch.setenv("NETOPS_PW_ADMIN", PASSWORD)
    return PASSWORD


# --------------------------------------------------------------------------- #
# dry run
# --------------------------------------------------------------------------- #


def test_dry_run_is_the_default_and_changes_nothing(device, csv_file, login, capsys):
    assert run(csv_file, "-s", "10.99.99.1") == cli.EXIT_OK
    out = capsys.readouterr().out

    assert "DRY RUN" in out
    assert "ntp server 10.99.99.1" in out  # the IOS form
    assert "ntp server 10.99.99.1 iburst" in out  # the EOS form
    assert device["config"] == {}  # nothing was pushed
    assert device["commands"]["sw1"] == ["show running-config | include ^ntp"]


def test_dry_run_shows_the_save_command_it_would_run(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1")
    assert "write memory" in capsys.readouterr().out


def test_dry_run_replace_lists_the_removals(device, csv_file, login, capsys):
    assert run(csv_file, "-s", "10.99.99.1", "--replace") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "no ntp server 10.10.10.2 prefer" in out
    assert "no ntp server 10.10.10.1" in out
    assert device["config"] == {}


def test_add_mode_never_removes(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--add")
    assert "no ntp server" not in capsys.readouterr().out


def test_already_compliant_device_reports_no_changes(device, csv_file, login, capsys):
    assert run(csv_file, "-s", "10.10.10.1", "--limit", "leaf1") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "already compliant" in out
    assert "1 compliant" in out


def test_fail_on_diff_exits_two(device, csv_file, login):
    assert run(csv_file, "-s", "10.99.99.1", "--fail-on-diff") == cli.EXIT_DIFF


def test_fail_on_diff_is_quiet_when_compliant(device, csv_file, login):
    assert run(csv_file, "-s", "10.10.10.1", "--limit", "leaf1", "--fail-on-diff") == cli.EXIT_OK


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #


def test_apply_pushes_per_platform_commands(device, csv_file, login, capsys):
    assert run(csv_file, "-s", "10.99.99.1,10.99.99.2", "--apply", "-y") == cli.EXIT_OK
    assert device["config"]["sw1"] == [
        "ntp server 10.99.99.1",
        "ntp server 10.99.99.2",
    ]
    assert device["config"]["leaf1"] == [
        "ntp server 10.99.99.1 iburst",
        "ntp server 10.99.99.2 iburst",
    ]
    assert "APPLYING CHANGES" in capsys.readouterr().out


def test_apply_replace_adds_and_removes(device, csv_file, login):
    run(csv_file, "-s", "10.99.99.1", "--replace", "--apply", "-y", "--limit", "sw1")
    assert device["config"]["sw1"] == [
        "ntp server 10.99.99.1",
        "no ntp server 10.10.10.1",
        "no ntp server 10.10.10.2 prefer",
    ]


def test_apply_saves_the_config(device, csv_file, login):
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y")
    assert device["commands"]["sw1"][-1] == "write memory"


def test_no_save_skips_the_write(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--no-save")
    assert "write memory" not in device["commands"]["sw1"]
    assert "write memory" not in capsys.readouterr().out


def test_apply_does_nothing_on_a_compliant_device(device, csv_file, login):
    assert run(csv_file, "-s", "10.10.10.1", "--limit", "leaf1", "--apply", "-y") == cli.EXIT_OK
    assert device["config"] == {}
    assert "write memory" not in device["commands"]["leaf1"]


def test_prefer_and_vrf_reach_the_device(device, csv_file, login):
    run(
        csv_file, "-s", "10.99.99.1,10.99.99.2", "--prefer", "10.99.99.2",
        "--vrf", "MGMT", "--apply", "-y", "--limit", "sw1",
    )
    assert device["config"]["sw1"] == [
        "ntp server vrf MGMT 10.99.99.1",
        "ntp server vrf MGMT 10.99.99.2 prefer",
    ]


# --------------------------------------------------------------------------- #
# selection, failures, reporting
# --------------------------------------------------------------------------- #


def test_limit_selects_one_device(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--limit", "sw1")
    out = capsys.readouterr().out
    assert "sw1" in out and "leaf1" not in out
    assert "1 device(s)" in out


def test_limit_accepts_an_address(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--limit", "10.1.1.2")
    assert "leaf1" in capsys.readouterr().out


def test_filter_on_a_csv_column(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--filter", "site=rdu")
    out = capsys.readouterr().out
    assert "leaf1" in out and "sw1 " not in out


def test_no_match_is_a_usage_error(device, csv_file, login, capsys):
    assert run(csv_file, "-s", "10.99.99.1", "--limit", "nope") == cli.EXIT_USAGE
    assert "no devices matched" in capsys.readouterr().err


def test_missing_credentials_is_a_usage_error(device, csv_file, capsys):
    assert run(csv_file, "-s", "10.99.99.1") == cli.EXIT_USAGE
    err = capsys.readouterr().err
    assert "no credentials for" in err
    assert "AWS Secrets Manager" in err


def test_unsupported_platform_fails_that_device_only(device, tmp_path, login, capsys):
    path = tmp_path / "mixed.csv"
    path.write_text(CSV + "10.1.1.3,fw1,juniper_junos,atl\n", encoding="utf-8")
    assert run(str(path), "-s", "10.99.99.1") == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "FAILED" in out
    assert "no 'ntp' support" in out
    assert "1 failed" in out
    assert "ntp server 10.99.99.1" in out  # the other two still planned


def test_device_error_does_not_stop_the_others(device, csv_file, login, monkeypatch, capsys):
    def flaky(task, command_string, **kwargs):
        if task.host.name == "sw1":
            raise OSError("connection refused")
        return Result(host=task.host, result="ntp server 10.10.10.1 iburst")

    monkeypatch.setattr(runner, "netmiko_send_command", flaky)
    assert run(csv_file, "-s", "10.99.99.1") == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "OSError: connection refused" in out
    assert "ntp server 10.99.99.1 iburst" in out  # leaf1 still planned


def test_json_report(device, csv_file, login, tmp_path):
    report = tmp_path / "report.json"
    run(csv_file, "-s", "10.99.99.1", "--replace", "--report", str(report))

    document = json.loads(report.read_text())
    assert document["feature"] == "ntp"
    assert document["mode"] == "replace"
    assert document["dry_run"] is True
    assert document["desired"] == ["server:10.99.99.1"]

    sw1 = document["devices"]["sw1"]
    assert sw1["status"] == "pending"
    assert sw1["platform"] == "cisco_ios"
    assert sw1["current"] == ["ntp server 10.10.10.1", "ntp server 10.10.10.2 prefer"]
    assert sw1["commands"][0] == "ntp server 10.99.99.1"
    assert sw1["applied"] is False
    assert "generated_at" in document


def test_report_records_an_applied_run(device, csv_file, login, tmp_path):
    report = tmp_path / "report.json"
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--report", str(report))
    sw1 = json.loads(report.read_text())["devices"]["sw1"]
    assert (sw1["status"], sw1["applied"]) == ("changed", True)
    assert sw1["save_command"] == "write memory"


def test_verbose_shows_current_state(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "-v", "--limit", "sw1")
    out = capsys.readouterr().out
    assert "current:" in out
    assert "ntp server 10.10.10.2 prefer" in out


# --------------------------------------------------------------------------- #
# platform autodetection
# --------------------------------------------------------------------------- #


def test_blank_platform_is_autodetected(device, tmp_path, login, monkeypatch, capsys):
    path = tmp_path / "unknown.csv"
    path.write_text("host,name,platform\n10.1.1.9,mystery,\n", encoding="utf-8")

    class FakeDetect:
        def __init__(self, **kwargs):
            assert kwargs["device_type"] == "autodetect"
            assert kwargs["username"] == "netauto"

        def autodetect(self):
            return "cisco_ios"

    monkeypatch.setattr("netmiko.ssh_autodetect.SSHDetect", FakeDetect)
    assert run(str(path), "-s", "10.99.99.1") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "detecting platform on 1 device(s)" in out
    assert "[cisco_ios]" in out


def test_undetectable_platform_fails_cleanly(device, tmp_path, login, monkeypatch, capsys):
    path = tmp_path / "unknown.csv"
    path.write_text("host,name,platform\n10.1.1.9,mystery,\n", encoding="utf-8")

    class FakeDetect:
        def __init__(self, **kwargs):
            pass

        def autodetect(self):
            return None

    monkeypatch.setattr("netmiko.ssh_autodetect.SSHDetect", FakeDetect)
    assert run(str(path), "-s", "10.99.99.1") == cli.EXIT_FAILED
    assert "could not autodetect" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# .env wiring
# --------------------------------------------------------------------------- #


def test_env_file_supplies_credentials(device, csv_file, tmp_path, monkeypatch, capsys):
    env = tmp_path / "creds.env"
    env.write_text("NET_USER=fromenv\nNET_PASS=fromenv\n", encoding="utf-8")
    code = cli.main(["ntp", "--env-file", str(env), "--csv", csv_file, "-s", "10.99.99.1"])
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert "credentials: fromenv via environment" in out
    assert "fromenv" in out and "NET_PASS" not in out


def test_missing_env_file_is_a_usage_error(device, csv_file, tmp_path, capsys):
    code = cli.main(
        ["ntp", "--env-file", str(tmp_path / "nope.env"), "--csv", csv_file, "-s", "10.1.1.1"]
    )
    assert code == cli.EXIT_USAGE
    assert "env file not found" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# idempotence
# --------------------------------------------------------------------------- #


def test_applying_twice_is_a_no_op(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y")
    capsys.readouterr()
    assert run(csv_file, "-s", "10.99.99.1") == cli.EXIT_OK
    out = capsys.readouterr().out
    assert out.count("already compliant") == 2
    assert "2 compliant" in out


def test_apply_is_verified_against_a_read_back(device, csv_file, login, tmp_path):
    report = tmp_path / "r.json"
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--report", str(report))
    sw1 = json.loads(report.read_text())["devices"]["sw1"]
    assert sw1["verified"] is True
    assert sw1["missing_after"] == []
    # show, config, show (read back), write memory
    assert device["commands"]["sw1"].count("show running-config | include ^ntp") == 2


def test_no_verify_skips_the_read_back(device, csv_file, login):
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--no-verify")
    assert device["commands"]["sw1"].count("show running-config | include ^ntp") == 1


def test_a_change_that_does_not_take_is_reported_and_not_saved(
    device, csv_file, login, monkeypatch, capsys
):
    """The dangerous case: the push is accepted but the config did not change."""

    def swallow(task, config_commands, **kwargs):
        return Result(host=task.host, result="")  # device ignores it

    monkeypatch.setattr(runner, "netmiko_send_config", swallow)
    assert run(csv_file, "-s", "10.99.99.1", "--apply", "-y") == cli.EXIT_FAILED

    out = capsys.readouterr().out
    assert "APPLIED BUT NOT VERIFIED" in out
    assert "still missing after the change: server:10.99.99.1" in out
    assert "startup-config was NOT saved" in out
    assert "write memory" not in device["commands"]["sw1"]  # not persisted
    assert "2 unverified" in out


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #


def test_users_dry_run_negates_then_rewrites(device, csv_file, login, user_password, capsys):
    assert run_feature("users", csv_file, "-U", "admin", "--limit", "sw1") == cli.EXIT_OK
    out = capsys.readouterr().out
    lines = [line.strip() for line in out.splitlines()]
    assert lines.index("no username admin") < lines.index(
        "username admin privilege 15 secret <redacted>"
    )


def test_users_dry_run_shows_the_weak_type_it_is_replacing(
    device, csv_file, login, user_password, capsys
):
    run_feature("users", csv_file, "-U", "admin", "--limit", "sw1", "-v")
    assert "username admin privilege 15 password 7 (weak)" in capsys.readouterr().out


def test_the_password_never_reaches_the_terminal(device, csv_file, login, user_password, capsys):
    run_feature("users", csv_file, "-U", "admin", "--apply", "-y", "-v")
    out = capsys.readouterr().out
    assert PASSWORD not in out
    assert "secret <redacted>" in out


def test_the_password_does_reach_the_device(device, csv_file, login, user_password):
    run_feature("users", csv_file, "-U", "admin", "--apply", "-y", "--limit", "sw1")
    assert device["config"]["sw1"] == [
        "no username admin",
        f"username admin privilege 15 secret {PASSWORD}",
    ]


def test_the_password_never_reaches_the_report(
    device, csv_file, login, user_password, tmp_path
):
    report = tmp_path / "users.json"
    run_feature("users", csv_file, "-U", "admin", "--apply", "-y", "--report", str(report))
    text = report.read_text()
    assert PASSWORD not in text
    assert "<redacted>" in text


def test_users_apply_is_verified_and_saved(device, csv_file, login, user_password, capsys):
    assert run_feature("users", csv_file, "-U", "admin", "--apply", "-y") == cli.EXIT_OK
    assert "write memory" in device["commands"]["sw1"]
    assert "2 changed" in capsys.readouterr().out


def test_rotation_rewrites_on_every_run(device, csv_file, login, user_password, capsys):
    """Unlike NTP, a rotation is never 'already compliant' -- the point is to
    set the password again, and a salted hash cannot be compared."""
    run_feature("users", csv_file, "-U", "admin", "--apply", "-y")
    capsys.readouterr()
    run_feature("users", csv_file, "-U", "admin", "--limit", "sw1")
    out = capsys.readouterr().out
    assert "no username admin" in out
    assert "already compliant" not in out


def test_only_missing_leaves_an_existing_account_alone(
    device, csv_file, login, user_password, capsys
):
    assert (
        run_feature("users", csv_file, "-U", "admin", "--only-missing", "--limit", "sw1")
        == cli.EXIT_OK
    )
    assert "already compliant" in capsys.readouterr().out


def test_only_missing_still_creates_an_absent_account(
    device, csv_file, login, user_password, capsys
):
    run_feature("users", csv_file, "-U", "admin", "--only-missing", "--limit", "leaf1")
    capsys.readouterr()
    monkey = device["devices"]["leaf1"]
    monkey.lines = [line for line in monkey.lines if not line.startswith("username admin")]
    run_feature("users", csv_file, "-U", "admin", "--only-missing", "--limit", "leaf1")
    out = capsys.readouterr().out
    assert "username admin privilege 15 secret <redacted>" in out
    assert "no username admin" not in out


def test_replace_purges_unmanaged_accounts_but_not_the_login(
    device, csv_file, login, user_password
):
    run_feature("users", csv_file, "-U", "admin", "--replace", "--apply", "-y", "--limit", "sw1")
    pushed = device["config"]["sw1"]
    assert "no username admin" in pushed
    # netauto is the account this run is logged in as
    assert "no username netauto" not in pushed
    assert "username netauto privilege 15 secret 9 $9$saLt$abcdef" in device["devices"]["sw1"].lines


def test_replace_can_be_told_to_purge_the_login_account(
    device, csv_file, login, user_password
):
    run_feature(
        "users", csv_file, "-U", "admin", "--replace", "--allow-remove-self",
        "--apply", "-y", "--limit", "sw1",
    )
    assert "no username netauto" in device["config"]["sw1"]


def test_users_missing_password_is_a_usage_error(device, csv_file, login, capsys):
    code = run_feature("users", csv_file, "-U", "nosuchpassword")
    assert code == cli.EXIT_USAGE
    assert "NETOPS_PW_NOSUCHPASSWORD" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# snmp packet size
# --------------------------------------------------------------------------- #


def test_snmp_skips_arista_rather_than_failing_it(device, csv_file, login, capsys):
    assert run_feature("snmp-packetsize", csv_file) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "skipped -- EOS has no `snmp-server packetsize` equivalent" in out
    assert "snmp-server packetsize 1300" in out  # the IOS box is still planned
    assert "1 not applicable" in out


def test_snmp_sets_the_size_and_is_then_compliant(device, csv_file, login, capsys):
    assert run_feature("snmp-packetsize", csv_file, "--apply", "-y") == cli.EXIT_OK
    assert device["config"]["sw1"] == ["snmp-server packetsize 1300"]
    capsys.readouterr()

    assert run_feature("snmp-packetsize", csv_file) == cli.EXIT_OK
    assert "already compliant" in capsys.readouterr().out


def test_snmp_custom_size(device, csv_file, login):
    run_feature("snmp-packetsize", csv_file, "--size", "1400", "--apply", "-y")
    assert device["config"]["sw1"] == ["snmp-server packetsize 1400"]


def test_snmp_size_out_of_range_is_rejected(device, csv_file, login, capsys):
    with pytest.raises(SystemExit):
        run_feature("snmp-packetsize", csv_file, "--size", "20000")
    assert "must be between" in capsys.readouterr().err


def test_an_eos_ssh_key_is_removed_with_the_account(device, csv_file, login, user_password):
    run_feature("users", csv_file, "-U", "admin", "--apply", "-y", "--limit", "leaf1")
    assert device["config"]["leaf1"] == [
        "no username admin ssh-key",
        "no username admin",
        f"username admin privilege 15 secret {PASSWORD}",
    ]
    assert not any("ssh-key" in line for line in device["devices"]["leaf1"].lines)


def test_only_missing_still_removes_an_ssh_key(device, csv_file, login, user_password, capsys):
    """The account keeps its password; the alternative credential does not."""
    assert (
        run_feature(
            "users", csv_file, "-U", "admin", "--only-missing", "--apply", "-y",
            "--limit", "leaf1",
        )
        == cli.EXIT_OK
    )
    assert device["config"]["leaf1"] == ["no username admin ssh-key"]
    lines = device["devices"]["leaf1"].lines
    assert not any("ssh-key" in line for line in lines)
    assert "username admin privilege 15 role network-admin secret sha512 $6$saLt$abc" in lines


# --------------------------------------------------------------------------- #
# error handling -- one line on the terminal, the detail in the log
# --------------------------------------------------------------------------- #


NETMIKO_TIMEOUT = """\
Connection to device timed-out: cisco_ios 10.1.1.1:22

TCP connection to device failed.

Common causes of this problem are:
1. Incorrect hostname or IP address.
2. Wrong TCP port.
3. Intermediate firewall blocking access.

Device settings: cisco_ios 10.1.1.1:22
"""


class NetmikoTimeoutException(Exception):
    pass


@pytest.fixture
def log_file():
    """conftest points every run at a throwaway log."""
    return Path(os.environ["NETOPS_LOG_FILE"])


@pytest.fixture
def timing_out(monkeypatch):
    """sw1 times out the way netmiko really does; leaf1 is fine."""

    def flaky(task, command_string, **kwargs):
        if task.host.name == "sw1":
            raise NetmikoTimeoutException(NETMIKO_TIMEOUT)
        return Result(host=task.host, result="ntp server 10.10.10.1 iburst")

    monkeypatch.setattr(runner, "netmiko_send_command", flaky)


def test_a_timeout_is_one_line_not_a_wall_of_text(device, csv_file, login, timing_out, capsys):
    assert run(csv_file, "-s", "10.99.99.1") == cli.EXIT_FAILED
    captured = capsys.readouterr()
    out = captured.out

    assert "FAILED -- timed out connecting -- unreachable, filtered, or wrong port" in out
    assert "Common causes" not in out
    # nornir logs a traceback per failed task; unhandled, those reach stderr
    assert "Traceback" not in out + captured.err
    assert "Common causes" not in captured.err
    # the whole failure is one line, not nine
    assert len([line for line in out.splitlines() if "sw1" in line]) == 1


def test_nornirs_own_task_tracebacks_go_to_the_log_not_the_terminal(
    device, csv_file, login, timing_out, log_file, capsys
):
    run(csv_file, "-s", "10.99.99.1")
    captured = capsys.readouterr()
    assert "failed with traceback" not in captured.out + captured.err
    assert "NornirSubTaskError" not in captured.out + captured.err
    # ...but the detail is still recoverable
    assert "Traceback" in log_file.read_text()


def test_no_log_file_still_silences_the_libraries(
    device, csv_file, login, timing_out, capsys
):
    """--no-log-file means 'do not record it', not 'dump it on me instead'."""
    run(csv_file, "-s", "10.99.99.1", "--no-log-file")
    captured = capsys.readouterr()
    assert "Traceback" not in captured.out + captured.err
    assert "failed with traceback" not in captured.out + captured.err


def test_the_full_error_goes_to_the_log(device, csv_file, login, timing_out, log_file, capsys):
    run(csv_file, "-s", "10.99.99.1")
    assert "full detail in" in capsys.readouterr().out

    logged = log_file.read_text()
    assert "sw1 -- timed out connecting" in logged
    assert "Common causes" in logged  # the part the terminal spared you
    assert "Traceback (most recent call last)" in logged
    assert "NetmikoTimeoutException" in logged


def test_a_clean_run_leaves_no_log_file(device, csv_file, login, log_file, capsys):
    assert run(csv_file, "-s", "10.99.99.1") == cli.EXIT_OK
    assert not log_file.exists()
    assert "full detail in" not in capsys.readouterr().out


def test_debug_prints_the_traceback_as_well(device, csv_file, login, timing_out, capsys):
    run(csv_file, "-s", "10.99.99.1", "--debug")
    out = capsys.readouterr().out
    assert "Traceback (most recent call last)" in out
    assert "NetmikoTimeoutException" in out


def test_no_log_file_writes_nothing(device, csv_file, login, timing_out, log_file, capsys):
    assert run(csv_file, "-s", "10.99.99.1", "--no-log-file") == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert not log_file.exists()
    assert "timed out connecting" in out  # still readable, just not recorded
    assert "full detail in" not in out


def test_one_device_failing_does_not_hide_the_others(
    device, csv_file, login, timing_out, capsys
):
    run(csv_file, "-s", "10.99.99.1")
    out = capsys.readouterr().out
    assert "ntp server 10.99.99.1 iburst" in out  # leaf1 still planned
    assert "1 failed" in out


def test_problem_devices_are_reported_last(device, csv_file, login, timing_out, capsys):
    """So the thing needing attention sits next to the summary, not scrolled off."""
    run(csv_file, "-s", "10.99.99.1")
    out = capsys.readouterr().out
    assert out.index("leaf1") < out.index("sw1")


def test_platform_detection_failure_is_also_one_line(
    device, tmp_path, login, monkeypatch, log_file, capsys
):
    path = tmp_path / "unknown.csv"
    path.write_text("host,name,platform\n10.1.1.9,mystery,\n", encoding="utf-8")

    class FakeDetect:
        def __init__(self, **kwargs):
            raise NetmikoTimeoutException(NETMIKO_TIMEOUT)

    monkeypatch.setattr("netmiko.ssh_autodetect.SSHDetect", FakeDetect)
    assert run(str(path), "-s", "10.99.99.1") == cli.EXIT_FAILED
    out = capsys.readouterr().out
    assert "platform detection failed: timed out connecting" in out
    assert "Common causes" not in out
    assert "Common causes" in log_file.read_text()


def test_ctrl_c_does_not_print_a_traceback(monkeypatch, capsys):
    def interrupted(argv, style, log):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "_run", interrupted)
    assert cli.main(["ntp", "-s", "10.1.1.1"]) == cli.EXIT_INTERRUPTED
    captured = capsys.readouterr()
    assert "interrupted" in captured.err
    assert "Traceback" not in captured.err + captured.out


def test_an_unexpected_crash_is_one_line_and_logged(monkeypatch, log_file, capsys):
    def explode(argv, style, log):
        raise RuntimeError("something nobody anticipated")

    monkeypatch.setattr(cli, "_run", explode)
    assert cli.main(["ntp", "-s", "10.1.1.1"]) == cli.EXIT_FAILED
    captured = capsys.readouterr()
    assert "error: RuntimeError: something nobody anticipated" in captured.err
    assert "Traceback" not in captured.err
    assert "Traceback (most recent call last)" in log_file.read_text()


def test_the_banner_says_how_many_run_at_once(device, csv_file, login, capsys):
    run(csv_file, "-s", "10.99.99.1", "--workers", "25")
    # 2 devices, 25 workers -> 2 at a time, not a misleading 25
    assert "2 device(s), 2 at a time" in capsys.readouterr().out


def test_conn_timeout_reaches_netmiko(tmp_path, login):
    from netops.inventory import init_nornir

    csv_path = tmp_path / "h.csv"
    csv_path.write_text("host,name\n10.1.1.1,sw1\n", encoding="utf-8")
    nr = init_nornir(str(csv_path), "u", "p", None, None, 22, 1, conn_timeout=3.5)
    extras = nr.inventory.hosts["sw1"].get_connection_parameters("netmiko").extras
    assert extras["conn_timeout"] == 3.5


# --------------------------------------------------------------------------- #
# the other features, end to end
# --------------------------------------------------------------------------- #


STANDARDS = """
ntp:
  servers: [10.50.0.10]
syslog:
  destinations: [10.1.1.50]
  severity: informational
snmp:
  allow: [10.1.1.0/24]
  acl: SNMP-POLLERS
  communities: []
  location: ATL DC1
  users:
    - name: nmsuser
      group: NMS-RO
      auth: sha
      priv: aes 128
  groups:
    - name: NMS-RO
      security: priv
      read: NMS-VIEW
  views:
    - name: NMS-VIEW
      oid: iso
      action: included
banner:
  motd: true
acls:
  - name: SNMP-POLLERS
    permit: snmp.allow
    deny_log: true
    rebuild: true
"""

SNMP_AUTH = "snmp-auth-passphrase"
SNMP_PRIV = "snmp-priv-passphrase"


@pytest.fixture
def standards(tmp_path):
    """isolated_cwd already puts us in tmp_path, so this is discovered."""
    (tmp_path / "standards.yaml").write_text(STANDARDS, encoding="utf-8")
    return tmp_path / "standards.yaml"


@pytest.fixture
def snmp_passphrases(monkeypatch):
    monkeypatch.setenv("NETOPS_SNMP_AUTH_NMSUSER", SNMP_AUTH)
    monkeypatch.setenv("NETOPS_SNMP_PRIV_NMSUSER", SNMP_PRIV)


def test_ntp_reads_the_standards_file(device, csv_file, login, standards, capsys):
    """No --servers: the values come from the file."""
    assert cli.main(["ntp", "--no-env-file", "--csv", csv_file]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "standards:" in out
    assert "ntp server 10.50.0.10" in out


def test_syslog_applies_and_is_then_compliant(device, csv_file, login, standards, capsys):
    assert run_feature("syslog", csv_file, "--apply", "-y") == cli.EXIT_OK
    assert device["config"]["sw1"] == [
        "logging host 10.1.1.50",
        "logging trap informational",
    ]
    capsys.readouterr()
    assert run_feature("syslog", csv_file) == cli.EXIT_OK
    assert "2 compliant" in capsys.readouterr().out


def test_banner_is_pushed_with_its_blank_lines(device, csv_file, login, standards):
    run_feature("banner", csv_file, "--apply", "-y", "--limit", "sw1")
    pushed = device["config"]["sw1"]
    assert pushed[0] == "banner motd ^C"
    assert pushed[-1] == "^C"
    assert "" in pushed


def test_banner_is_idempotent(device, csv_file, login, standards, capsys):
    run_feature("banner", csv_file, "--apply", "-y")
    capsys.readouterr()
    assert run_feature("banner", csv_file) == cli.EXIT_OK
    assert "2 compliant" in capsys.readouterr().out


def test_acl_is_built_in_order_and_is_then_compliant(
    device, csv_file, login, standards, capsys
):
    assert run_feature("acl", csv_file, "--apply", "-y", "--limit", "sw1") == cli.EXIT_OK
    assert device["config"]["sw1"] == [
        "ip access-list standard SNMP-POLLERS",
        " permit 10.1.1.0 0.0.0.255",
        " deny any log",
    ]
    capsys.readouterr()
    assert run_feature("acl", csv_file, "--limit", "sw1") == cli.EXIT_OK
    assert "already compliant" in capsys.readouterr().out


def test_acl_out_of_order_on_the_device_is_rebuilt(device, csv_file, login, standards):
    device["devices"]["sw1"].lines.extend(
        [
            "ip access-list standard SNMP-POLLERS",
            " 10 deny any log",
            " 20 permit 10.1.1.0 0.0.0.255",
        ]
    )
    run_feature("acl", csv_file, "--apply", "-y", "--limit", "sw1")
    assert device["config"]["sw1"][0] == "no ip access-list standard SNMP-POLLERS"


def test_a_drifted_acl_without_rebuild_is_reported_and_nothing_is_sent(
    device, csv_file, login, tmp_path, capsys
):
    """Deleting an ACL to reorder it is a per-ACL decision, not a default."""
    (tmp_path / "standards.yaml").write_text(
        "acls:\n  - name: VTY-ACCESS\n    permit: [10.1.1.0/24]\n", encoding="utf-8"
    )
    device["devices"]["sw1"].lines.extend(
        ["ip access-list standard VTY-ACCESS", " 10 permit 10.9.9.9/32"]
    )
    code = run_feature("acl", csv_file, "--apply", "-y", "--limit", "sw1")

    out = capsys.readouterr().out
    assert code == cli.EXIT_DIFF  # a human is needed, whatever the flags said
    assert "NEEDS ATTENTION" in out
    assert "has drifted" in out
    assert "rebuild: true" in out
    assert device["config"] == {}  # nothing was sent
    assert "1 needing attention" in out


def test_snmp_removes_a_community_and_rewrites_a_weak_user(
    device, csv_file, login, standards, snmp_passphrases, capsys
):
    box = device["devices"]["sw1"]
    box.lines.append("snmp-server community public RO")
    box.extra["show snmp user"] = (
        "User name: nmsuser\n"
        "Authentication Protocol: MD5\n"
        "Privacy Protocol: DES\n"
        "Group-name: NMS-RO\n"
    )
    assert run_feature("snmp", csv_file, "--apply", "-y", "--limit", "sw1") == cli.EXIT_OK

    pushed = device["config"]["sw1"]
    assert "no snmp-server community public" in pushed
    assert "no snmp-server user nmsuser NMS-RO v3" in pushed
    assert any("snmp-server user nmsuser NMS-RO v3 auth sha" in c for c in pushed)


def test_snmp_passphrases_never_reach_the_terminal_or_report(
    device, csv_file, login, standards, snmp_passphrases, tmp_path, capsys
):
    report = tmp_path / "snmp.json"
    run_feature("snmp", csv_file, "--apply", "-y", "-v", "--report", str(report))
    out = capsys.readouterr().out
    assert SNMP_AUTH not in out and SNMP_PRIV not in out
    assert "<redacted>" in out

    text = report.read_text()
    assert SNMP_AUTH not in text and SNMP_PRIV not in text


def test_a_community_string_read_off_the_device_is_redacted(
    device, csv_file, login, standards, snmp_passphrases, capsys
):
    """It has to be named to be removed, but it is still a credential."""
    device["devices"]["sw1"].lines.append("snmp-server community s3cr3t-community RO")
    run_feature("snmp", csv_file, "--limit", "sw1")
    out = capsys.readouterr().out
    assert "s3cr3t-community" not in out
    assert "no snmp-server community <redacted>" in out


def test_snmp_is_idempotent_once_applied(
    device, csv_file, login, standards, snmp_passphrases, capsys
):
    run_feature("snmp", csv_file, "--apply", "-y", "--limit", "leaf1")
    capsys.readouterr()
    assert run_feature("snmp", csv_file, "--limit", "leaf1") == cli.EXIT_OK
    assert "already compliant" in capsys.readouterr().out


def test_snmp_without_passphrases_is_a_usage_error(device, csv_file, login, standards, capsys):
    assert run_feature("snmp", csv_file) == cli.EXIT_USAGE
    assert "NETOPS_SNMP_AUTH_NMSUSER" in capsys.readouterr().err


# --------------------------------------------------------------------------- #
# selftest -- it renders every template against the real standards file, so a
# broken template or an unrenderable value fails here rather than on a device
# --------------------------------------------------------------------------- #


def test_selftest_runs_clean(capsys):
    assert cli.main(["selftest"]) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "all templates rendered" in out
    assert "RENDER FAILED" not in out


def test_selftest_covers_every_feature_and_platform(capsys):
    cli.main(["selftest"])
    out = capsys.readouterr().out
    for name, feature in cli.FEATURES.items():
        assert f"### {name}" in out
        for platform in feature.platforms:
            assert platform in out


def test_selftest_checks_the_standards_file(capsys):
    """It renders against the real file when there is one, and the shipped
    example when there is not -- so a fresh clone can still check itself."""
    cli.main(["selftest"])
    out = capsys.readouterr().out
    assert "standards:" in out
    assert "warning:" not in out


def test_a_run_with_no_standards_file_says_where_the_example_is(
    device, csv_file, login, capsys
):
    with pytest.raises(SystemExit):
        cli.main(["ntp", "--no-env-file", "--csv", csv_file])
    err = capsys.readouterr().err
    assert "no standards file here" in err
    assert "standards.yaml.example" in err


def test_selftest_needs_no_credentials_or_network(capsys, monkeypatch):
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    assert cli.main(["selftest"]) == cli.EXIT_OK


# --------------------------------------------------------------------------- #
# ServiceNow change records, end to end through the CLI
# --------------------------------------------------------------------------- #


class FakeSnow:
    """Stands in for the ServiceNow client; records every call."""

    def __init__(self, state="-2"):
        from netops.servicenow import Settings

        self.settings = Settings(instance="acme", username="u", password="p")
        self.state = state
        self.created = None
        self.updates = []
        self.notes = []
        self.attachments = []

    def get_change(self, number):
        return {"number": number, "sys_id": "sys-abc", "state": self.state}

    def create_change(self, fields):
        self.created = fields
        return {"number": "CHG0099999", "sys_id": "sys-new", "state": fields["state"]}

    def update_change(self, sys_id, fields):
        self.updates.append((sys_id, fields))
        return {}

    def add_work_note(self, sys_id, text):
        self.notes.append(text)

    def attach(self, sys_id, filename, payload, content_type):
        self.attachments.append((sys_id, filename, payload.decode()))


@pytest.fixture
def snow(monkeypatch):
    fake = FakeSnow()
    monkeypatch.setattr(cli, "_servicenow_client", lambda args, style: fake)
    return fake


def test_open_change_records_the_plan_and_touches_no_device(
    device, csv_file, login, snow, capsys
):
    assert run(csv_file, "-s", "10.99.99.1", "--open-change") == cli.EXIT_OK
    out = capsys.readouterr().out

    assert device["config"] == {}  # dry run: nothing was sent
    assert snow.created["state"] == "-5"  # opened in New, not approved
    assert "ntp server 10.99.99.1" in snow.created["implementation_plan"]
    assert "sw1" in snow.created["implementation_plan"]
    assert "opened CHG0099999" in out
    assert "--change CHG0099999" in out  # tells you the next step


def test_open_change_attaches_the_report(device, csv_file, login, snow):
    run(csv_file, "-s", "10.99.99.1", "--open-change")
    sys_id, filename, body = snow.attachments[0]
    assert (sys_id, filename) == ("sys-new", "netops-ntp-plan.json")
    assert json.loads(body)["dry_run"] is True


def test_open_change_refuses_to_be_combined_with_apply(device, csv_file, login, snow, capsys):
    assert run(csv_file, "-s", "10.99.99.1", "--open-change", "--apply", "-y") == cli.EXIT_USAGE
    assert "dry-run action" in capsys.readouterr().err
    assert snow.created is None


def test_an_unapproved_change_is_refused_before_any_device_is_touched(
    device, csv_file, login, snow, capsys
):
    snow.state = "-5"  # New
    code = run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--change", "CHG0012345")
    assert code == cli.EXIT_USAGE
    assert device["config"] == {}  # nothing was pushed
    assert device["commands"] == {}  # nothing was even read
    err = capsys.readouterr().err
    assert "cannot be implemented" in err
    assert "Approve it in ServiceNow" in err


def test_an_approved_change_is_implemented_and_closed(device, csv_file, login, snow, capsys):
    assert run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--change", "CHG0012345") == (
        cli.EXIT_OK
    )
    assert device["config"]["sw1"] == ["ntp server 10.99.99.1"]

    sys_id, fields = snow.updates[-1]
    assert sys_id == "sys-abc"
    assert fields["state"] == "3"  # closed
    assert fields["close_code"] == "successful"
    assert "sw1: changed" in snow.notes[0]
    assert snow.attachments[0][1] == "netops-ntp-result.json"
    assert "closed CHG0012345 as successful" in capsys.readouterr().out


def test_a_partial_failure_closes_as_successful_with_issues(
    device, csv_file, login, snow, monkeypatch, capsys
):
    def flaky(task, command_string, **kwargs):
        if task.host.name == "sw1":
            raise OSError("connection refused")
        # leaf1 behaves normally, so it really does change and verify
        return Result(
            host=task.host,
            result=device["devices"][task.host.name].show(command_string),
        )

    monkeypatch.setattr(runner, "netmiko_send_command", flaky)
    code = run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--change", "CHG0012345")

    _, fields = snow.updates[-1]
    assert code == cli.EXIT_FAILED  # the device failure still shows in the exit code
    assert fields["close_code"] == "successful_issues"
    assert "sw1: FAILED" in snow.notes[0]
    assert "connection refused" in snow.notes[0]


def test_a_total_failure_closes_as_unsuccessful(
    device, csv_file, login, snow, monkeypatch
):
    def dead(task, command_string, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr(runner, "netmiko_send_command", dead)
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--change", "CHG0012345")
    assert snow.updates[-1][1]["close_code"] == "unsuccessful"


def test_the_change_number_is_recorded_in_the_report(
    device, csv_file, login, snow, tmp_path
):
    report = tmp_path / "r.json"
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--change", "CHG0012345",
        "--report", str(report))
    assert json.loads(report.read_text())["change"] == "CHG0012345"


def test_a_servicenow_failure_after_the_push_is_reported_loudly(
    device, csv_file, login, snow, monkeypatch, capsys
):
    """The devices are done; the operator has to know the change is still open."""
    from netops.servicenow import ServiceNowError

    def refuse(sys_id, fields):
        raise ServiceNowError("Insufficient rights to close")

    monkeypatch.setattr(snow, "update_change", refuse)
    code = run(csv_file, "-s", "10.99.99.1", "--apply", "-y", "--change", "CHG0012345")

    err = capsys.readouterr().err
    assert code == cli.EXIT_FAILED
    assert "Insufficient rights" in err
    assert "was not closed -- close it by hand" in err
    assert device["config"]["sw1"] == ["ntp server 10.99.99.1"]  # the push did happen


def test_open_change_and_change_together_is_a_usage_error(
    device, csv_file, login, snow, capsys
):
    code = run(csv_file, "-s", "10.99.99.1", "--open-change", "--change", "CHG1")
    assert code == cli.EXIT_USAGE
    assert "Use one or the other" in capsys.readouterr().err


def test_no_servicenow_call_without_the_flags(device, csv_file, login, snow):
    run(csv_file, "-s", "10.99.99.1", "--apply", "-y")
    assert snow.created is None and snow.updates == []


def test_rewrite_users_pushes_what_cannot_be_read_back(
    device, csv_file, login, standards, snmp_passphrases
):
    """A passphrase is invisible from the device, so nothing else triggers it."""
    box = device["devices"]["sw1"]
    box.lines.append("snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-POLLERS")
    box.extra["show snmp user"] = (
        "User name: nmsuser\n"
        "Authentication Protocol: SHA\n"
        "Privacy Protocol: AES128\n"
        "Group-name: NMS-RO\n"
    )
    # Everything the device can report already matches, so the user is left alone...
    run_feature("snmp", csv_file, "--apply", "-y", "--limit", "sw1")
    assert not any("no snmp-server user" in c for c in device["config"].get("sw1", []))

    # ...until asked.
    device["config"].clear()
    run_feature("snmp", csv_file, "--apply", "-y", "--limit", "sw1", "--rewrite-users")
    pushed = device["config"]["sw1"]
    assert "no snmp-server user nmsuser NMS-RO v3" in pushed
    assert any("snmp-server user nmsuser NMS-RO v3 auth sha" in c for c in pushed)


def test_a_changed_group_acl_is_detected_and_takes_its_user_with_it(
    device, csv_file, login, standards, snmp_passphrases
):
    """Unlike a user's, a group's ACL is in the running config -- so it is
    comparable, and a change to it is ordinary detected drift."""
    box = device["devices"]["sw1"]
    box.lines.append("snmp-server group NMS-RO v3 priv read NMS-VIEW access WRONG-ACL")
    box.extra["show snmp user"] = (
        "User name: nmsuser\n"
        "Authentication Protocol: SHA\n"
        "Privacy Protocol: AES128\n"
        "Group-name: NMS-RO\n"
    )
    run_feature("snmp", csv_file, "--apply", "-y", "--limit", "sw1")
    pushed = device["config"]["sw1"]

    assert pushed.index("no snmp-server user nmsuser NMS-RO v3") < pushed.index(
        "no snmp-server group NMS-RO v3 priv read NMS-VIEW access WRONG-ACL"
    )
    assert "snmp-server group NMS-RO v3 priv read NMS-VIEW access SNMP-POLLERS" in pushed
    assert any("snmp-server user nmsuser NMS-RO v3 auth sha" in c for c in pushed)
