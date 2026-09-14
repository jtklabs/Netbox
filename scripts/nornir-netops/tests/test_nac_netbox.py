"""NAC interface reporting, including cron selection and failure semantics."""
import copy
import csv
import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from netops import cli, nac_netbox, runner
from netops.core import MODE_ADD
from netops.features.nac import IOS_SAMPLE, parse_interfaces, plan_nac
from netops.netbox import NetBoxError
from test_nac import context
from test_run import device, login, ports, nac_standards, nac_webhook


OLD = "2026-01-01T00:00:00+00:00"


class MemoryNetBox:
    def __init__(self):
        self.fields = {}
        self.choices = {}
        self.writes = []
        self.queries = []
        self.fail_ids = set()
        self.ignore_patch = False
        self.devices = [{"id": 1, "name": "sw1", "primary_ip4": {"address": "10.1.1.1/24"},
                         "platform": {"slug": "cisco-ios"}, "tags": [], "site": {"id": 1}}]
        self.interfaces = {
            index: {"id": index, "device": {"id": 1}, "name": entry.data["name"],
                    "tags": [{"id": 9}], "custom_fields": {"owner": "network", "nac_checked_at": OLD}}
            for index, entry in enumerate(parse_interfaces(IOS_SAMPLE), 10)
        }
        self.addresses = [{"address": "10.1.1.1/24", "assigned_object_type": "dcim.interface",
                           "assigned_object": {"device": {"id": 1}}}]

    def get(self, path, params=None):
        params = params or {}
        self.queries.append((path, params))
        if path == "extras/custom-fields/":
            return [copy.deepcopy(self.fields[params["name"]])] if params["name"] in self.fields else []
        if path == "extras/custom-field-choice-sets/":
            return [copy.deepcopy(v) for v in self.choices.values() if v["name"] == params["name"]]
        if path == "dcim/interfaces/":
            assert "tag" not in params, "NAC should not query NTP/syslog source tags"
            return copy.deepcopy([row for row in self.interfaces.values() if row["device"]["id"] == params["device_id"]])
        if path == "dcim/devices/":
            return copy.deepcopy(self.devices)
        if path == "ipam/ip-addresses/":
            return copy.deepcopy(self.addresses)
        if path == "dcim/regions/":
            return [{"id": 1, "parent": None, "tags": [{"slug": "poller-boston"}]}]
        if path == "dcim/sites/":
            return [{"id": 1, "region": {"id": 1}, "tags": []}]
        raise AssertionError((path, params))

    def request_object(self, method, path, payload=None):
        if method == "POST":
            self.writes.append((method, path, copy.deepcopy(payload)))
            created = {"id": len(self.fields) + len(self.choices) + 1, **copy.deepcopy(payload)}
            if path == "extras/custom-field-choice-sets/":
                self.choices[created["id"]] = created
            elif path == "extras/custom-fields/":
                self.fields[created["name"]] = created
            else:
                raise AssertionError(path)
            return created
        if path.startswith("extras/custom-field-choice-sets/"):
            return copy.deepcopy(self.choices[int(path.split("/")[-2])])
        assert path.startswith("dcim/interfaces/")
        ident = int(path.split("/")[-2])
        if method == "PATCH":
            self.writes.append((method, path, copy.deepcopy(payload)))
            if ident in self.fail_ids:
                raise NetBoxError("simulated permission failure")
            if not self.ignore_patch:
                self.interfaces[ident]["custom_fields"].update(payload["custom_fields"])
        return copy.deepcopy(self.interfaces[ident])


@pytest.fixture
def client():
    return MemoryNetBox()


def record():
    desired, ctx = context()
    add, _ = plan_nac(parse_interfaces(IOS_SAMPLE), desired.keys, MODE_ADD, ctx)
    return {"audit_before": ctx["audit"], "audit_after": None, "commands": add, "error": None}


def host(known=True):
    return SimpleNamespace(name="sw1", hostname="10.1.1.1", data={"netbox_id": 1} if known else {})


def test_schema_creation_is_repeatable_and_interface_only(client):
    nac_netbox.ensure_fields(client)
    assert len(client.writes) == 7
    assert set(client.fields) == set(nac_netbox.FIELDS)
    assert all(field["object_types"] == ["dcim.interface"] for field in client.fields.values())
    assert client.fields["nac_status"]["type"] == "select"
    assert client.fields["nac_status"]["default"] == "unknown"
    nac_netbox.ensure_fields(client)
    assert len(client.writes) == 7


@pytest.mark.parametrize("field", [
    {"type": "boolean", "object_types": ["dcim.interface"]},
    {"type": "select", "object_types": ["dcim.device"]},
])
def test_incompatible_schema_stops_before_any_write(client, field):
    client.fields["nac_status"] = {"name": "nac_status", **field}
    with pytest.raises(NetBoxError, match="nac_status must be"):
        nac_netbox.ensure_fields(client)
    assert client.writes == []


def test_wrong_choice_set_is_not_modified(client):
    client.choices[1] = {"id": 1, "name": nac_netbox.CHOICE_NAME, "extra_choices": [["yes", "Yes"]]}
    with pytest.raises(NetBoxError, match="choice set must include"):
        nac_netbox.ensure_fields(client)
    assert client.writes == []


def test_sync_writes_verdict_findings_and_fix_preserving_other_data(client):
    result = nac_netbox.sync_device(client, host(), record(), poller="poller-boston")
    assert result["status"] == "written"
    good, bad, shut = [client.interfaces[index]["custom_fields"] for index in (10, 11, 12)]
    assert good["nac_status"] == "compliant" and good["nac_issues"] == ""
    assert bad["nac_status"] == "noncompliant"
    assert "access-session closed" in bad["nac_issues"]
    assert "interface GigabitEthernet1/0/2\n access-session" in bad["nac_remediation"]
    assert "\n mab\n" not in bad["nac_remediation"]
    assert shut["nac_status"] == "skipped" and shut["nac_issues"] == "shut down"
    assert all(row["tags"] == [{"id": 9}] and row["custom_fields"]["owner"] == "network" for row in client.interfaces.values())
    assert all(set(payload) == {"custom_fields"} for method, _, payload in client.writes if method == "PATCH")
    assert bad["nac_poller"] == "poller-boston" and bad["nac_checked_at"] != OLD


@pytest.mark.parametrize("state,applying", [({"error": "SSH timed out"}, False), (record(), True)])
def test_failed_or_unverified_audits_are_unknown_and_keep_last_assessed_date(client, state, applying):
    result = nac_netbox.sync_device(client, host(), state, applying=applying)
    assert result["status"] == "written"
    for row in client.interfaces.values():
        fields = row["custom_fields"]
        assert fields["nac_status"] == "unknown" and fields["nac_checked_at"] == OLD
        assert fields["nac_last_attempt"] != OLD
        assert "rerun" in fields["nac_remediation"]


def test_verified_after_state_clears_old_failure_details(client):
    state = record()
    nac_netbox.sync_device(client, host(), state)
    after = copy.deepcopy(state["audit_before"])
    for port in after["interfaces"]:
        if port["status"] == "noncompliant":
            port.update(status="compliant", missing_lines=[])
    state["audit_after"] = after
    nac_netbox.sync_device(client, host(), state, applying=True)
    fields = client.interfaces[11]["custom_fields"]
    assert fields["nac_status"] == "compliant"
    assert fields["nac_issues"] == fields["nac_remediation"] == ""


def test_missing_observation_cannot_leave_stale_compliance(client):
    state = record()
    state["audit_before"]["interfaces"] = state["audit_before"]["interfaces"][1:]
    nac_netbox.sync_device(client, host(), state)
    assert client.interfaces[10]["custom_fields"]["nac_status"] == "unknown"
    assert client.interfaces[10]["custom_fields"]["nac_checked_at"] == OLD


def test_missing_netbox_interface_is_reported_without_creating_it(client):
    del client.interfaces[11]
    result = nac_netbox.sync_device(client, host(), record())
    assert result["status"] == "failed"
    assert "GigabitEthernet1/0/2" in result["errors"][0]
    assert not any(method == "POST" for method, _, _ in client.writes)


def test_write_failures_are_isolated_and_readback_is_checked(client):
    client.fail_ids = {10}
    result = nac_netbox.sync_device(client, host(), record())
    assert result["status"] == "failed"
    assert result["interfaces"][0]["status"] == "failed"
    assert result["interfaces"][1]["status"] == "written"
    client.fail_ids.clear()
    client.ignore_patch = True
    client.interfaces[11]["custom_fields"]["nac_status"] = "incorrect"
    result = nac_netbox.sync_device(client, host(), record())
    assert any("did not verify" in error for error in result["errors"])


def test_ipam_resolution_requires_unique_device(client):
    assert nac_netbox.device_id(client, host(False)) == 1
    client.addresses.append({**client.addresses[0], "assigned_object": {"device": {"id": 2}}})
    with pytest.raises(NetBoxError, match="found 2"):
        nac_netbox.device_id(client, host(False))


@pytest.mark.parametrize("setting,flag,expected", [("true", None, True), ("false", None, False), ("true", False, False)])
def test_sync_env_and_override(monkeypatch, setting, flag, expected):
    monkeypatch.setenv("NETOPS_NAC_NETBOX_SYNC", setting)
    assert nac_netbox.enabled(SimpleNamespace(sync_netbox=flag)) is expected


@pytest.mark.parametrize("source", ["netbox", "csv", "ip"])
def test_cli_audit_writes_interfaces_without_configuring_switch(
    client, ports, login, nac_standards, nac_webhook, monkeypatch, tmp_path, source
):
    monkeypatch.setattr("netops.netbox.Client", lambda *a, **kw: client)
    monkeypatch.setenv("NETBOX_URL", "https://netbox.example.com")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    monkeypatch.setenv("NETOPS_NAC_NETBOX_SYNC", "true")
    if source == "netbox":
        monkeypatch.setenv("NETOPS_INVENTORY", "netbox")
        monkeypatch.setenv("NETBOX_AUTOFILTER", "true")
        monkeypatch.setenv("NETOPS_POLLER", "boston")
        client.devices.append({**client.devices[0], "id": 2, "name": "other-poller",
                               "tags": [{"slug": "poller-dallas"}]})
        flags = []
    elif source == "csv":
        path = tmp_path / "hosts.csv"
        path.write_text("host,name,platform\n10.1.1.1,sw1,cisco_ios\n")
        flags = ["--csv", str(path)]
    else:
        ports["devices"]["10.1.1.1"] = ports["devices"]["sw1"]
        flags = ["--ip", "10.1.1.1", "--platform", "cisco_ios"]
    assert cli.main(["nac", "--no-env-file", *flags]) == cli.EXIT_OK
    assert ports["config"] == {}
    assert len(ports["commands"]) == 1
    assert client.interfaces[11]["custom_fields"]["nac_status"] == "noncompliant"
    delivered = json.loads(nac_webhook[0][1])
    result = next(iter(delivered["devices"].values()))
    assert result["netbox_writeback"]["status"] == "written"
    if source == "netbox":
        assert client.interfaces[11]["custom_fields"]["nac_poller"] == "poller-boston"


def test_cli_sync_failure_still_delivers_report_and_exits_nonzero(
    client, ports, login, nac_standards, nac_webhook, monkeypatch
):
    monkeypatch.setattr("netops.netbox.Client", lambda *a, **kw: client)
    monkeypatch.setenv("NETBOX_URL", "https://netbox.example.com")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    client.fail_ids = {11}
    assert cli.main(["nac", "--no-env-file", "--netbox", "--sync-netbox"]) == cli.EXIT_FAILED
    delivered = json.loads(nac_webhook[0][1])
    assert delivered["devices"]["sw1"]["netbox_writeback"]["status"] == "failed"


def test_no_sync_override_keeps_netbox_read_only(client, ports, login, nac_standards, monkeypatch):
    monkeypatch.setattr("netops.netbox.Client", lambda *a, **kw: client)
    monkeypatch.setenv("NETBOX_URL", "https://netbox.example.com")
    monkeypatch.setenv("NETBOX_TOKEN", "test-token")
    monkeypatch.setenv("NETOPS_NAC_NETBOX_SYNC", "true")
    assert cli.main(["nac", "--no-env-file", "--netbox", "--no-sync-netbox"]) == cli.EXIT_OK
    assert client.writes == []


def test_export_template_preserves_multiline_findings_and_quotes():
    from jinja2 import Environment

    path = Path(__file__).resolve().parents[1] / "export-templates" / "nac-interfaces.csv.j2"
    interface = SimpleNamespace(device=SimpleNamespace(name='switch,"one"'), name="Gi1/0/1", cf={
        "nac_status": "noncompliant", "nac_issues": 'Missing "mab"',
        "nac_remediation": "interface Gi1/0/1\n mab", "nac_poller": "poller-boston",
    })
    text = Environment().from_string(path.read_text()).render(queryset=[interface])
    rows = list(csv.DictReader(io.StringIO(text)))
    assert len(rows) == 1
    assert rows[0]["device"] == 'switch,"one"'
    assert rows[0]["findings"] == 'Missing "mab"'
    assert rows[0]["remediation"] == "interface Gi1/0/1\n mab"
    assert rows[0]["last_assessed"] == ""
