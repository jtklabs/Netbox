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
    return waf_setup


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
    assert ROOT in setup.box.reads

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


def test_waf_drift_prevents_combined_compliance_but_is_not_changed(setup):
    setup.box.app["servers"] = [{"name": "192.0.2.99:514"}]
    before = copy.deepcopy(setup.box.app)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["system_syslog_compliant"] is True
    assert row["waf_audit"]["compliant"] is False
    assert row["syslog_compliant"] is False
    assert setup.box.app == before
    assert [path for path, _ in setup.box.writes] == [SYSLOG]


@pytest.mark.parametrize("endpoint,error", [
    (ROOT, RuntimeError("WAF feature is not available (404)")),
    (ROOT, RuntimeError("WAF access forbidden (403)")),
    (ROOT, RuntimeError("WAF discovery timed out")),
    (f5_syslog.ASM_PROVISION, RuntimeError("provisioning read forbidden (403)")),
])
def test_optional_waf_audit_failure_does_not_block_system_syslog(setup, endpoint, error):
    setup.box.responses[endpoint] = error
    setup.nb.device["custom_fields"]["syslog_compliant"] = False
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert [path for path, _ in setup.box.writes] == [SYSLOG]
    assert setup.box.saves == 1 and row["verified"] is True
    assert row["system_syslog_compliant"] is True
    assert row["waf_audit"]["status"] == "unavailable"
    assert row["waf_audit"]["compliant"] is None
    assert row["syslog_compliant"] is None
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is False
    assert setup.nb.device["custom_fields"]["syslog_last_checked"] != OLD_DATE
    if endpoint == f5_syslog.ASM_PROVISION:
        assert ROOT not in setup.box.reads


def test_unknown_waf_ownership_preserves_compliance(setup):
    setup.box.responses[ROOT]["items"][0].pop("builtIn")
    setup.nb.device["custom_fields"]["syslog_compliant"] = False
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["syslog_compliant"] is None
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is False


def test_builtin_waf_is_not_part_of_system_syslog_compliance(setup):
    setup.box.responses[ROOT]["items"][0]["builtIn"] = "enabled"
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["syslog_compliant"] is True
    assert report["devices"]["f5"]["waf_audit"]["applicable_profiles"] == 0


@pytest.mark.parametrize("apply", [False, True])
@pytest.mark.parametrize("policy", ["audit", "add", "manage"])
def test_system_syslog_without_waf_provisioning(setup, apply, policy):
    setup.box.responses[f5_syslog.ASM_PROVISION] = {"name": "asm", "level": "none"}
    setup.box.responses[ROOT] = RuntimeError("security logging feature is not available")
    code, report = setup.run("--policy", policy, *(["--apply"] if apply else []), netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["waf_audit"]["status"] == "not_applicable"
    assert row["waf_audit"]["applicable"] is False
    assert row["waf_audit"]["applicable_profiles"] == 0
    assert ROOT not in setup.box.reads
    assert row["syslog_compliant"] is (apply and policy == "manage")
    assert row["system_syslog_compliant"] == row["syslog_compliant"]
    if apply and policy != "audit":
        assert [path for path, _ in setup.box.writes] == [SYSLOG]
        assert row["verified"] and setup.box.saves == 1
    else:
        assert setup.box.writes == [] and setup.box.saves == 0
    assert bool(setup.nb.writes) is apply


@pytest.mark.parametrize("provision", [{}, {"name": "ltm", "level": "none"},
                                       {"name": "asm"}, {"name": "asm", "level": "unknown"}])
def test_incomplete_provisioning_does_not_claim_waf_is_absent(setup, provision):
    setup.box.responses[f5_syslog.ASM_PROVISION] = provision
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["waf_audit"]["status"] == "unavailable"
    assert row["waf_audit"]["applicable"] is None
    assert row["syslog_compliant"] is None
    assert row["verified"] and setup.box.saves == 1
    assert ROOT not in setup.box.reads


def test_system_syslog_failure_still_fails_without_waf(setup):
    setup.box.responses[f5_syslog.ASM_PROVISION] = {"name": "asm", "level": "none"}
    setup.box.reject.add(SYSLOG)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert setup.box.saves == 0 and setup.nb.writes == []
    assert report["devices"]["f5"]["syslog_compliant"] is None
