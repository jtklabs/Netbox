"""Offline transcripts and failure injection; never connect to real switches."""

import copy
import json
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from netops.upgrade import checks, workflow
from netops.upgrade.profile import Profile


@pytest.fixture
def profile():
    return Profile("lab-approved", ("C9300-48P",), ("17.9.4a",), "17.12.4",
                   "cat9k_iosxe.17.12.04.SPA.bin", "a" * 32, 1500000000)


def transcript(release="17.9.4a", mode="INSTALL"):
    config = "hostname sw1\n!\nboot-start-marker\nboot system flash:packages.conf\nboot-end-marker\n!\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n!\nend"
    return {
        "show version": f"Cisco IOS XE Software, Version {release}\nSwitch Ports Model SW Version SW Image Mode\n* 1 56 C9300-48P {release} CAT9K_IOSXE {mode}\n",
        "show switch": "Switch/Stack Mac Address : 0011.2233.4455\nSwitch# Role Mac Address Priority Version State\n*1 Active 0011.2233.4455 15 V02 Ready\n",
        "show running-config": "Building configuration...\nCurrent configuration : 222 bytes\n! Last configuration change at 12:00\nversion 17.9\n" + config,
        "show startup-config": "Using 222 out of 2097152 bytes\n! NVRAM config last updated at 12:00\nversion 17.9\n" + config,
        "show inventory": 'NAME: "Switch 1", DESCR: "C9300-48P"\nPID: C9300-48P , VID: V02 , SN: FOC12345678\n',
        "show interfaces status": "Port         Name               Status       Vlan       Duplex  Speed Type\nGi1/0/1                         connected    10         a-full a-1000 10/100/1000BaseTX\n",
        "show ip interface brief": "Interface              IP-Address      OK? Method Status                Protocol\nVlan10                 10.0.0.1        YES manual up                    up\n",
        "show access-session": "Interface                MAC Address    Method  Domain  Status Fg  Session ID\nGi1/0/1                  aaaa.bbbb.cccc  dot1x   DATA    Auth       0000000000000001\nSession count = 1\n",
        "show mac address-table": "          Mac Address Table\n-------------------------------------------\nVlan    Mac Address       Type        Ports\n----    -----------       --------    -----\n  10    aaaa.bbbb.cccc    DYNAMIC     Gi1/0/1\nTotal Mac Addresses for this criterion: 1\n",
        "show vlan brief": "VLAN Name                             Status    Ports\n---- -------------------------------- --------- -------------------------------\n1    default                          active\n10   users                            active    Gi1/0/1\n",
        "show etherchannel summary": "Number of channel-groups in use: 0\n",
        "show spanning-tree": "No spanning tree instance exists.\n",
        "show ip arp": "Protocol  Address          Age (min)  Hardware Addr   Type   Interface\nInternet  10.0.0.2         5          aaaa.bbbb.cccc  ARPA   Vlan10\n",
        "show standby brief": "P indicates configured to preempt.\nInterface   Grp  Pri P State   Active          Standby         Virtual IP\n",
        "show ip route": "Codes: L - local, C - connected, S - static\nGateway of last resort is not set\nC        10.0.0.0/24 is directly connected, Vlan10\n",
        "show boot": "BOOT variable = flash:packages.conf;\nManual Boot = no\n",
        "show install summary": f"[ Switch 1 ] Installed Package(s) Information:\nType St Filename/Version\nIMG C {release}.0.1\nAuto abort timer: inactive\n",
        "show vrf": "  Name                             Default RD            Protocols   Interfaces\n",
    }


def baseline(raw=None):
    data = transcript() if raw is None else raw
    return checks.collect(lambda cmd: data.get(cmd, "% Invalid input detected at '^' marker."), lambda cmd: None)


def test_baseline_parses_realistic_tables_without_losing_endpoints():
    snapshot = baseline()
    assert snapshot["errors"] == {}
    assert snapshot["tables"]["nac"] == [{"interface": "Gi1/0/1", "mac_address": "aaaa.bbbb.cccc", "method": "dot1x", "domain": "DATA", "status": "Auth"}]
    assert len(snapshot["tables"]["mac"]) == 1
    assert snapshot["config"] == snapshot["startup_config"]


@pytest.mark.parametrize('port', ['Fi1/0/49', 'Ap1/0/1', 'Tw1/0/1', 'AppGigabitEthernet1/0/1', 'TwoHundredGigE1/1/1'])
def test_interface_check_does_not_require_a_known_port_abbreviation(port):
    raw = transcript()
    raw['show interfaces status'] += f'{port:<31} connected    10         a-full a-1000 10/100/1000BaseTX\n'
    snapshot = baseline(raw)
    assert 'interfaces' not in snapshot['errors']
    assert {row['port'] for row in snapshot['tables']['interfaces']} == {'Gi1/0/1', port}


@pytest.mark.parametrize('ports', [[], ['Gi1/0/99'], ['Gi1/0/1', 'Gi1/0/1']])
def test_interface_check_rejects_missing_replaced_or_duplicated_records(monkeypatch, ports):
    monkeypatch.setattr(checks, 'parse_output', lambda **kwargs: [dict(port=port) for port in ports])
    with pytest.raises(ValueError, match='parser (returned no records|did not account for every table row)'):
        checks.table('show interfaces status', *checks.TABLES['interfaces'][1:4],
                     transcript()['show interfaces status'])


@pytest.mark.parametrize("change,expected", [
    (lambda s: s["software"]["1"].update(version="17.6.5"), "starting version"),
    (lambda s: s["software"]["1"].update(model="C9500-24Y4C"), "PID"),
    (lambda s: s["software"]["1"].update(mode="BUNDLE"), "BUNDLE"),
    (lambda s: s["stack"]["1"].update(state="Provisioned"), "Ready"),
    (lambda s: s["errors"].update(nac="unreadable"), "nac"),
])
def test_preflight_gates(profile, change, expected):
    snapshot = baseline()
    change(snapshot)
    assert expected in " ".join(workflow.preflight(snapshot, profile)["blockers"])


def test_unsaved_configuration_is_recorded_without_blocking_a_dry_run(profile):
    snapshot = baseline()
    snapshot['config'] = snapshot['config'].replace('switchport access vlan 10', 'switchport access vlan 20')
    plan = workflow.preflight(snapshot, profile)
    assert plan['unsaved_changes'] and not plan['blockers']
    assert '- switchport access vlan 10' in plan['saved_config_diff']
    assert '+ switchport access vlan 20' in plan['saved_config_diff']


def test_configuration_still_unsaved_after_write_memory_blocks(profile):
    snapshot = baseline()
    snapshot['config'] = snapshot['config'].replace('switchport access vlan 10', 'switchport access vlan 20')
    plan = workflow.preflight(snapshot, profile, saved=True)
    assert any('still differ after write memory' in reason for reason in plan['blockers'])
    assert not any('switchport access vlan' in reason for reason in plan['blockers'])
    assert '+ switchport access vlan 20' in plan['saved_config_diff']


def test_failed_config_read_is_not_reported_as_unsaved(profile):
    snapshot = baseline()
    del snapshot['config']
    snapshot['errors']['config'] = 'show running-config: read timed out'
    plan = workflow.preflight(snapshot, profile, saved=True)
    assert any(reason.startswith('config: ') for reason in plan['blockers'])
    assert not any('differ' in reason for reason in plan['blockers'])
    assert not plan['unsaved_changes'] and 'saved_config_diff' not in plan


def test_already_current_device_with_unsaved_changes_is_not_blocked(profile):
    snapshot = baseline(transcript('17.12.04'))
    snapshot['config'] += '\ninterface Vlan99'
    plan = workflow.preflight(snapshot, profile)
    assert plan['already_current'] and not plan['blockers'] and plan['unsaved_changes']


def test_boot_findings_can_skip_the_saved_config_check():
    snapshot = {'config': 'boot system flash:packages.conf\nhostname a', 'startup_config': 'boot system flash:packages.conf\nhostname b'}
    assert [f['check'] for f in workflow.boot_findings(snapshot)] == ['saved_config']
    assert workflow.boot_findings(snapshot, saved=False) == []


def test_target_is_a_noop_and_versions_normalize(profile):
    snapshot = baseline(transcript("17.12.04"))
    plan = workflow.preflight(snapshot, profile)
    assert plan["already_current"] and not plan["upgrade_needed"]
    assert not plan["blockers"]


def test_bundle_requires_profile_validation(profile):
    from dataclasses import replace
    snapshot = baseline(transcript(mode="BUNDLE"))
    plan = workflow.preflight(snapshot, replace(profile, bundle_conversion_validated=True))
    assert plan["bundle_conversion"] and not plan["blockers"]


@pytest.mark.parametrize("field,value", [
    ("starting_versions", ["16.5.1a"]), ("starting_versions", ["17.12.4"]),
    ("models", ["C9300*"]), ("models", ["C9350-48P"]),
    ("md5", "bad"), ("image", "cat9k_iosxe.x.bin\nreload"),
    ("image", "cat9k_iosxe.17.09.04a.SPA.bin"),
    ("minimum_free_bytes", 100), ("bundle_conversion_validated", "false"),
    ("image_source", "https://user:secret@example.com/x.bin"),
])
def test_profile_rejects_unvalidated_or_injected_values(profile, tmp_path, field, value):
    data = asdict(profile)
    data["models"] = list(data["models"])
    data["starting_versions"] = list(data["starting_versions"])
    data[field] = value
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        Profile.load(path)


def test_profile_load(profile, tmp_path):
    data = asdict(profile)
    data["models"] = list(data["models"])
    data["starting_versions"] = list(data["starting_versions"])
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data))
    assert Profile.load(path) == profile


def test_equal_counts_do_not_hide_endpoint_replacement_or_authorization_loss():
    pre, post = baseline(), baseline()
    post["tables"]["nac"][0]["status"] = "Unauth"
    post["tables"]["mac"][0]["destination_address"] = "1111.2222.3333"
    findings = {f["check"]: f for f in checks.compare(pre, post)}
    assert findings["nac"]["removed"] and findings["nac"]["added"]
    assert findings["mac"]["before_count"] == findings["mac"]["after_count"] == 1


def test_config_change_is_flagged_and_boot_changes_checked_separately(profile):
    pre = baseline()
    post = baseline(transcript(profile.target_version))
    post["config"] += "\nusername unexpected privilege 15 secret 9 abc"
    assert any(f["check"] == "config" for f in checks.compare(pre, post))
    post["config"] = post["config"].replace("boot system flash:packages.conf", "boot system flash:wrong.bin")
    assert any(f["check"] == "config_boot" for f in workflow.target_findings(post, profile, pre))


def test_unparsed_mac_fails_closed():
    raw = transcript()
    raw["show mac address-table"] += "unknown syntax 1111.2222.3333\n"
    assert "mac" in baseline(raw)["errors"]


def test_nac_command_fallback():
    raw = transcript()
    raw["show authentication sessions"] = raw.pop("show access-session")
    snapshot = baseline(raw)
    assert "nac" not in snapshot["errors"]
    assert len(snapshot["tables"]["nac"]) == 1


def test_protocol_detection_includes_vrf_named_eigrp_and_unknown_protocols():
    config = "router ospf 10 vrf users\n router-id 1.1.1.1\n!\nrouter bgp 65000\n neighbor 10.0.0.2 remote-as 65001\n!\nrouter eigrp CAMPUS\n address-family ipv4 vrf users autonomous-system 10\n exit-address-family\n!\nrouter rip\n version 2\n!\n"
    routing, unknown = checks.routing_checks(config)
    assert ("show ip ospf 10 neighbor", "ospf") in routing.values()
    assert ("show ip eigrp vrf users neighbors", "eigrp") in routing.values()
    assert ("show bgp all summary", "bgp") in routing.values()
    assert unknown == ["router rip"]


def test_bgp_ipv6_wrapped_peer_and_lost_session():
    text = "For address family: IPv6 Unicast\nBGP router identifier 1.1.1.1, local AS number 65000\nNeighbor V AS MsgRcvd MsgSent TblVer InQ OutQ Up/Down State/PfxRcd\n2001:db8::2\n  4 65001 12 14 3 0 0 00:10:12 5\n"
    rows = checks.peers(text, "bgp")
    assert rows[0]["neighbor"] == "2001:db8::2" and rows[0]["state"] == "Established"
    assert checks.peers(text.replace("00:10:12 5", "00:00:01 Idle"), "bgp")[0]["state"] == "Idle"


def test_disappeared_protocol_still_runs_original_check():
    called = []
    raw = transcript()
    raw["show ip ospf 10 neighbor"] = "Neighbor ID Pri State Dead Time Address Interface\n"
    snap = checks.collect(lambda cmd: called.append(cmd) or raw.get(cmd, "% Invalid input"), lambda cmd: None,
                          {"router ospf 10": ("show ip ospf 10 neighbor", "ospf")})
    assert "show ip ospf 10 neighbor" in called
    assert snap["routing"]["router ospf 10"] == []


@pytest.fixture
def options(tmp_path):
    return SimpleNamespace(apply=False, lock_dir=tmp_path / "locks", show_timeout=60,
                           install_timeout=30, reload_timeout=3, settle_seconds=0,
                           validation_timeout=1, poll_interval=0)


@pytest.fixture
def fake_device(monkeypatch, profile):
    class FakeDevice:
        instances = []
        raw = transcript()
        digest = "a" * 32
        fail_install = False
        lose_mac = False
        image_exists = True

        def __init__(self, task, options, emit):
            self.options, self.emit = options, emit
            self.raw = dict(self.raw)
            self.connection = Mock()
            self.connection.send_config_set.side_effect = self.configure_boot
            self.mutations = []
            self.instances.append(self)

        def configure_boot(self, commands, **kwargs):
            import re
            self.raw["show running-config"] = re.sub(r"(?m)^boot system .*", "boot system flash:packages.conf", self.raw["show running-config"])
            return "ok"

        def connect(self):
            pass

        def close(self):
            pass

        def read(self, command):
            if command.startswith("dir flash:cat9k"):
                return ("1 -rw- 1000 Sep 11 2026 " + profile.image) if self.image_exists else "%Error opening flash:x (No such file or directory)"
            if command.startswith("dir flash-"):
                return "9999999999 bytes total (9000000000 bytes free)"
            return self.raw.get(command, "% Invalid input")

        def write(self, command, timeout=120):
            if command.startswith("verify /md5"):
                return "verify /md5 = " + self.digest
            self.mutations.append(command)
            if command == "write memory":
                self.raw["show startup-config"] = self.raw["show running-config"]
            return "Building configuration...\n[OK]"

        def interactive(self, command, timeout, reload=False):
            self.mutations.append(command)
            if self.fail_install:
                raise ValueError("injected install failure")

        def wait_for_target(self, profile):
            self.raw = transcript(profile.target_version)
            if self.lose_mac:
                self.raw["show mac address-table"] = "Mac Address Table\nTotal Mac Addresses for this criterion: 0\n"

    monkeypatch.setattr(workflow, "Device", FakeDevice)
    return FakeDevice


def run_device(profile, options):
    host = SimpleNamespace(name="sw1", hostname="192.0.2.1", port=22, platform="cisco_ios", data={})
    reporter = Mock()
    reporter.emit.return_value = True
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    return result, reporter


def test_dry_run_never_mutates(profile, options, fake_device):
    result, reporter = run_device(profile, options)
    assert not result.failed
    device = fake_device.instances[0]
    assert not device.mutations
    device.connection.send_config_set.assert_not_called()
    assert reporter.emit.call_args.args[1] == "dry_run_complete"


def test_apply_success_and_repeat_baseline(profile, options, fake_device):
    options.apply = True
    result, reporter = run_device(profile, options)
    assert not result.failed
    assert result.changed
    assert fake_device.instances[0].mutations == ["write memory", "write memory", f"install add file flash:{profile.image} activate commit"]
    stages = [call.args[1] for call in reporter.emit.call_args_list]
    assert stages.count("validating") == 2
    assert stages[-1] == "completed_with_warnings"


def test_bad_checksum_blocks_all_writes(profile, options, fake_device):
    options.apply = True
    fake_device.digest = "b" * 32
    result, reporter = run_device(profile, options)
    assert result.failed and not result.changed
    # Only the precheck configuration save; no boot or install writes.
    assert fake_device.instances[0].mutations == ["write memory"]
    fake_device.instances[0].connection.send_config_set.assert_not_called()


def test_unsupported_start_blocks_apply(profile, options, fake_device):
    options.apply = True
    fake_device.raw = transcript("17.6.5")
    result, reporter = run_device(profile, options)
    assert result.failed
    assert reporter.emit.call_args.args[1] == "blocked"
    assert fake_device.instances[0].mutations == ["write memory"]


def test_install_failure_is_not_retried(profile, options, fake_device):
    options.apply = True
    fake_device.fail_install = True
    result, reporter = run_device(profile, options)
    assert result.failed and result.changed
    assert reporter.emit.call_args.args[1] == "recovery_required"
    assert len([c for c in fake_device.instances[0].mutations if c.startswith("install")]) == 1


def test_post_upgrade_difference_fails(profile, options, fake_device):
    options.apply = True
    options.validation_timeout = 0
    fake_device.lose_mac = True
    result, reporter = run_device(profile, options)
    assert result.failed and result.changed
    assert reporter.emit.call_args.args[1] == "validation_failed"


def test_pending_transfer_is_read_only_in_dry_run(profile, options, fake_device):
    from dataclasses import replace
    profile = replace(profile, image_source="https://images.example.com/" + profile.image)
    fake_device.image_exists = False
    result, _ = run_device(profile, options)
    assert not result.failed
    assert result.result["image_verification"] == "pending_transfer"
    assert not fake_device.instances[0].mutations


def test_apply_webhook_gate_blocks_writes(profile, options, fake_device):
    options.apply = True
    host = SimpleNamespace(name="sw1", hostname="192.0.2.1", port=22, platform="cisco_ios", data={})
    reporter = Mock()
    reporter.emit.return_value = False
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    assert result.failed and not result.changed
    assert fake_device.instances[0].mutations == ["write memory"]
    fake_device.instances[0].connection.send_config_set.assert_not_called()


def test_local_lock_excludes_concurrent_runs(tmp_path):
    with workflow.device_lock(tmp_path, "192.0.2.1"):
        with pytest.raises(ValueError, match="already holds"):
            with workflow.device_lock(tmp_path, "192.0.2.1"):
                pytest.fail("must not acquire twice")


def test_interactive_reload_prompt_only_once(monkeypatch, options):
    conn = Mock()
    conn.find_prompt.return_value = "sw1#"
    conn.read_channel.side_effect = ["install_add_activate_commit: START\nDo you want to proceed? [y/n]", "\nSUCCESS: install finished\nsw1#"]
    conn.is_alive.return_value = True
    monkeypatch.setattr(workflow.time, "sleep", lambda _: None)
    device = workflow.Device(None, options, Mock())
    device.connection = conn
    device.interactive("install add file flash:x.bin activate commit", 30, reload=True)
    assert [call.args[0] for call in conn.write_channel.call_args_list] == ["install add file flash:x.bin activate commit\n", "y\n"]


def test_unknown_prompt_is_not_accepted(monkeypatch, options):
    conn = Mock()
    conn.find_prompt.return_value = "sw1#"
    conn.read_channel.return_value = "Delete everything? [confirm]"
    device = workflow.Device(None, options, Mock())
    device.connection = conn
    with pytest.raises(ValueError, match="unrecognized interactive"):
        device.interactive("install add file flash:x.bin activate commit", 30, reload=True)
    assert conn.write_channel.call_count == 1


@pytest.mark.parametrize("stage_only", [False, True])
def test_progress_retries_same_event_and_never_sends_config(tmp_path, monkeypatch, stage_only):
    from netops import archive, webhook
    from netops.upgrade.progress import Reporter
    run = archive.Run([], tmp_path)
    run.args = SimpleNamespace(command="upgrade", apply=True, stage_only=stage_only)
    run.prepare()
    send = Mock(side_effect=[webhook.WebhookError("offline"), None])
    monkeypatch.setattr(webhook, "send", send)
    monkeypatch.setattr("netops.upgrade.progress.time.sleep", lambda _: None)
    reporter = Reporter(run, webhook.Settings("https://example.com/events", "secret"))
    host = SimpleNamespace(name="sw1", hostname="192.0.2.1", platform="cisco_ios", data={})
    assert reporter.emit(host, "precheck_complete", "Captured baseline", {"pre": {"config": "private config"}})
    assert send.call_args_list[0].args[1] == send.call_args_list[1].args[1]
    event = json.loads(send.call_args.args[1])
    assert event["operation"] == ("stage_image" if stage_only else "upgrade")
    assert "private config" not in json.dumps(event) and "secret" not in json.dumps(event)
    assert run.records["sw1"]["upgrade_events"][0]["delivery"] == "delivered"


def test_upgrade_environment_is_separate(monkeypatch):
    from netops.upgrade.progress import settings_from_env
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_URL", "https://example.com/nac")
    monkeypatch.setenv("NETOPS_NAC_WEBHOOK_TOKEN", "nac-token")
    assert settings_from_env() is None
    monkeypatch.setenv("NETOPS_UPGRADE_WEBHOOK_URL", "https://example.com/upgrades")
    monkeypatch.setenv("NETOPS_UPGRADE_WEBHOOK_TOKEN", "upgrade-token")
    assert settings_from_env().token == "upgrade-token"


def test_commit_gate_covers_every_stack_member(profile):
    pre, post = baseline(), baseline(transcript(profile.target_version))
    pre["software"]["2"] = dict(pre["software"]["1"])
    post["software"]["2"] = dict(post["software"]["1"])
    assert any(f["check"] == "install_commit" for f in workflow.target_findings(post, profile, pre))
    post["raw"]["show install summary"] = post["raw"]["show install summary"].replace("Switch 1", "Switch 1 2")
    assert not any(f["check"] == "install_commit" for f in workflow.target_findings(post, profile, pre))


def test_preflight_rejects_partial_commit_and_active_abort(profile):
    snapshot = baseline()
    snapshot["raw"]["show install summary"] = "IMG C 17.9.4a\nAuto abort timer: active"
    blockers = workflow.preflight(snapshot, profile)["blockers"]
    assert any("every stack member" in b for b in blockers)
    assert any("auto-abort" in b for b in blockers)


def test_vrf_routes_are_collected_and_missing_post_routes_fail():
    raw = transcript()
    raw["show vrf"] += "  users                            <not set>            ipv4        Vl10\n"
    raw["show ip route vrf users"] = "Routing Table: users\n" + raw["show ip route"]
    before = baseline(raw)
    assert not before["errors"]
    assert len(before["tables"]["show ip route vrf users"]) == 1
    after = baseline()
    assert any(f["check"] == "show ip route vrf users" for f in checks.compare(before, after))


def test_bgp_vrf_is_explicitly_collected():
    routing, errors = checks.routing_checks("router bgp 65000\n address-family ipv4 vrf users\n exit-address-family\n address-family ipv6 vrf users\n exit-address-family\n!\n")
    assert not errors
    assert ("show bgp vpnv4 unicast vrf users summary", "bgp") in routing.values()
    assert ("show bgp vpnv6 unicast vrf users summary", "bgp") in routing.values()


def test_ntp_cpu_memory_and_alarm_regressions():
    raw = transcript()
    raw.update({
        "show processes cpu sorted": "CPU utilization for five seconds: 5%/0%; one minute: 4%; five minutes: 3%",
        "show processes memory sorted": "Processor Pool Total: 1000 Used: 500 Free: 500",
        "show environment all": "Switch 1 FAN 1 is OK",
        "show ntp associations": "     address         ref clock     st  when  poll reach  delay  offset disp\n*~10.0.0.10       1.1.1.1          2    20    64   377  0.1  0.1 0.1\n",
    })
    before = baseline(raw)
    raw["show processes cpu sorted"] = raw["show processes cpu sorted"].replace("one minute: 4%", "one minute: 95%")
    raw["show processes memory sorted"] = "Processor Pool Total: 1000 Used: 950 Free: 50"
    raw["show environment all"] = "Switch 1 FAN 1 is FAULTY"
    raw["show ntp associations"] = raw["show ntp associations"].replace("*~", " ~").replace("377", "0")
    findings = checks.compare(before, baseline(raw))
    for name in ("ntp", "cpu", "memory", "environment"):
        assert any(f["check"] == name and f["severity"] == "error" for f in findings)


def test_member_flash_reserves_image_copy_space(profile, options):
    snapshot, plan = baseline(), {}
    device = Mock(options=options)
    device.read.side_effect = [f"1 -rw- 1000000000 Sep 11 2026 {profile.image}",
                              "9999999999 bytes total (2000000000 bytes free)"]
    with pytest.raises(ValueError, match="insufficient"):
        workflow.verify_image(device, profile, snapshot, plan, apply=False)
    device.write.assert_not_called()


def test_source_copy_checksum_failure_never_changes_boot(profile, options, fake_device):
    from dataclasses import replace
    options.apply = True
    fake_device.image_exists = False
    fake_device.digest = "b" * 32
    profile = replace(profile, image_source="https://images.example.com/" + profile.image)
    result, reporter = run_device(profile, options)
    assert result.failed and result.changed
    fake_device.instances[0].connection.send_config_set.assert_not_called()
    assert not any(c.startswith("install") for c in fake_device.instances[0].mutations)


def test_image_staging_success(profile, options, fake_device, monkeypatch):
    from dataclasses import replace
    options.apply = True
    fake_device.image_exists = False
    original = fake_device.interactive

    def copied(self, command, timeout, reload=False):
        original(self, command, timeout, reload)
        if command.startswith("copy"):
            self.image_exists = True

    monkeypatch.setattr(fake_device, "interactive", copied)
    profile = replace(profile, image_source="https://images.example.com/" + profile.image)
    result, _ = run_device(profile, options)
    assert not result.failed and result.changed
    assert fake_device.instances[0].mutations[0] == "write memory"
    assert fake_device.instances[0].mutations[1].startswith("copy https://")


def test_stale_configuration_blocks_boot_write(profile, options, fake_device, monkeypatch):
    options.apply = True
    original = fake_device.write

    def drift(self, command, timeout=120):
        output = original(self, command, timeout)
        if command.startswith("verify"):
            self.raw = dict(self.raw, **{"show running-config": self.raw["show running-config"] + "\ninterface Vlan99"})
        return output

    monkeypatch.setattr(fake_device, "write", drift)
    result, _ = run_device(profile, options)
    assert result.failed and not result.changed
    fake_device.instances[0].connection.send_config_set.assert_not_called()


def test_reconnect_waits_through_old_version_and_unreachable(options, profile, monkeypatch):
    device = workflow.Device(None, options, Mock())
    device.close = Mock()
    device.connect = Mock(side_effect=[OSError("down"), None, None])
    device.read = Mock(side_effect=[transcript()["show version"], transcript(profile.target_version)["show version"]])
    monkeypatch.setattr(workflow.time, "sleep", lambda _: None)
    device.wait_for_target(profile)
    assert device.connect.call_count == 3


def test_reconnect_timeout_is_bounded(options, profile, monkeypatch):
    device = workflow.Device(None, options, Mock())
    device.close = Mock()
    device.connect = Mock(side_effect=OSError("down"))
    counter = iter([0, 0, 4])
    monkeypatch.setattr(workflow.time, "monotonic", lambda: next(counter))
    monkeypatch.setattr(workflow.time, "sleep", lambda _: None)
    with pytest.raises(TimeoutError, match="manual recovery"):
        device.wait_for_target(profile)


def test_transient_postcheck_recovers_before_deadline(profile, options, fake_device, monkeypatch):
    options.apply = True
    options.validation_timeout = 5
    original = checks.collect
    calls = []

    def transient(*args, **kwargs):
        snapshot = original(*args, **kwargs)
        calls.append(snapshot)
        if len(calls) == 2:
            snapshot["tables"]["mac"] = []
        return snapshot

    monkeypatch.setattr(checks, "collect", transient)
    result, reporter = run_device(profile, options)
    assert not result.failed
    assert len(calls) == 4  # baseline, failed post, two passing samples


def test_install_timeout_verifies_outcome_without_retry(profile, options, fake_device, monkeypatch):
    options.apply = True

    def timeout(self, command, timeout, reload=False):
        self.mutations.append(command)
        raise TimeoutError("lost dialogue")

    monkeypatch.setattr(fake_device, "interactive", timeout)
    result, reporter = run_device(profile, options)
    assert not result.failed
    assert len([c for c in fake_device.instances[0].mutations if c.startswith("install")]) == 1


def test_progress_failure_is_durable(tmp_path, monkeypatch):
    from netops import archive, webhook
    from netops.upgrade.progress import Reporter
    run = archive.Run([], tmp_path)
    run.args = SimpleNamespace(command="upgrade", apply=True)
    run.prepare()
    monkeypatch.setattr(webhook, "send", Mock(side_effect=webhook.WebhookError("offline")))
    monkeypatch.setattr("netops.upgrade.progress.time.sleep", lambda _: None)
    reporter = Reporter(run, webhook.Settings("https://example.com/events", "secret"))
    host = SimpleNamespace(name="sw1", hostname="192.0.2.1", platform="cisco_ios", data={})
    assert not reporter.emit(host, "completed", "Finished")
    assert reporter.delivery_failed
    doc = json.loads(run.path.read_text())
    assert doc["devices"]["sw1"]["upgrade_events"][0]["delivery"] == "failed"


@pytest.mark.parametrize("selection,netbox", [([], True), (["--csv", "lab.csv"], False)])
def test_cli_shares_env_and_targeting_but_defaults_to_netbox(profile, tmp_path, monkeypatch, selection, netbox):
    from netops import cli
    data = asdict(profile)
    data["models"], data["starting_versions"] = list(data["models"]), list(data["starting_versions"])
    path = tmp_path / "path.yaml"
    path.write_text(yaml.safe_dump(data))
    env = tmp_path / "shared.env"
    env.write_text("NET_AWS_SECRET=network/device-login\nNETOPS_INVENTORY=csv\n")
    seen = []
    monkeypatch.setattr(cli, "_connect", lambda args, style: seen.append(args) or (None, None, 0))
    assert cli.main(["upgrade", "--profile", str(path), "--env-file", str(env),
                     "--netbox-filter", "site=atl", "--netbox-filter", "role=access", *selection]) == 0
    assert seen[0].netbox is netbox
    assert seen[0].netbox_filter == ["site=atl", "role=access"]
    assert seen[0].aws_secret == "network/device-login"


def test_actual_bundle_conversion(profile, options, fake_device):
    from dataclasses import replace
    options.apply = True
    fake_device.raw = transcript(mode="BUNDLE")
    for command in ("show running-config", "show startup-config"):
        fake_device.raw[command] = fake_device.raw[command].replace("flash:packages.conf", "flash:cat9k_iosxe.17.09.04a.SPA.bin")
    profile = replace(profile, bundle_conversion_validated=True)
    result, reporter = run_device(profile, options)
    assert not result.failed and result.changed
    assert any(call.args[1] == "bundle_mode_flagged" for call in reporter.emit.call_args_list)
    fake_device.instances[0].connection.send_config_set.assert_called_once()


def test_boot_conversion_prompt_and_reload_prompt(monkeypatch, options):
    conn = Mock()
    conn.find_prompt.return_value = "sw1#"
    conn.read_channel.side_effect = [
        "Please confirm you have changed boot config to flash:packages.conf [y/n]",
        "\nThis operation requires a reload of the system. Do you want to proceed? [y/n]",
        "\nSUCCESS: install finished\nsw1#",
    ]
    conn.is_alive.return_value = True
    monkeypatch.setattr(workflow.time, "sleep", lambda _: None)
    device = workflow.Device(None, options, Mock())
    device.connection = conn
    device.interactive("install add file flash:x.bin activate commit", 30, reload=True)
    assert [call.args[0] for call in conn.write_channel.call_args_list] == ["install add file flash:x.bin activate commit\n", "y\n", "y\n"]


def test_eigrp_vrf_and_wrapped_ipv6_peers():
    text = "EIGRP-IPv6 Neighbors for AS(10) VRF(users)\nH Address Interface Hold Uptime SRTT RTO Q Seq\n0 FE80::A:B:C:D\n  Gi1/0/1 12 01:00:00 2 100 0 22\n"
    rows = checks.peers(text, "eigrp")
    assert rows == [{"as": "10", "ip_address": "FE80::A:B:C:D", "interface": "Gi1/0/1", "q_cnt": "0"}]
    with pytest.raises(ValueError, match="incomplete"):
        checks.peers(text.replace("  Gi1/0/1 12 01:00:00 2 100 0 22", ""), "eigrp")


def test_ospfv3_scopes_preserve_address_family():
    text = "OSPFv3 10 address-family ipv4\nNeighbor ID Pri State Dead Time Interface ID Interface\n1.1.1.1 1 FULL/DR 00:00:30 10 Gi1/0/1\nOSPFv3 10 address-family ipv6\nNeighbor ID Pri State Dead Time Interface ID Interface\n1.1.1.1 1 FULL/DR 00:00:30 10 Gi1/0/1\n"
    rows = checks.peers(text, "ospf")
    assert len(rows) == 2 and rows[0]["scope"] != rows[1]["scope"]


def test_threaded_fleet_isolates_device_failure(profile, options, fake_device):
    from netops.inventory import init_from_records
    nr = init_from_records(records={
        "good": {"hostname": "192.0.2.1", "platform": "cisco_ios"},
        "bad": {"hostname": "192.0.2.2", "platform": "arista_eos"},
    }, username="test", password="test", secret=None, key_file=None, port=22, workers=2, conn_timeout=1)
    reporter = Mock()
    reporter.emit.return_value = True
    try:
        results = nr.run(task=workflow.upgrade_device, profile=profile, options=options, reporter=reporter)
        assert results["bad"].failed and not results["good"].failed
        assert not fake_device.instances[0].mutations
    finally:
        nr.close_connections()


@pytest.mark.parametrize("apply", [False, True])
def test_stage_only_existing_image_never_mutates_or_collects_full_baseline(profile, options, fake_device, monkeypatch, apply):
    options.stage_only, options.apply = True, apply
    monkeypatch.setattr(checks, "collect", Mock(side_effect=AssertionError("full baseline is not a staging step")))
    result, reporter = run_device(profile, options)
    assert not result.failed and not result.changed
    assert result.result["commands"] == []
    assert result.result["image_verification"] == "verified"
    device = fake_device.instances[0]
    assert not device.mutations
    device.connection.send_config_set.assert_not_called()
    assert reporter.emit.call_args.args[1] == ("staged" if apply else "dry_run_complete")


def test_stage_only_dry_run_plans_http_copy_without_writing(profile, options, fake_device):
    from dataclasses import replace
    options.stage_only = True
    fake_device.image_exists = False
    profile = replace(profile, image_source="http://images.example.com/" + profile.image)
    result, reporter = run_device(profile, options)
    assert not result.failed and not result.changed
    assert result.result["commands"] == [f"copy {profile.image_source} flash:{profile.image}"]
    assert result.result["image_verification"] == "pending_transfer"
    assert not fake_device.instances[0].mutations


def test_stage_only_http_copy_verifies_and_stops(profile, options, fake_device, monkeypatch):
    from dataclasses import replace
    options.stage_only = options.apply = True
    fake_device.image_exists = False
    profile = replace(profile, image_source="http://images.example.com/" + profile.image)

    def copy_image(self, command, timeout, reload=False):
        assert not reload
        self.mutations.append(command)
        self.image_exists = True

    monkeypatch.setattr(fake_device, "interactive", copy_image)
    monkeypatch.setattr(fake_device, "wait_for_target", Mock(side_effect=AssertionError("must never reload")))
    result, reporter = run_device(profile, options)
    assert not result.failed and result.changed
    assert result.result["image_verification"] == "verified"
    device = fake_device.instances[0]
    assert device.mutations == [f"copy {profile.image_source} flash:{profile.image}"]
    device.connection.send_config_set.assert_not_called()
    assert reporter.emit.call_args.args[1] == "staged"


def test_stage_only_bad_existing_checksum_is_not_overwritten(profile, options, fake_device):
    options.stage_only = options.apply = True
    fake_device.digest = "b" * 32
    result, reporter = run_device(profile, options)
    assert result.failed and not result.changed
    assert reporter.emit.call_args.args[1] == "blocked"
    assert not fake_device.instances[0].mutations


def test_stage_only_bad_download_reports_staging_failure(profile, options, fake_device):
    from dataclasses import replace
    options.stage_only = options.apply = True
    fake_device.image_exists = False
    fake_device.digest = "b" * 32
    profile = replace(profile, image_source="http://images.example.com/" + profile.image)
    result, reporter = run_device(profile, options)
    assert result.failed and result.changed
    assert reporter.emit.call_args.args[1] == "staging_failed"
    fake_device.instances[0].connection.send_config_set.assert_not_called()


def test_stage_only_bundle_is_allowed_without_conversion_approval(profile, options, fake_device):
    options.stage_only = options.apply = True
    fake_device.raw = transcript(mode="BUNDLE")
    result, reporter = run_device(profile, options)
    assert not result.failed and not result.changed
    assert result.result["bundle_conversion"]
    assert not profile.bundle_conversion_validated
    assert not fake_device.instances[0].mutations


def test_stage_only_still_checks_image_when_already_on_target(profile, options, fake_device):
    options.stage_only = options.apply = True
    fake_device.raw = transcript(profile.target_version)
    fake_device.image_exists = False
    result, reporter = run_device(profile, options)
    assert result.failed  # No source URL, despite target already running.
    assert "image missing" in " ".join(result.result["blockers"])
    assert reporter.emit.call_args.args[1] == "blocked"


def test_stage_only_retains_starting_version_gate(profile, options, fake_device):
    options.stage_only = options.apply = True
    fake_device.raw = transcript("17.6.5")
    result, _ = run_device(profile, options)
    assert result.failed and not result.changed
    assert not fake_device.instances[0].mutations


def test_stage_only_cli_and_archive_operation():
    from netops import cli, archive
    args = cli.build_parser().parse_args(["upgrade", "--profile", "test.yaml", "--stage-only", "--apply"])
    assert args.stage_only and args.apply
    assert archive.action(args, None, None, {})["action"] == "stage_image"


def test_http_copy_accepts_only_default_destination_prompt(monkeypatch, options, profile):
    conn = Mock()
    conn.find_prompt.return_value = "sw1#"
    conn.read_channel.side_effect = [f"Destination filename [{profile.image}]?", "\n1000 bytes copied\nsw1#"]
    conn.is_alive.return_value = True
    monkeypatch.setattr(workflow.time, "sleep", lambda _: None)
    device = workflow.Device(None, options, Mock())
    device.connection = conn
    command = f"copy http://images.example.com/{profile.image} flash:{profile.image}"
    device.interactive(command, 30)
    assert [call.args[0] for call in conn.write_channel.call_args_list] == [command + "\n", "\n"]


@pytest.fixture
def c9350_profile(profile):
    from dataclasses import replace
    return replace(profile, models=("C9350-48P",), starting_versions=("17.18.1",),
                   target_version="17.18.4", image="cisco9k_iosxe.17.18.04.SPA.bin")


def write_profile_file(profile, tmp_path):
    data = asdict(profile)
    data["models"] = list(profile.models)
    data["starting_versions"] = list(profile.starting_versions)
    path = tmp_path / "path.yaml"
    path.write_text(yaml.safe_dump(data))
    return path


@pytest.mark.parametrize("npe", [False, True])
def test_c9350_profile_accepts_its_own_image_packages(c9350_profile, tmp_path, npe):
    from dataclasses import replace
    if npe:
        c9350_profile = replace(c9350_profile, image="cisco9k_iosxe_npe.17.18.04.SPA.bin")
    assert Profile.load(write_profile_file(c9350_profile, tmp_path)) == c9350_profile


@pytest.mark.parametrize("family", ["C9300", "C9350", "mixed"])
def test_wrong_family_images_are_rejected(profile, c9350_profile, tmp_path, family):
    from dataclasses import replace
    if family == "C9300":
        bad = replace(c9350_profile, models=profile.models)
    elif family == "C9350":
        bad = replace(c9350_profile, image="cat9k_iosxe.17.18.04.SPA.bin")
    else:
        bad = replace(c9350_profile, models=("C9300-48P", "C9350-48P"))
    with pytest.raises(ValueError, match="package|separate profiles"):
        Profile.load(write_profile_file(bad, tmp_path))


@pytest.mark.parametrize("model,starting,target", [
    ("C9350-48P", "17.17.1", "17.18.4"),
    ("C9350-24HX", "17.18.1", "26.1.2"),
    ("C9350-48HXN", "26.1.1", "26.1.2"),
])
def test_c9350_introductory_release_gate(c9350_profile, tmp_path, model, starting, target):
    from dataclasses import replace
    bad = replace(c9350_profile, models=(model,), starting_versions=(starting,),
                  target_version=target, image=f"cisco9k_iosxe.{target}.SPA.bin")
    with pytest.raises(ValueError, match="starting versions"):
        Profile.load(write_profile_file(bad, tmp_path))


def test_c9350_26x_profile(c9350_profile, tmp_path):
    from dataclasses import replace
    approved = replace(c9350_profile, models=("C9350-24HX", "C9350-48HXN"),
                       starting_versions=("26.1.1a",), target_version="26.1.2",
                       image="cisco9k_iosxe.26.1.02.SPA.bin")
    assert Profile.load(write_profile_file(approved, tmp_path)) == approved


def c9350_transcript(release="17.18.1", mode="INSTALL"):
    # Synthetic CLI fixture: validates driver behavior, not a hardware capture.
    return {command: value.replace("C9300-48P", "C9350-48P").replace("CAT9K_IOSXE", "CISCO9K_IOSXE")
            for command, value in transcript(release, mode).items()}


@pytest.mark.parametrize("operation", ["dry_run", "upgrade", "bundle_conversion", "stage_only"])
def test_c9350_workflows_use_cisco9k_image(c9350_profile, options, fake_device, monkeypatch, operation):
    from dataclasses import replace
    options.apply = operation != "dry_run"
    options.stage_only = operation == "stage_only"
    profile = replace(c9350_profile, image_source="http://images.example.com/" + c9350_profile.image,
                      bundle_conversion_validated=operation == "bundle_conversion")

    class C9350Device(fake_device):
        raw = c9350_transcript(mode="BUNDLE" if operation == "bundle_conversion" else "INSTALL")
        image_exists = operation != "stage_only"

        def read(self, command):
            if command == f"dir flash:{profile.image}":
                return (f"1 -rw- 1000 Sep 11 2026 {profile.image}" if self.image_exists else
                        "%Error opening flash:image (No such file or directory)")
            return super().read(command)

        def interactive(self, command, timeout, reload=False):
            super().interactive(command, timeout, reload)
            if command.startswith("copy "):
                self.image_exists = True

        def wait_for_target(self, profile):
            self.raw = c9350_transcript(profile.target_version)

    monkeypatch.setattr(workflow, "Device", C9350Device)
    result, reporter = run_device(profile, options)
    assert not result.failed
    device = C9350Device.instances[-1]
    if operation in {"upgrade", "bundle_conversion"}:
        assert f"install add file flash:{profile.image} activate commit" in device.mutations
    elif operation == "stage_only":
        assert device.mutations == [f"copy {profile.image_source} flash:{profile.image}"]
        device.connection.send_config_set.assert_not_called()
    else:
        assert not device.mutations



def test_normalization_ignores_compressed_startup_and_post_reload_headers():
    body = "hostname sw1\n!\ninterface GigabitEthernet1/0/1\n switchport access vlan 10\n!\nend"
    running = ("Building configuration...\n\nCurrent configuration : 140898 bytes\n!\n"
               "! No configuration change since last restart\n"
               "! NVRAM config last updated at 23:28:02 EDT Fri Sep 11 2026 by admin\n!\nversion 17.18\n" + body)
    startup = ("Using 140898 out of 2097152 bytes, uncompressed size = 140898 bytes\n"
               "Uncompressed configuration from 30125 bytes to 140898 bytes\n!\n"
               "! Last configuration change at 18:22:49 EDT Fri Sep 11 2026\n"
               "! NVRAM config last updated at 23:28:02 EDT Fri Sep 11 2026 by admin\n!\nversion 17.18\n" + body)
    assert checks.normalized_config(running) == checks.normalized_config(startup)
    assert checks.normalized_config(running).startswith("hostname sw1")
    assert checks.normalized_config(running) != checks.normalized_config(startup.replace("vlan 10", "vlan 20"))


def test_normalization_without_a_version_line_still_strips_known_headers():
    text = "Building configuration...\n! Last configuration change at 12:00\nhostname sw1\n"
    assert checks.normalized_config(text) == "hostname sw1"


def test_compressed_startup_config_is_not_reported_as_unsaved(profile):
    raw = transcript()
    raw["show startup-config"] = ("Using 222 out of 2097152 bytes, uncompressed size = 222 bytes\n"
                                  "Uncompressed configuration from 100 bytes to 222 bytes\n" + raw["show startup-config"])
    raw["show running-config"] = raw["show running-config"].replace(
        "! Last configuration change at 12:00", "! No configuration change since last restart")
    plan = workflow.preflight(baseline(raw), profile)
    assert not any("running/startup configuration differ" in reason for reason in plan["blockers"])
    assert "saved_config_diff" not in plan


@pytest.mark.parametrize("status", ["act/lshut", "sus/lshut", "act/ishut", "suspended"])
def test_vlan_rows_with_local_shutdown_statuses_are_counted(status):
    raw = transcript()
    raw["show vlan brief"] += f"30   QUARANTINE                       {status} Gi1/0/3\n"
    snapshot = baseline(raw)
    assert "vlans" not in snapshot["errors"]
    assert {row["vlan_id"] for row in snapshot["tables"]["vlans"]} == {"1", "10", "30"}


def test_vlan_count_still_fails_closed_when_the_parser_drops_a_row(monkeypatch):
    monkeypatch.setattr(checks, "parse_output",
                        lambda **kwargs: [{"vlan_id": "1", "vlan_name": "default", "status": "active", "interfaces": []}])
    with pytest.raises(ValueError, match="did not account for every table row"):
        checks.table("show vlan brief", *checks.TABLES["vlans"][1:4], transcript()["show vlan brief"])


def unsaved_transcript():
    raw = transcript()
    raw["show running-config"] = raw["show running-config"].replace("switchport access vlan 10", "switchport access vlan 20")
    return raw


def stages(reporter):
    return [call.args[1] for call in reporter.emit.call_args_list]


def plan_from(reporter, stage="precheck_complete"):
    return next(call.args[3]["upgrade_plan"] for call in reporter.emit.call_args_list if call.args[1] == stage)


def test_apply_saves_running_config_before_comparing_it(profile, options, fake_device, monkeypatch):
    options.apply = True
    fake_device.raw = unsaved_transcript()
    original_wait = fake_device.wait_for_target

    def reload_keeps_saved_config(self, profile):
        original_wait(self, profile)
        for command in ("show running-config", "show startup-config"):
            self.raw[command] = self.raw[command].replace("switchport access vlan 10", "switchport access vlan 20")

    monkeypatch.setattr(fake_device, "wait_for_target", reload_keeps_saved_config)
    result, reporter = run_device(profile, options)
    assert not result.failed
    assert fake_device.instances[0].mutations == ["write memory", "write memory", f"install add file flash:{profile.image} activate commit"]
    seen = stages(reporter)
    assert seen.index("saving_config") < seen.index("precheck")
    assert "unsaved_changes" not in seen
    plan = plan_from(reporter)
    assert plan["configuration_saved"] and not plan["unsaved_changes"]


def test_dry_run_flags_unsaved_changes_without_saving(profile, options, fake_device):
    fake_device.raw = unsaved_transcript()
    result, reporter = run_device(profile, options)
    assert not result.failed
    assert not fake_device.instances[0].mutations
    assert "unsaved_changes" in stages(reporter)
    assert stages(reporter)[-1] == "dry_run_complete"
    assert "saves the unsaved running configuration" in reporter.emit.call_args.args[2]
    plan = plan_from(reporter, "dry_run_complete")
    assert plan["unsaved_changes"] and not plan["configuration_saved"]
    assert "+ switchport access vlan 20" in plan["saved_config_diff"]


def test_save_that_does_not_take_blocks_before_any_boot_change(profile, options, fake_device, monkeypatch):
    options.apply = True
    fake_device.raw = unsaved_transcript()
    original = fake_device.write

    def nvram_keeps_old_config(self, command, timeout=120):
        output = original(self, command, timeout)
        if command == "write memory":
            self.raw["show startup-config"] = transcript()["show startup-config"]
        return output

    monkeypatch.setattr(fake_device, "write", nvram_keeps_old_config)
    result, reporter = run_device(profile, options)
    assert result.failed and not result.changed
    assert stages(reporter)[-1] == "blocked"
    assert "still differ after write memory" in reporter.emit.call_args.args[2]
    assert fake_device.instances[0].mutations == ["write memory"]
    fake_device.instances[0].connection.send_config_set.assert_not_called()


def test_unacknowledged_save_fails_before_prechecks(profile, options, fake_device, monkeypatch):
    options.apply = True
    monkeypatch.setattr(fake_device, "write", lambda self, command, timeout=120: "Building configuration...\n% Error: NVRAM write failed")
    result, reporter = run_device(profile, options)
    assert result.failed and not result.changed
    assert stages(reporter)[-1] == "failed"
    assert "precheck" not in stages(reporter)


def test_config_dumps_use_the_config_timeout():
    device = workflow.Device(SimpleNamespace(), SimpleNamespace(show_timeout=60, config_timeout=300), Mock())
    device.connection = Mock()
    for command in ("show running-config", "show startup-config", "show version"):
        device.read(command)
    assert [c.kwargs["read_timeout"] for c in device.connection.send_command.call_args_list] == [300, 300, 60]


def test_cli_config_timeout_default_and_override():
    from netops.cli import build_parser
    assert build_parser().parse_args(["upgrade", "--profile", "p.yaml"]).config_timeout == 300
    assert build_parser().parse_args(["upgrade-poll", "--config-timeout", "900"]).config_timeout == 900
