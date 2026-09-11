"""Banner CLI integration with real inventory and a simulated F5 REST API."""

import copy
import json

import pytest

from netops import cli, f5_banner
from netops.features.banner import desired_body
from test_waf import setup as waf_setup

SSH = f5_banner.SURFACES[0][1]
GUI = f5_banner.SURFACES[1][1]


@pytest.fixture
def setup(waf_setup):
    waf_setup.standards.write_text(json.dumps({"banner": {"motd": True, "login": False}}))
    waf_setup.box.responses[SSH] = {
        "banner": "disabled", "bannerText": "Old SSH notice", "allow": ["192.0.2.0/24"],
        "port": 22, "login": "enabled", "inactivityTimeout": 600,
    }
    waf_setup.box.responses[GUI] = {
        "guiSecurityBanner": "enabled", "guiSecurityBannerText": "Old GUI notice",
        "hostname": "f5.example.com", "guiSetup": "disabled", "awsSecretKey": "do-not-report",
    }
    original = waf_setup.run
    waf_setup.run = lambda *args, **kwargs: original(*args, feature="banner", **kwargs)
    return waf_setup


def test_netbox_dry_run_previews_both_banners_without_changes(setup):
    code, report = setup.run("--fail-on-diff", netbox_inventory=True)
    assert code == cli.EXIT_DIFF
    row = report["devices"]["f5"]
    assert row["status"] == "pending" and len(row["commands"]) == 2
    assert len(row["banners"]) == 2
    assert setup.box.reads == [SSH, GUI]
    assert setup.box.writes == setup.nb.writes == [] and setup.box.saves == 0
    assert "syslog_compliant" not in row
    assert "do-not-report" not in json.dumps(report)


@pytest.mark.parametrize("replace", [False, True])
def test_apply_verify_save_and_idempotence(setup, replace):
    before = copy.deepcopy(setup.box.responses)
    flags = ["--apply"] + (["--replace"] if replace else [])
    code, report = setup.run(*flags, netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["verified"] and row["saved"]
    assert [path for path, _ in setup.box.writes] == [SSH, GUI]
    text = f5_banner.banner_text(["motd"], {})
    assert setup.box.responses[SSH]["bannerText"] == text
    assert setup.box.responses[SSH]["banner"] == "enabled"
    assert setup.box.responses[GUI]["guiSecurityBannerText"] == text
    assert setup.box.responses[GUI]["guiSecurityBanner"] == "enabled"
    for _, path, flag, content in f5_banner.SURFACES:
        assert {k: v for k, v in before[path].items() if k not in (flag, content)} == {
            k: v for k, v in setup.box.responses[path].items() if k not in (flag, content)}
    assert row["banners"][0]["before"] == {"banner": "disabled", "bannerText": "Old SSH notice"}
    assert setup.nb.writes == []
    assert setup.run(*flags)[1]["devices"]["f5"]["status"] == "ok"
    assert len(setup.box.writes) == 2 and setup.box.saves == 1

    assert row["result_after"]["status"] == "observed"
    assert row["backout"]["complete"]
    for step in row["backout"]["steps"]:
        if step["purpose"] == "restore":
            setup.box.patch_json(step["path"], step["body"])
    assert setup.box.responses[SSH]["bannerText"] == "Old SSH notice"
    assert setup.box.responses[GUI]["guiSecurityBannerText"] == "Old GUI notice"


@pytest.mark.parametrize("kind", ["login", "motd"])
def test_default_f5_text_matches_switch_standard(kind):
    for platform in ("cisco_ios", "arista_eos"):
        assert f5_banner.banner_text([kind], {}) == desired_body(platform, kind, {"delimiter": None})


def test_selected_login_overrides_standard_and_combines_when_both_selected(setup):
    assert setup.run("--banner", "login", "--apply")[0] == cli.EXIT_OK
    assert setup.box.responses[SSH]["bannerText"] == f5_banner.banner_text(["login"], {})
    assert setup.run("--banner", "motd", "--banner", "login", "--apply")[0] == cli.EXIT_OK
    assert setup.box.responses[GUI]["guiSecurityBannerText"] == (
        f5_banner.banner_text(["motd"], {}) + "\n\n" + f5_banner.banner_text(["login"], {}))


@pytest.mark.parametrize("failure", ["read", "reject", "ignore", "save"])
def test_failure_reports_partial_state_and_does_not_save(setup, failure):
    if failure == "read":
        setup.box.responses[GUI] = RuntimeError("cannot read global settings")
    elif failure == "reject":
        setup.box.reject.add(GUI)
    elif failure == "ignore":
        setup.box.ignore.add(GUI)
    else:
        setup.box.save_failure = True
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert setup.box.saves == 0 and setup.nb.writes == []
    row = report["devices"]["f5"]
    if failure == "read":
        assert setup.box.writes == []
    elif failure == "reject":
        assert row["banners"][0]["applied"] and not row["banners"][1]["applied"]
    elif failure == "ignore":
        assert row["verified"] is False and row["saved"] is False
        assert row["missing_after"] == ["Web login banner"]


def test_no_verify_no_save_and_explicit_tls(setup):
    code, report = setup.run("--apply", "--no-verify", "--no-save", "--f5-verify-tls", direct=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["192.0.2.1"]
    assert row["verified"] is row["saved"] is None
    assert setup.box.reads == [SSH, GUI] and setup.box.saves == 0
    assert setup.box.logins[0][4]["verify_tls"] is True


@pytest.mark.parametrize("body", [{}, {"banner": "maybe"}, {"banner": "enabled", "bannerText": 42}])
def test_malformed_read_fails_before_writes(setup, body):
    setup.box.responses[SSH] = body
    assert setup.run("--apply")[0] == cli.EXIT_FAILED
    assert setup.box.writes == []


def test_custom_multiline_template_quotes_and_blank_lines_survive(setup, tmp_path, monkeypatch):
    folder = tmp_path / "templates" / "f5_tmsh"
    folder.mkdir(parents=True)
    text = '  "Authorised" users only.\n\n  Access is logged: 100%.\n  Path: C:\\notice'
    (folder / "banner.j2").write_text(text)
    monkeypatch.setenv("NETOPS_TEMPLATES", str(folder.parent))
    assert setup.run("--apply")[0] == cli.EXIT_OK
    assert setup.box.responses[SSH]["bannerText"] == text
    assert setup.box.responses[GUI]["guiSecurityBannerText"] == text


def test_empty_template_fails_before_login(setup, tmp_path, monkeypatch):
    folder = tmp_path / "templates" / "f5_tmsh"
    folder.mkdir(parents=True)
    (folder / "banner.j2").write_text("\n   \n")
    monkeypatch.setenv("NETOPS_TEMPLATES", str(folder.parent))
    assert setup.run("--apply")[0] == cli.EXIT_FAILED
    assert setup.box.logins == []


def test_only_drifted_surface_is_patched(setup):
    setup.box.responses[SSH].update(banner="enabled", bannerText=f5_banner.banner_text(["motd"], {}))
    assert setup.run("--apply")[0] == cli.EXIT_OK
    assert [path for path, _ in setup.box.writes] == [GUI]
