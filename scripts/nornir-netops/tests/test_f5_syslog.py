"""System syslog through real CLI/inventory dispatch and simulated F5 APIs."""

import copy
import json

import pytest

from netops import cli, f5_syslog
from test_waf import setup as waf_setup, ROOT, OLD_DATE, COLLECTORS

SYSLOG = f5_syslog.SYSLOG
RETAINED = {"name": "/Common/existing", "host": "192.0.2.50", "remotePort": 514,
            "localIp": "192.0.2.10", "description": "Preserve this collector"}
EXTRA = {"name": "old", "host": "192.0.2.99", "remotePort": 514}


@pytest.fixture
def setup(waf_setup):
    waf_setup.box.app["servers"] = copy.deepcopy(COLLECTORS)
    waf_setup.box.responses[SYSLOG] = {
        "remoteServers": [copy.deepcopy(RETAINED), copy.deepcopy(EXTRA)],
        "include": "custom syslog-ng configuration", "consoleLog": "disabled",
        "messagesFrom": "notice", "authPrivTo": "emerg",
    }
    original_run = waf_setup.run
    waf_setup.run = lambda *args, **kwargs: original_run(*args, feature="syslog", **kwargs)
    yield waf_setup
    # Includes failed and audit-only runs: no WAF/provisioning API reads.
    assert set(waf_setup.box.reads) <= {SYSLOG}


def test_dry_run_previews_without_writes(setup):
    code, report = setup.run("--fail-on-diff", netbox_inventory=True)
    assert code == cli.EXIT_DIFF
    row = report["devices"]["f5"]
    assert row["add"] == ["192.0.2.51:1514"]
    assert row["remove"] == ["192.0.2.99:514"]
    assert row["netbox_writeback"]["status"] == "planned"
    assert setup.box.writes == setup.nb.writes == []
    assert setup.box.saves == 0


@pytest.mark.parametrize("policy", ["syslog-add", "syslog-manage", "syslog-audit", None])
def test_netbox_policies_preserve_unrelated_settings_and_verify(setup, policy):
    setup.nb.device["tags"] = [{"slug": policy}] if policy else []
    before = copy.deepcopy(setup.box.responses[SYSLOG])
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["netbox_writeback"]["status"] == "written"
    assert row["syslog_compliant"] is (policy == "syslog-manage")
    if policy in (None, "syslog-audit"):
        assert setup.box.writes == []
        assert setup.box.saves == 0
    else:
        assert [path for path, _ in setup.box.writes] == [SYSLOG]
        patch = setup.box.writes[0][1]
        assert set(patch) == {"remoteServers"}
        assert patch["remoteServers"][0] == RETAINED
        assert (EXTRA in patch["remoteServers"]) is (policy == "syslog-add")
        assert row["verified"] is True
        assert setup.box.saves == 1
    after = setup.box.responses[SYSLOG]
    assert {k: v for k, v in after.items() if k != "remoteServers"} == {
        k: v for k, v in before.items() if k != "remoteServers"}
    assert ROOT not in setup.box.reads

    assert row["backout"]["complete"]
    for step in row["backout"]["steps"]:
        if step["purpose"] == "restore":
            setup.box.patch_json(step["path"], step["body"])
    assert setup.box.responses[SYSLOG]["remoteServers"] == before["remoteServers"]


@pytest.mark.parametrize("replace", [False, True])
def test_direct_ip_uses_cli_mode_and_tls_options(setup, replace):
    code, report = setup.run("--apply", "--f5-insecure", *(["--replace"] if replace else []), direct=True)
    assert code == cli.EXIT_OK
    assert setup.box.logins[0][4]["verify_tls"] is False
    assert report["devices"]["192.0.2.1"]["syslog_compliant"] is replace
    assert setup.nb.calls == []


def test_empty_remote_list_is_configured(setup):
    setup.box.responses[SYSLOG].pop("remoteServers")
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert len(setup.box.responses[SYSLOG]["remoteServers"]) == 2
    assert report["devices"]["f5"]["syslog_compliant"] is True


@pytest.mark.parametrize("failure", ["reject", "ignore", "save", "discovery"])
def test_failed_apply_or_verification_does_not_stamp(setup, failure):
    if failure == "reject":
        setup.box.reject.add(SYSLOG)
    elif failure == "ignore":
        setup.box.ignore.add(SYSLOG)
    elif failure == "save":
        setup.box.save_failure = True
    else:
        setup.box.responses[SYSLOG] = RuntimeError("discovery failed")
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert setup.nb.writes == []
    assert setup.nb.device["custom_fields"]["syslog_last_checked"] == OLD_DATE
    assert report["devices"]["f5"]["syslog_compliant"] is None
    if failure != "save":
        assert setup.box.saves == 0


def test_no_verify_leaves_fields_unchanged(setup):
    code, report = setup.run("--apply", "--no-verify", "--no-save", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert setup.nb.writes == [] and setup.box.saves == 0
    assert report["devices"]["f5"]["checked_at"] is None


def test_conflicting_tags_fail_before_f5_login(setup):
    setup.nb.device["tags"] = [{"slug": "syslog-add"}, {"slug": "syslog-manage"}]
    code, _ = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert setup.box.logins == setup.nb.writes == []


def test_f5_ignores_switch_scalars_and_accepts_destination_override(setup):
    code, report = setup.run("--apply", "--replace", "--destination", "192.0.2.60:1514",
                             "--severity", "debugging", "--source", "Loopback0", "--origin-id", "hostname")
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["desired"] == ["192.0.2.60:1514"]
    assert any("collector IPs and ports only" in note for note in report["devices"]["f5"]["notes"])


def test_f5_rejects_dns_destinations_before_login(setup):
    code, _ = setup.run("--apply", "--destination", "logs.example.com")
    assert code == cli.EXIT_FAILED
    assert setup.box.logins == []


def test_plan_normalizes_ipv6_and_route_domains_and_removes_duplicates():
    wanted = [("2001:db8::50", 514), ("192.0.2.50", 514)]
    doc = {"remoteServers": [
        {"name": "v6", "host": "2001:0db8::50", "remotePort": "514"},
        {"name": "v4", "host": "192.0.2.50%0"},
        {"name": "duplicate", "host": "192.0.2.50"},
        {"name": "rd", "host": "192.0.2.50%2"},
    ]}
    plan = f5_syslog.plan_syslog(doc, wanted, True)
    assert plan.add == []
    assert len(plan.extra) == 2
    assert plan.payload["remoteServers"] == doc["remoteServers"][:2]


def test_generated_name_collision_preserves_existing_entry():
    wanted = [("192.0.2.50", 514)]
    generated = f5_syslog.plan_syslog({}, wanted, False).payload["remoteServers"][0]["name"]
    old = {"name": "/Common/" + generated, "host": "192.0.2.99"}
    plan = f5_syslog.plan_syslog({"remoteServers": [old]}, wanted, False)
    assert plan.payload["remoteServers"][0] == old
    assert plan.payload["remoteServers"][1]["name"] == generated + "_1"


@pytest.mark.parametrize("document", [{"items": []}, {"remoteServers": "none"},
                                      {"remoteServers": [{"host": "192.0.2.1"}]},
                                      {"remoteServers": [{"name": "x", "host": "192.0.2.1", "remotePort": 0}]}])
def test_malformed_remote_servers_fail_closed(document):
    with pytest.raises(ValueError):
        f5_syslog.plan_syslog(document, [("192.0.2.50", 514)], True)


@pytest.mark.parametrize("waf_state", ["drift", "unavailable", "missing"])
def test_system_syslog_does_not_read_or_depend_on_waf(setup, waf_state):
    if waf_state == "drift":
        setup.box.app["servers"] = [{"name": "192.0.2.99:514"}]
    elif waf_state == "unavailable":
        setup.box.responses[ROOT] = RuntimeError("WAF feature is not available")
    else:
        setup.box.responses.pop(ROOT)
    before = copy.deepcopy(setup.box.app)
    setup.nb.device["custom_fields"]["syslog_compliant"] = False
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["system_syslog_compliant"] is True
    assert row["syslog_compliant"] is True
    assert "waf_audit" not in row
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True
    assert setup.box.app == before
    assert [path for path, _ in setup.box.writes] == [SYSLOG]
    assert row["verified"] and setup.box.saves == 1
    assert row["configuration_scope"] == "syslog"
    assert all(step["path"] == SYSLOG for step in row["current_config"]["read_steps"])
    assert all(step["path"] == SYSLOG for step in row["backout"]["steps"] if step["purpose"] == "restore")
