"""The probe: ask one device what it is, with no NetBox anywhere.

Runs against recorded walks, so it needs neither a NetBox nor net-snmp. What
matters here is that the report actually contains the things somebody reaches
for it to find out — and that it works with no NetBox configuration at all,
which is the whole point of it existing.
"""

from __future__ import annotations

import io
import json

import pytest
from conftest import ReplayCollector, collect_fixture, fixture_path

from snmpinv import probe as probe_module
from snmpinv.model import build_scan_result


def report_for(fixture: str) -> str:
    facts = collect_fixture(fixture)
    out = io.StringIO()
    probe_module._print_report(facts, build_scan_result(facts), out)
    return out.getvalue()


class TestTheReportSaysWhatWasFound:
    def test_a_stack_shows_every_member_with_its_own_serial(self):
        text = report_for("cisco-c9300-stack")
        for serial in ("FOC2530L0AB", "FOC2530L0CD", "FOC2531L0EF"):
            assert serial in text
        assert "C9300-24P" in text and "C9300-48P" in text
        # The stackwise table, decoded rather than shown as raw integers.
        assert "master" in text and "ready" in text

    def test_the_model_is_shown_verbatim(self):
        text = report_for("arista-7050sx")
        assert "DCS-7050SX-72Q" in text
        assert "aristaDCS7050SX272Q" not in text

    def test_sysobjectid_is_shown_as_a_vendor_not_a_model(self):
        """The enterprise arc names the vendor; nothing below it is interpreted."""
        text = report_for("arista-7050sx")
        assert "enterprise 30065 — Arista Networks" in text

    def test_interfaces_show_the_netbox_type_they_would_get(self):
        text = report_for("cisco-c9300-stack")
        assert "1000base-t" in text
        assert "10gbase-x-sfpp" in text
        assert "lag" in text

    def test_addresses_are_shown_against_their_interface(self):
        assert "10.10.1.5/24" in report_for("cisco-c9300-stack")

    def test_an_empty_entity_table_is_explained_not_just_blank(self):
        """A firewall reporting no ENTITY-MIB is normal, and the report should
        say so rather than leaving somebody wondering what broke."""
        text = report_for("palo-pa3220")
        assert "does not populate entPhysicalTable" in text
        # ...and the model it did find, from the vendor OID path.
        assert "PA-3220" in text

    def test_it_shows_what_would_be_created(self):
        text = report_for("cisco-c9300-stack")
        assert "WHAT THIS WOULD BECOME IN NETBOX" in text
        assert "virtual chassis" in text
        assert "member 1 (master)" in text

    def test_a_device_with_no_model_is_called_out(self):
        """The single most useful thing the report can tell you."""
        from snmpinv.collect import DeviceFacts

        facts = DeviceFacts(host="192.0.2.1", sys_name="mystery",
                            sys_object_id="1.3.6.1.4.1.99999.1")
        out = io.StringIO()
        probe_module._print_report(facts, build_scan_result(facts), out)
        text = out.getvalue()
        assert "NO MODEL" in text or "no chassis was identified" in text

    def test_aruba_controller_lists_its_access_points(self):
        text = report_for("aruba-7010-wlc")
        assert "ACCESS POINTS" in text
        assert "dal-ap-101" in text and "AP-515" in text


class TestJsonOutput:
    def test_it_is_valid_json_with_the_expected_shape(self):
        facts = collect_fixture("cisco-c9300-stack")
        data = probe_module.facts_to_dict(facts, build_scan_result(facts))
        # Round-trips, so it can be piped into anything.
        data = json.loads(json.dumps(data, default=str))
        assert set(data) >= {
            "host", "system", "identification", "entities", "stack_members",
            "interfaces", "ip_addresses", "would_create",
        }
        assert len(data["interfaces"]) == 18
        assert len(data["stack_members"]) == 3
        assert data["would_create"]["virtual_chassis"] == "bld-a-core-01"
        assert data["system"]["enterprise"] == 9

    def test_every_interface_carries_the_type_it_would_get(self):
        facts = collect_fixture("arista-7050sx")
        data = probe_module.facts_to_dict(facts, build_scan_result(facts))
        assert all("netbox_type" in i for i in data["interfaces"])


class TestItNeedsNoNetBox:
    def test_probe_config_loads_from_credentials_alone(self, tmp_path):
        """The point of the probe: it works before anything else is set up."""
        from snmpinv import config as config_module

        creds = tmp_path / "creds.conf"
        creds.write_text(
            "[credential:only]\n"
            "security_name = netops\n"
            "auth_protocol = SHA-256\n"
            "auth_passphrase = something\n"
            "priv_protocol = AES\n"
            "priv_passphrase = something-else\n"
        )
        config = config_module.load_for_probe(str(tmp_path / "nope.conf"), str(creds))
        assert len(config.credentials) == 1
        assert config.credentials[0].security_name == "netops"
        # No NetBox anywhere.
        assert config.netbox.url == ""
        assert config.netbox.token == ""

    def test_it_refuses_clearly_with_neither_config_nor_credentials(self, tmp_path):
        from snmpinv import config as config_module

        with pytest.raises(FileNotFoundError, match="--credentials"):
            config_module.load_for_probe(str(tmp_path / "nope.conf"), "")

    def test_the_probe_module_imports_nothing_netbox_shaped(self):
        """A probe must not be able to touch NetBox even by accident."""
        import inspect

        source = inspect.getsource(probe_module)
        assert "from .netbox" not in source and "import netbox" not in source
