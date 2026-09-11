"""WAF CLI integration through real inventory/Nornir and simulated REST APIs."""

import copy
import json
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from netops import cli, f5_waf, f5_syslog, netbox, runner
from netops.features import waf

ROOT = f5_waf.WAF_PROFILES
PROFILE = ROOT + "/~Tenant~folder~logging"
APP = PROFILE + "/application/~Tenant~remote"
OLD_DATE = "2025-01-01T00:00:00Z"
COLLECTORS = [{"name": "192.0.2.50:514"}, {"name": "192.0.2.51:1514"}]


def application(servers=None, **overrides):
    return {
        "name": "remote", "partition": "Tenant", "remoteStorage": "remote",
        "selfLink": "https://localhost" + APP + "?ver=17.1.0",
        "protocol": "tcp", "localStorage": "enabled",
        "format": {"type": "user-defined", "userString": "%request%"},
        "filter": [{"name": "request-type", "values": ["illegal"]}],
        "servers": servers if servers is not None else [{"name": "192.0.2.99:514"}],
        **overrides,
    }


class FakeF5:
    def __init__(self):
        self.app = application()
        self.responses = {
            ROOT: {"items": [{"name": "logging", "fullPath": "/Tenant/folder/logging",
                              "builtIn": "disabled",
                              "applicationReference": {
                                  "link": "https://localhost" + PROFILE + "/application"}}]},
            PROFILE + "/application": {"items": [self.app]}, APP: self.app,
            f5_syslog.SYSLOG: {"remoteServers": [
                {"name": "standard1", "host": "192.0.2.50", "remotePort": 514},
                {"name": "standard2", "host": "192.0.2.51", "remotePort": 1514}]},
        }
        self.reads, self.writes, self.logins = [], [], []
        self.saves = 0
        self.ignore, self.reject = set(), set()
        self.save_failure = False

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_json(self, path):
        self.reads.append(path)
        response = self.responses[path]
        if isinstance(response, Exception):
            raise response
        return copy.deepcopy(response)

    def patch_json(self, path, payload):
        self.writes.append((path, copy.deepcopy(payload)))
        if path in self.reject:
            raise RuntimeError("write rejected")
        if path not in self.ignore:
            self.responses[path].update(copy.deepcopy(payload))

    def save_config(self):
        if self.save_failure:
            raise RuntimeError("save failed")
        self.saves += 1


class NetBoxSession:
    def __init__(self):
        self.headers = {}
        self.device = {
            "id": 7, "name": "f5", "primary_ip4": {"address": "192.0.2.1/24"},
            "platform": {"slug": "f5-tmos"}, "tags": [{"slug": "syslog-manage"}],
            "custom_fields": {waf.CHECKED_FIELD: OLD_DATE, "owner": "network-team",
                              "syslog_compliant": True},
        }
        self.fields = [{"name": waf.CHECKED_FIELD, "type": {"value": "datetime"},
                        "object_types": ["dcim.device"]},
                       {"name": "syslog_compliant", "type": {"value": "boolean"},
                        "object_types": ["dcim.device"]}]
        self.calls = []
        self.fail_stamp = self.ignore_stamp = False
        self.ignore_compliance = False

    @property
    def writes(self):
        return [call for call in self.calls if call[0] != "GET"]

    def get(self, url, **kwargs):
        return self.request("GET", url, **kwargs)

    def request(self, method, url, json=None, params=None, **kwargs):
        path = urlsplit(url).path.removeprefix("/api/")
        self.calls.append((method, path, copy.deepcopy(json or params)))
        status = 200
        if method == "GET" and path == "dcim/devices/":
            body = {"results": [self.device], "next": None}
        elif method == "GET" and path == "extras/custom-fields/":
            body = {"results": [field for field in self.fields
                                if field["name"] == params["name"]], "next": None}
        elif method == "POST" and path == "extras/custom-fields/":
            self.fields.append(copy.deepcopy(json))
            body = json
        elif method == "GET" and path == "dcim/devices/7/":
            body = self.device
        elif method == "PATCH" and path == "dcim/devices/7/":
            if self.fail_stamp:
                status = 403
            elif not self.ignore_stamp:
                self.device["custom_fields"].update({
                    key: value for key, value in json["custom_fields"].items()
                    if not (self.ignore_compliance and key == "syslog_compliant")})
            body = self.device
        else:
            raise AssertionError(f"unexpected NetBox request: {method} {path}")
        return SimpleNamespace(status_code=status, text="permission denied" if status == 403 else "",
                               json=lambda: copy.deepcopy(body))


@pytest.fixture
def setup(tmp_path, monkeypatch):
    box = FakeF5()
    nb = NetBoxSession()
    real_client = netbox.Client

    def f5_client(host, **kwargs):
        box.logins.append((host.name, host.hostname, host.username, host.password, kwargs))
        return box

    def nb_client(*args, **kwargs):
        client = real_client(*args, **kwargs)
        client._session = nb
        return client

    def no_ssh(*args, **kwargs):
        raise AssertionError("WAF must never open SSH")

    monkeypatch.setattr(f5_waf, "Client", f5_client)
    monkeypatch.setattr(netbox, "Client", nb_client)
    monkeypatch.setattr(runner, "detect_platform", no_ssh)
    monkeypatch.setattr(runner, "netmiko_send_config", no_ssh)
    monkeypatch.setenv("NET_USER", "network-admin")
    monkeypatch.setenv("NET_PASS", "test-login-password")
    monkeypatch.setenv("NETBOX_URL", "https://nb")
    monkeypatch.setenv("NETBOX_TOKEN", "test-netbox-token")
    standards = tmp_path / "standards.json"
    standards.write_text(json.dumps({"syslog": {"destinations": [
        "192.0.2.50", {"host": "192.0.2.51", "port": 1514}]}}))
    csv = tmp_path / "hosts.csv"
    csv.write_text("host,name,platform\n192.0.2.1,f5,f5_tmsh\n")
    report = tmp_path / "report.json"

    def run(*extra, netbox_inventory=False, direct=False, feature="waf"):
        inventory = (["--netbox"] if netbox_inventory else
                     ["--ip", "192.0.2.1", "--platform", "f5_tmsh"] if direct else
                     ["--csv", str(csv)])
        code = cli.main([feature, "--no-env-file", "--no-rollback-file", "--yes",
                         "--standards", str(standards), "--report", str(report),
                         *inventory, *extra])
        data = json.loads(report.read_text()) if report.exists() else None
        return code, data

    return SimpleNamespace(box=box, nb=nb, run=run, csv=csv, standards=standards)


@pytest.mark.parametrize("replace", [False, True])
def test_default_dry_run_never_writes(setup, replace):
    code, report = setup.run(*(["--replace"] if replace else []), "--fail-on-diff")
    assert code == cli.EXIT_DIFF
    record = report["devices"]["f5"]
    assert record["status"] == "pending"
    assert len(record["profiles"][0]["add"]) == 2
    assert len(record["profiles"][0]["remove"]) == int(replace)
    assert setup.box.writes == setup.nb.writes == []
    assert setup.box.saves == 0


def test_add_only_preserves_settings_and_is_idempotent(setup):
    before = copy.deepcopy(setup.box.app)
    code, report = setup.run("--apply")
    assert code == cli.EXIT_OK
    assert setup.box.writes == [(APP, {"servers": before["servers"] + COLLECTORS})]
    before["servers"] += COLLECTORS
    assert setup.box.app == before
    record = report["devices"]["f5"]
    assert record["verified"] and record["saved"]
    assert record["profiles"][0]["before_servers"] == [{"name": "192.0.2.99:514"}]
    assert setup.run("--apply")[1]["devices"]["f5"]["status"] == "ok"
    assert len(setup.box.writes) == 1


def test_replace_removes_duplicates_wrong_ports_and_extras(setup):
    keep = {"name": "192.0.2.50:514", "appService": "/Tenant/service"}
    setup.box.app["servers"] = [keep, {"name": "192.0.2.50:514"},
                                {"name": "192.0.2.51:514"}, {"name": "192.0.2.99:514"}]
    code, report = setup.run("--replace", "--apply")
    assert code == cli.EXIT_OK
    assert setup.box.app["servers"] == [keep, COLLECTORS[1]]
    assert len(report["devices"]["f5"]["remove"]) == 3


@pytest.mark.parametrize("tags,expected", [([], "audit"), (["syslog-audit"], "audit"),
                                           (["syslog-add"], "add"), (["syslog-manage"], "replace")])
def test_netbox_tags_control_each_device_and_stamp_completed_checks(setup, tags, expected):
    setup.nb.device["tags"] = [{"slug": tag} for tag in tags] + [{"slug": "existing-tag"}]
    before_tags = copy.deepcopy(setup.nb.device["tags"])
    code, report = setup.run("--apply", "--policy", "netbox", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["mode"] == "netbox-tags"
    record = report["devices"]["f5"]
    assert record["mode"] == expected
    assert len(setup.box.writes) == int(expected != "audit")
    if expected == "replace":
        assert setup.box.app["servers"] == COLLECTORS
    elif expected == "add":
        assert len(setup.box.app["servers"]) == 3
    assert setup.nb.device["tags"] == before_tags
    assert setup.nb.device["custom_fields"]["owner"] == "network-team"
    assert setup.nb.device["custom_fields"][waf.CHECKED_FIELD] != OLD_DATE
    assert record["netbox_writeback"]["status"] == "written"
    assert record["netbox_writeback"]["device_id"] == 7
    assert record["syslog_compliant"] is (expected == "replace")
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is (expected == "replace")
    assert all(call[1] != "dcim/interfaces/" for call in setup.nb.calls)


def test_netbox_dry_run_previews_field_creation_and_stamp_without_writes(setup):
    setup.nb.fields = setup.nb.fields[1:]
    code, report = setup.run(netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert setup.nb.writes == setup.box.writes == []
    assert report["devices"]["f5"]["netbox_writeback"]["status"] == "planned"
    assert report["devices"]["f5"]["syslog_compliant"] is False
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True


def test_missing_datetime_field_is_created_on_apply(setup):
    setup.nb.fields = setup.nb.fields[1:]
    assert setup.run("--apply", netbox_inventory=True)[0] == cli.EXIT_OK
    assert setup.nb.writes[0][0:2] == ("POST", "extras/custom-fields/")
    assert setup.nb.fields[-1]["object_types"] == ["dcim.device"]
    assert setup.nb.fields[-1]["type"] == "datetime"
    assert setup.nb.fields[-1]["name"] == "syslog_last_checked"
    assert setup.nb.fields[-1]["label"] == "Syslog last checked"


def test_wrong_field_type_stops_before_f5_changes(setup):
    setup.nb.fields[0]["type"] = "text"
    assert setup.run("--apply", netbox_inventory=True)[0] == cli.EXIT_USAGE
    assert setup.nb.writes == setup.box.logins == []


def test_conflicting_tags_fail_before_f5_login_and_do_not_stamp(setup):
    setup.nb.device["tags"] = [{"slug": "syslog-add"}, {"slug": "syslog-manage"}]
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert "conflicting" in report["devices"]["f5"]["error"]
    assert setup.nb.writes == setup.box.logins == []


@pytest.mark.parametrize("failure", ["reject", "ignore", "save", "discovery", "readback"])
def test_device_failures_leave_old_timestamp_and_report_failure(setup, failure):
    if failure == "reject":
        setup.box.reject.add(APP)
    elif failure == "ignore":
        setup.box.ignore.add(APP)
    elif failure == "save":
        setup.box.save_failure = True
    elif failure == "discovery":
        setup.box.responses[ROOT] = RuntimeError("discovery failed")
    else:
        setup.box.ignore.add(APP)
        setup.box.responses[APP] = RuntimeError("profile disappeared")
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert report["devices"]["f5"]["status"] in ("failed", "unverified")
    assert setup.nb.writes == []
    assert setup.nb.device["custom_fields"][waf.CHECKED_FIELD] == OLD_DATE
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True


@pytest.mark.parametrize("failure", ["denied", "ignored"])
def test_netbox_writeback_failure_is_not_reported_as_success(setup, failure):
    setup.nb.fail_stamp = failure == "denied"
    setup.nb.ignore_stamp = failure == "ignored"
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert report["devices"]["f5"]["applied"]
    assert report["devices"]["f5"]["netbox_writeback"]["status"] == "failed"


def test_no_verify_does_not_stamp_and_no_save_does_not_persist(setup):
    code, report = setup.run("--apply", "--no-verify", "--no-save", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert setup.nb.writes == []
    assert setup.box.saves == 0
    assert report["devices"]["f5"]["checked_at"] is None


@pytest.mark.parametrize("app", [application([], remoteStorage="remote"),
                                application(remoteStorage="none")])
def test_local_only_and_empty_remote_profiles_are_not_enabled(setup, app):
    setup.box.app.update(app)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["status"] == "skipped"
    assert setup.box.writes == []
    assert report["devices"]["f5"]["netbox_writeback"]["status"] == "written"
    assert report["devices"]["f5"]["syslog_compliant"] is True
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True


def test_non_f5_and_unknown_platforms_never_open_ssh(setup):
    for platform, code in (("cisco_ios", cli.EXIT_OK), ("", cli.EXIT_FAILED)):
        setup.csv.write_text(f"host,name,platform\n192.0.2.1,f5,{platform}\n")
        assert setup.run("--apply")[0] == code
    assert setup.box.logins == []


def test_direct_ip_uses_inventory_credentials_over_https(setup):
    assert setup.run("--apply", direct=True)[0] == cli.EXIT_OK
    assert setup.box.logins[0][1:4] == ("192.0.2.1", "network-admin", "test-login-password")
    assert setup.box.logins[0][4]["port"] == 443
    assert setup.box.logins[0][4]["verify_tls"] is False


def test_discovery_follows_pages_and_handles_expanded_reference(setup):
    box = setup.box
    page = box.responses[ROOT]
    box.responses[ROOT] = {"items": [], "nextLink": "https://localhost" + ROOT + "?$skip=1"}
    box.responses[ROOT + "?$skip=1"] = page
    page["items"][0]["applicationReference"] = {
        "items": [], "nextLink": "https://localhost" + PROFILE + "/application?$skip=1"}
    box.responses[PROFILE + "/application?$skip=1"] = {"items": [box.app]}
    plans = f5_waf.plan_waf(box, [("192.0.2.50", 514)], False)
    assert len(plans) == 1
    assert plans[0].endpoint == APP


@pytest.mark.parametrize("policy", ["syslog-audit", "syslog-add", "syslog-manage"])
def test_builtin_profiles_never_enter_plans_or_compliance(setup, policy):
    setup.nb.device["tags"] = [{"slug": policy}]
    setup.box.app["servers"] = copy.deepcopy(COLLECTORS)
    for name, flag in (("internal remote logger", "enabled"),
                       ("cloud security services", True),
                       ("future-system-profile", "enabled")):
        setup.box.responses[ROOT]["items"].append({
            "name": name, "fullPath": "/Common/" + name, "builtIn": flag,
            # No fake response: fetching these subresources would fail.
            "applicationReference": {"link": ROOT + "/system/application"},
        })
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    record = report["devices"]["f5"]
    assert len(record["profiles"]) == 1
    assert len(record["skipped_profiles"]) == 3
    assert all(row["built_in"] is True for row in record["skipped_profiles"])
    assert record["syslog_compliant"] is True
    assert setup.box.writes == []
    assert ROOT + "/system/application" not in setup.box.reads


def test_only_builtin_profiles_never_write_f5_and_use_system_compliance(setup):
    setup.box.responses[ROOT]["items"][0]["builtIn"] = "enabled"
    setup.nb.device["custom_fields"]["syslog_compliant"] = False
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    record = report["devices"]["f5"]
    assert record["status"] == "skipped"
    assert record["profiles"] == []
    assert record["syslog_compliant"] is True
    assert setup.box.reads == [ROOT, f5_syslog.SYSLOG]
    assert setup.box.writes == [] and setup.box.saves == 0
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True
    assert setup.nb.device["custom_fields"][waf.CHECKED_FIELD] != OLD_DATE


@pytest.mark.parametrize("flag", [None, "unexpected", ""])
def test_unknown_profile_ownership_is_skipped_without_claiming_compliance(setup, flag):
    profile = {"name": "unknown", "application": [application()]}
    if flag is not None:
        profile["builtIn"] = flag
    setup.box.responses[ROOT]["items"].append(profile)
    setup.nb.device["custom_fields"]["syslog_compliant"] = False
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    record = report["devices"]["f5"]
    assert len(record["profiles"]) == 1
    assert record["skipped_profiles"][0]["built_in"] is None
    assert record["syslog_compliant"] is None
    assert [path for path, _ in setup.box.writes] == [APP]
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is False


@pytest.mark.parametrize("flag", ["disabled", False])
def test_custom_profiles_in_common_remain_eligible(setup, flag):
    profile = setup.box.responses[ROOT]["items"][0]
    profile.update(fullPath="/Common/our-remote-logger", builtIn=flag)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert [path for path, _ in setup.box.writes] == [APP]
    assert report["devices"]["f5"]["skipped_profiles"] == []


def test_ipv6_and_route_domain_keys():
    assert f5_waf._waf_server_key({"name": "2001:0db8::50.514"}) == ("2001:db8::50", 514)
    assert f5_waf._waf_server_key({"name": "192.0.2.50%0:514"}) == ("192.0.2.50", 514)
    assert f5_waf._waf_server_key({"name": "192.0.2.50%2:514"}) == ("192.0.2.50%2", 514)
    plan = f5_waf.plan_waf_application(application(), [("2001:db8::51", 1514)], True, "p", APP)
    assert plan.payload == {"servers": [{"name": "2001:db8::51.1514"}]}


def test_one_failed_application_does_not_hide_other_results(setup):
    second = APP + "-second"
    app = application(name="second", selfLink="https://localhost" + second)
    setup.box.responses[PROFILE + "/application"]["items"].append(app)
    setup.box.responses[second] = app
    setup.box.reject.add(APP)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert [path for path, _ in setup.box.writes] == [APP, second]
    assert report["devices"]["f5"]["profiles"][1]["verified"]
    assert setup.box.saves == 0
    assert setup.nb.writes == []


def test_invalid_standard_rejected_before_inventory_or_device_contact(setup):
    setup.standards.write_text(json.dumps({"syslog": {"destinations": ["logs.example.com"]}}))
    with pytest.raises(SystemExit):
        setup.run()
    assert setup.nb.calls == setup.box.logins == []


def test_removal_only_failure_is_verified(setup):
    setup.box.app["servers"].extend(copy.deepcopy(COLLECTORS))
    setup.box.ignore.add(APP)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert report["devices"]["f5"]["profiles"][0]["add"] == []
    assert report["devices"]["f5"]["verified"] is False
    assert setup.nb.writes == []


@pytest.mark.parametrize("tag", ["syslog-audit", "syslog-add", "syslog-manage"])
def test_exact_match_sets_compliance_true_without_f5_changes(setup, tag):
    setup.nb.device["tags"] = [{"slug": tag}]
    setup.nb.device["custom_fields"]["syslog_compliant"] = False
    setup.box.app["servers"] = copy.deepcopy(COLLECTORS)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert setup.box.writes == []
    assert report["devices"]["f5"]["syslog_compliant"] is True
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True
    writes = [call for call in setup.nb.writes if call[0] == "PATCH"]
    assert len(writes) == 1
    assert set(writes[0][2]["custom_fields"]) == {waf.CHECKED_FIELD, "syslog_compliant"}


def test_add_only_with_existing_extras_is_not_fully_managed(setup):
    setup.nb.device["tags"] = [{"slug": "syslog-add"}]
    setup.box.app["servers"].extend(copy.deepcopy(COLLECTORS))
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    record = report["devices"]["f5"]
    assert record["status"] == "ok"  # Meets add policy, but not the fully managed target.
    assert record["syslog_compliant"] is False
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is False
    assert setup.box.writes == []


def test_add_only_can_reach_exact_match_after_verification(setup):
    setup.nb.device["tags"] = [{"slug": "syslog-add"}]
    setup.box.app["servers"] = [copy.deepcopy(COLLECTORS[0])]
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["syslog_compliant"] is True
    assert setup.nb.device["custom_fields"]["syslog_compliant"] is True


def test_every_eligible_profile_must_match(setup):
    setup.nb.device["tags"] = [{"slug": "syslog-audit"}]
    setup.box.app["servers"] = copy.deepcopy(COLLECTORS)
    second = application(name="second", selfLink="https://localhost" + APP + "-second")
    setup.box.responses[PROFILE + "/application"]["items"].append(second)
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    record = report["devices"]["f5"]
    assert record["profiles"][0]["fully_managed_compliant"] is True
    assert record["profiles"][1]["fully_managed_compliant"] is False
    assert record["syslog_compliant"] is False


def test_compliance_verdict_is_read_back_even_if_timestamp_verified(setup):
    setup.nb.device["tags"] = [{"slug": "syslog-audit"}]
    setup.nb.ignore_compliance = True
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert "syslog_compliant did not verify" in report["devices"]["f5"]["error"]


@pytest.mark.parametrize("kind", ["missing", "text", "wrong-object"])
def test_boolean_field_is_validated_before_any_f5_change(setup, kind):
    if kind == "missing":
        setup.nb.fields.pop()
    elif kind == "text":
        setup.nb.fields[-1]["type"] = "text"
    else:
        setup.nb.fields[-1]["object_types"] = ["dcim.interface"]
    assert setup.run("--apply", netbox_inventory=True)[0] == cli.EXIT_USAGE
    assert setup.box.logins == setup.nb.writes == []


def test_repeated_or_malformed_discovery_pages_fail():
    box = FakeF5()
    box.responses[ROOT] = {"items": [], "nextLink": ROOT}
    with pytest.raises(ValueError, match="repeated"):
        f5_waf.plan_waf(box, [("192.0.2.50", 514)], False)
    box.responses[ROOT] = {"items": ["not an object"]}
    with pytest.raises(ValueError, match="invalid WAF collection"):
        f5_waf.plan_waf(box, [("192.0.2.50", 514)], False)


@pytest.mark.parametrize("tls_options,expected", [({}, False), ({"verify_tls": True}, True)])
def test_rest_client_authentication_tls_ipv6_and_logout(monkeypatch, tls_options, expected):
    import requests

    calls = []

    class Session:
        headers = {}

        def request(self, method, url, **kwargs):
            calls.append((method, url, kwargs))
            body = {"token": {"token": "session-token"}} if url.endswith("/login") else {}
            return SimpleNamespace(status_code=200, json=lambda: body)

        def delete(self, url, **kwargs):
            calls.append(("DELETE", url, kwargs))

        def close(self):
            calls.append(("CLOSE",))

    session = Session()
    monkeypatch.setattr(requests, "Session", lambda: session)
    host = SimpleNamespace(hostname="2001:db8::1", username="admin", password="password")
    with f5_waf.Client(host, port=8443, provider="radius", **tls_options) as client:
        assert session.headers["X-F5-Auth-Token"] == "session-token"
        client.patch_json(APP, {"servers": COLLECTORS})
        client.save_config()
    assert calls[0][1] == "https://[2001:db8::1]:8443/mgmt/shared/authn/login"
    assert calls[0][2]["json"]["loginProviderName"] == "radius"
    assert all(call[2]["verify"] is expected for call in calls if call[0] != "CLOSE")
    assert calls[1][2]["json"] == {"servers": COLLECTORS}
    assert calls[2][2]["timeout"] >= 120
    assert calls[-2][0] == "DELETE"
    assert calls[-1] == ("CLOSE",)


def test_failed_rest_login_closes_session(monkeypatch):
    import requests

    closed = []
    session = SimpleNamespace(
        request=lambda *args, **kwargs: SimpleNamespace(status_code=403, text="denied"),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(requests, "Session", lambda: session)
    host = SimpleNamespace(hostname="192.0.2.1", username="admin", password="password")
    with pytest.raises(RuntimeError, match="403"):
        with f5_waf.Client(host):
            pytest.fail("failed login must not enter the session")
    assert closed == [True]


def test_env_file_supplies_netbox_aws_keys_and_f5_options(setup, tmp_path, monkeypatch):
    from netops import credentials

    fetched = []

    def secret(name, region):
        fetched.append((name, region))
        return {"f5_user": "aws-user", "f5_password": "aws-password"}

    monkeypatch.setattr(credentials, "fetch_json_secret", secret)
    env = tmp_path / "f5.env"
    env.write_text('''NETOPS_INVENTORY=netbox
NETBOX_FILTERS="platform=f5-tmos site=atl"
NET_AWS_SECRET=prod/network/f5
NET_AWS_REGION=us-east-1
NET_AWS_USERNAME_KEY=f5_user
NET_AWS_PASSWORD_KEY=f5_password
NETOPS_F5_PORT=8443
NETOPS_F5_TIMEOUT=45
NETOPS_F5_VERIFY_TLS=false
NETOPS_F5_LOGIN_PROVIDER=radius
NETBOX_CHECKED_FIELD=syslog_last_checked
''')
    code = cli.main(["waf", "--env-file", str(env), "--standards", str(setup.standards)])
    assert code == cli.EXIT_OK
    assert fetched == [("prod/network/f5", "us-east-1")]
    assert setup.box.logins[0][2:4] == ("aws-user", "aws-password")
    assert setup.box.logins[0][4] == {"port": 8443, "timeout": 45,
                                      "verify_tls": False, "provider": "radius"}
    query = setup.nb.calls[0][2]
    assert query["platform"] == "f5-tmos" and query["site"] == "atl"
    assert setup.nb.writes == setup.box.writes == []


def test_cli_overrides_environment_connection_settings_and_filters(setup, monkeypatch):
    monkeypatch.setenv("NETOPS_F5_PORT", "8443")
    monkeypatch.setenv("NETOPS_F5_TIMEOUT", "45")
    monkeypatch.setenv("NETOPS_F5_VERIFY_TLS", "false")
    monkeypatch.setenv("NETOPS_F5_LOGIN_PROVIDER", "radius")
    monkeypatch.setenv("NETBOX_FILTERS", "site=wrong platform=wrong")
    code, _ = setup.run("--f5-port", "443", "--f5-timeout", "60", "--f5-verify-tls",
                         "--f5-login-provider", "tmos", "--netbox-filter", "platform=f5-tmos",
                         netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert setup.box.logins[0][4] == {"port": 443, "timeout": 60,
                                      "verify_tls": True, "provider": "tmos"}
    assert setup.nb.calls[0][2]["platform"] == "f5-tmos"
    assert "site" not in setup.nb.calls[0][2]


@pytest.mark.parametrize("direct", [False, True])
def test_explicit_csv_or_ip_overrides_netbox_environment(setup, monkeypatch, direct):
    monkeypatch.setenv("NETOPS_INVENTORY", "netbox")
    assert setup.run(direct=direct)[0] == cli.EXIT_OK
    assert setup.nb.calls == []
    assert len(setup.box.logins) == 1


def test_environment_csv_default_still_works(setup, monkeypatch):
    monkeypatch.setenv("NETOPS_INVENTORY", "csv")
    monkeypatch.setenv("NETOPS_CSV", str(setup.csv))
    assert cli.main(["waf", "--no-env-file", "--standards", str(setup.standards)]) == cli.EXIT_OK
    assert setup.nb.calls == []


@pytest.mark.parametrize("variable,value", [("NETOPS_INVENTORY", "typo"),
                                             ("NETOPS_F5_VERIFY_TLS", "typo")])
def test_invalid_environment_defaults_fail_before_connection(setup, monkeypatch, variable, value):
    monkeypatch.setenv(variable, value)
    with pytest.raises(SystemExit):
        cli.main(["waf", "--no-env-file", "--standards", str(setup.standards)])
    assert setup.nb.calls == setup.box.logins == []


def test_shell_environment_wins_over_env_file(setup, monkeypatch, tmp_path):
    monkeypatch.setenv("NETOPS_F5_PORT", "8443")
    env = tmp_path / "f5.env"
    env.write_text("NETOPS_F5_PORT=443\n")
    assert cli.main(["waf", "--env-file", str(env), "--csv", str(setup.csv),
                     "--standards", str(setup.standards)]) == cli.EXIT_OK
    assert setup.box.logins[0][4]["port"] == 8443


def test_system_syslog_drift_prevents_combined_waf_compliance(setup):
    setup.box.responses[f5_syslog.SYSLOG]["remoteServers"] = []
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["profiles"][0]["fully_managed_compliant"] is True
    assert row["system_syslog_audit"]["compliant"] is False
    assert row["syslog_compliant"] is False
    assert [path for path, _ in setup.box.writes] == [APP]


def test_system_syslog_read_failure_prevents_waf_changes(setup):
    setup.box.responses[f5_syslog.SYSLOG] = RuntimeError("cannot audit system syslog")
    code, _ = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_FAILED
    assert setup.box.writes == setup.nb.writes == []


@pytest.mark.parametrize("feature", ["waf", "syslog"])
@pytest.mark.parametrize("policy,expected", [("audit", "audit"), ("add", "add"), ("manage", "replace")])
def test_explicit_policy_overrides_tags_even_when_conflicting(setup, feature, policy, expected):
    setup.nb.device["tags"] = [{"slug": "syslog-audit"}, {"slug": "syslog-manage"}]
    setup.box.responses[f5_syslog.SYSLOG]["remoteServers"] = [
        {"name": "old", "host": "192.0.2.99", "remotePort": 514}]
    code, report = setup.run("--apply", "--policy", policy, netbox_inventory=True, feature=feature)
    assert code == cli.EXIT_OK
    row = report["devices"]["f5"]
    assert row["mode"] == expected
    assert len(setup.box.writes) == int(policy != "audit")
    assert row["netbox_writeback"]["status"] == "written"
    if policy != "audit":
        servers = setup.box.writes[0][1]["servers" if feature == "waf" else "remoteServers"]
        assert len(servers) == (3 if policy == "add" else 2)


@pytest.mark.parametrize("feature", ["waf", "syslog"])
@pytest.mark.parametrize("flag,expected", [("--add", "add"), ("--replace", "replace")])
def test_legacy_mode_flags_override_netbox_tags(setup, feature, flag, expected):
    setup.nb.device["tags"] = [{"slug": "syslog-audit"}]
    setup.box.responses[f5_syslog.SYSLOG]["remoteServers"] = []
    code, report = setup.run("--apply", flag, netbox_inventory=True, feature=feature)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["mode"] == expected
    assert len(setup.box.writes) == 1


@pytest.mark.parametrize("feature", ["waf", "syslog"])
@pytest.mark.parametrize("policy", ["audit", "add", "manage", "netbox"])
def test_policy_never_bypasses_dry_run(setup, feature, policy):
    setup.box.responses[f5_syslog.SYSLOG]["remoteServers"] = []
    assert setup.run("--policy", policy, netbox_inventory=True, feature=feature)[0] == cli.EXIT_OK
    assert setup.box.writes == setup.nb.writes == []
    assert setup.box.saves == 0


@pytest.mark.parametrize("feature", ["waf", "syslog"])
@pytest.mark.parametrize("flags", [("--policy", "netbox"),
                                   ("--policy", "audit", "--add"),
                                   ("--policy", "netbox", "--replace")])
def test_invalid_policy_selection_stops_before_connections(setup, feature, flags):
    with pytest.raises(SystemExit):
        setup.run(*flags, feature=feature)
    assert setup.box.logins == setup.nb.calls == []


def test_policy_env_default_and_cli_override(setup, monkeypatch):
    monkeypatch.setenv("NETOPS_F5_POLICY", "audit")
    code, report = setup.run("--apply", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["mode"] == "audit"
    assert setup.box.writes == []
    code, report = setup.run("--apply", "--policy", "manage", netbox_inventory=True)
    assert code == cli.EXIT_OK
    assert report["devices"]["f5"]["mode"] == "replace"
    assert len(setup.box.writes) == 1


@pytest.mark.parametrize("feature", ["waf", "syslog"])
def test_netbox_policy_is_resolved_independently_for_multiple_devices(setup, monkeypatch, feature):
    devices, boxes = {}, {}
    for offset, tag in enumerate(("syslog-audit", "syslog-add", "syslog-manage", None)):
        device = copy.deepcopy(setup.nb.device)
        device.update(id=7 + offset, name=f"f5-{offset}", tags=[{"slug": tag}] if tag else [])
        devices[device["id"]] = device
        box = FakeF5()
        box.responses[f5_syslog.SYSLOG]["remoteServers"] = [
            {"name": "old", "host": "192.0.2.99", "remotePort": 514}]
        boxes[device["name"]] = box
    original_request = setup.nb.request

    def request(method, url, json=None, params=None, **kwargs):
        path = urlsplit(url).path.removeprefix("/api/")
        if path == "dcim/devices/":
            body = {"results": list(devices.values()), "next": None}
        elif path.startswith("dcim/devices/"):
            device = devices[int(path.rstrip("/").rsplit("/", 1)[1])]
            if method == "PATCH":
                device["custom_fields"].update(json["custom_fields"])
            body = device
        else:
            return original_request(method, url, json=json, params=params, **kwargs)
        return SimpleNamespace(status_code=200, text="", json=lambda: copy.deepcopy(body))

    monkeypatch.setattr(setup.nb, "request", request)
    monkeypatch.setattr(f5_waf, "Client", lambda host, **kwargs: boxes[host.name])
    code, report = setup.run("--apply", "--policy", "netbox", netbox_inventory=True, feature=feature)
    assert code == cli.EXIT_OK
    for offset, expected in enumerate(("audit", "add", "replace", "audit")):
        row = report["devices"][f"f5-{offset}"]
        assert row["mode"] == expected
        assert row["netbox_writeback"]["device_id"] == 7 + offset
        assert len(boxes[f"f5-{offset}"].writes) == int(expected != "audit")
        if expected != "audit":
            patch = boxes[f"f5-{offset}"].writes[0][1]
            assert len(patch["servers" if feature == "waf" else "remoteServers"]) == (3 if expected == "add" else 2)
