"""Hostnames with dots in them, and the domains to take off.

The first-label rule made "sw1.floor2.corp.example.com" and
"sw1.floor3.corp.example.com" the same device name. The Discovery plugin now
holds the list of domains to strip, and everything else about a hostname --
dots included -- is kept. These pin the stripping itself, that nothing changes
until a domain is listed, and that records named under the old rule are
recognised and renamed rather than duplicated or, for a stack, torn apart.
"""

from __future__ import annotations

import logging

import pytest
from conftest import collect_fixture
from test_contexts import FakeNetBox, _id, chassis_in, syncer

from snmpinv import naming
from snmpinv.cables import CableSyncer
from snmpinv.collect import DeviceFacts, Entity
from snmpinv.mibs import ENT_CLASS_CHASSIS
from snmpinv.model import DeviceRecord, ScanResult, build_scan_result
from snmpinv.naming import derivations, device_name, load_stripped_domains, strip_domain
from snmpinv.netbox import NetBoxError
from snmpinv.sync import MANUAL_TAG, SyncOptions, Syncer

DOMAINS = ("google.com",)


class TestTheExampleFromTheRequest:
    def test_the_domain_and_the_dot_that_joined_it_come_off(self):
        assert device_name("test.google.com", DOMAINS) == "test"

    def test_dots_that_belong_to_the_hostname_are_kept(self):
        assert device_name("sw1.floor2.google.com", DOMAINS) == "sw1.floor2"
        assert device_name("sw1.floor2", DOMAINS) == "sw1.floor2"

    def test_a_domain_not_on_the_list_stays(self):
        assert device_name("sw1.other.net", DOMAINS) == "sw1.other.net"


class TestStripping:
    @pytest.mark.parametrize("hostname, domains, expected", [
        ("Test.Google.COM", ("google.com",), "Test"),              # case-insensitive, case kept
        ("test.google.com.", ("google.com",), "test"),             # a rooted FQDN
        ("test.google.com", (".Google.com.",), "test"),            # however the entry was typed
        ("testgoogle.com", ("google.com",), "testgoogle.com"),     # only at a label boundary
        ("google.com", ("google.com",), "google.com"),             # never stripped to nothing
        ("sw1.corp.google.com", ("google.com", "corp.google.com"), "sw1"),   # longest wins
        ("sw1.corp.google.com", ("google.com",), "sw1.corp"),
        ("google.com.evil.net", ("google.com",), "google.com.evil.net"),     # only from the end
        ("sw1", ("google.com",), "sw1"),
        ("", ("google.com",), ""),
    ])
    def test_strip_domain(self, hostname, domains, expected):
        assert strip_domain(hostname, domains) == expected

    def test_no_list_means_the_old_rule(self):
        """A NetBox without the plugin, or where nobody has set this up."""
        assert device_name("sw1.floor2.google.com") == "sw1"
        assert device_name("sw1.floor2.google.com", ()) == "sw1"

    def test_blank_entries_do_not_count_as_a_list_entry_that_matches(self):
        assert strip_domain("sw1.google.com", ("", "  ", ".")) == "sw1.google.com"

    def test_every_name_a_rule_could_have_given(self):
        assert derivations("sw1.floor2.google.com", DOMAINS) == (
            "sw1", "sw1.floor2.google.com", "sw1.floor2")
        assert derivations("sw1", DOMAINS) == ("sw1",)
        assert derivations("", DOMAINS) == ()


def facts_for(sys_name, host="10.0.0.1", serial="SER1", model="C9300-24P"):
    facts = DeviceFacts(host=host)
    facts.sys_name = sys_name
    facts.entities = [Entity(index=1, entity_class=ENT_CLASS_CHASSIS, serial=serial, model=model)]
    return facts


class TestTheScanResult:
    def test_the_device_is_named_under_the_list(self):
        result = build_scan_result(facts_for("sw1.floor2.google.com"), strip_domains=DOMAINS)
        assert result.primary.name == "sw1.floor2"
        # What the old rule, and no rule, would have called it.
        assert set(result.primary.former_names) == {"sw1", "sw1.floor2.google.com"}

    def test_without_a_list_nothing_changes(self):
        result = build_scan_result(facts_for("sw1.floor2.google.com"))
        assert result.primary.name == "sw1"

    def test_no_hostname_still_falls_back_to_the_address(self):
        result = build_scan_result(facts_for(""), strip_domains=DOMAINS)
        assert result.primary.name == "10.0.0.1"
        assert result.primary.former_names == ()

    def test_a_stack_names_its_members_and_chassis_from_the_same_name(self):
        facts = collect_fixture("cisco-c9300-stack")
        facts.sys_name = "dal-stack.idf2.google.com"
        result = build_scan_result(facts, strip_domains=DOMAINS)
        assert result.virtual_chassis_name == "dal-stack.idf2"
        assert [d.name for d in result.devices] == [
            "dal-stack.idf2", "dal-stack.idf2-2", "dal-stack.idf2-3"]
        assert "dal-stack-2" in result.devices[1].former_names
        assert "dal-stack" in result.former_chassis_names


class TestReadingTheListFromThePlugin:
    def test_enabled_domains_normalised_and_deduplicated(self):
        class Fake:
            def all(self, path, params=None):
                assert path == naming.STRIPPED_DOMAINS_ENDPOINT
                assert params == {"enabled": "true"}
                return [{"domain": "Google.com"}, {"domain": ".corp.example.com."},
                        {"domain": "google.com"}, {"domain": ""}]
        assert load_stripped_domains(Fake()) == ("google.com", "corp.example.com")

    def test_a_netbox_without_the_plugin_means_the_old_rule(self, caplog):
        class NoPlugin:
            def all(self, path, params=None):
                raise NetBoxError("GET %s -> 404: not found" % path)
        with caplog.at_level(logging.DEBUG):
            assert load_stripped_domains(NoPlugin()) == ()
        assert "could not read" not in caplog.text

    def test_any_other_failure_is_said_out_loud(self, caplog):
        """A list that exists and is silently ignored would name devices
        differently from one run to the next."""
        class Forbidden:
            def all(self, path, params=None):
                raise NetBoxError("GET %s -> 403: permission denied" % path)
        with caplog.at_level(logging.WARNING):
            assert load_stripped_domains(Forbidden()) == ()
        assert "could not read the stripped-domain list" in caplog.text


# --- records named under the old rule -----------------------------------------


def record(name, former=(), serial="SER1", **extra):
    return DeviceRecord(name=name, serial=serial, model="C9300-24P", manufacturer="Cisco",
                        former_names=tuple(former), **extra)


class TestFollowingAChangeOfRule:
    def test_a_record_under_the_old_name_is_the_same_device_not_a_second_one(self):
        """In NetBox as 'sw1', re-addressed since, so only the old name agrees.
        Recognised -- and not renamed: a name is never made longer."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="sw1", address="10.0.0.1", serial="SER1")
        scan = ScanResult(host="10.0.0.9", devices=[
            record("sw1.floor2", former=("sw1", "sw1.floor2.google.com"))])
        syncer(netbox).sync(scan, site_id=1, scanned_address="10.0.0.9")
        assert [d["name"] for d in netbox.devices()] == ["sw1"]

    def test_listing_a_first_domain_does_not_turn_other_domains_into_fqdns(self):
        """The cliff: google.com is listed, other.net is not yet, and the
        fleet already holds 'sw9'. It must stay 'sw9'."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="sw9", address="10.0.0.1", serial="SER1")
        result = build_scan_result(facts_for("sw9.other.net"), strip_domains=DOMAINS)
        assert result.primary.name == "sw9.other.net"
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.1")
        assert [d["name"] for d in netbox.devices()] == ["sw9"]

    def test_a_name_somebody_typed_is_left_alone(self):
        netbox = FakeNetBox()
        chassis_in(netbox, name="Floor 2 closet switch", address="10.0.0.1", serial="SER1")
        scan = ScanResult(host="10.0.0.1", devices=[
            record("sw1.floor2", former=("sw1", "sw1.floor2.google.com"))])
        syncer(netbox).sync(scan, site_id=1, scanned_address="10.0.0.1")
        assert [d["name"] for d in netbox.devices()] == ["Floor 2 closet switch"]

    def test_a_missed_domain_heals_once_it_is_listed(self, caplog):
        """Forgot other.net, so a new device was created as 'sw9.other.net'.
        Listing it takes the domain off, because the full hostname is a name
        the scanner gave."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="sw9.other.net", address="10.0.0.1", serial="SER1")
        result = build_scan_result(facts_for("sw9.other.net"),
                                   strip_domains=("google.com", "other.net"))
        syncer(netbox).sync(result, site_id=1, scanned_address="10.0.0.1")
        assert [d["name"] for d in netbox.devices()] == ["sw9"]

    def test_a_name_already_taken_at_the_site_is_not_fought_over(self, caplog):
        netbox = FakeNetBox()
        chassis_in(netbox, name="sw9.other.net", address="10.0.0.1", serial="SER1")
        chassis_in(netbox, name="sw9", address="10.0.0.2", serial="OTHER")
        scan = ScanResult(host="10.0.0.1", devices=[record("sw9", former=("sw9.other.net",))])
        with caplog.at_level(logging.WARNING):
            syncer(netbox).sync(scan, site_id=1, scanned_address="10.0.0.1")
        assert sorted(d["name"] for d in netbox.devices()) == ["sw9", "sw9.other.net"]
        assert "already taken at this site" in caplog.text

    def test_a_hand_entered_device_keeps_its_name(self):
        netbox = FakeNetBox()
        device = chassis_in(netbox, name="sw9.other.net", address="10.0.0.1", serial="SER1")
        device["tags"] = [{"slug": MANUAL_TAG}]
        scan = ScanResult(host="10.0.0.1", devices=[record("sw9", former=("sw9.other.net",))])
        syncer(netbox).sync(scan, site_id=1, scanned_address="10.0.0.1")
        assert [d["name"] for d in netbox.devices()] == ["sw9.other.net"]

    def test_the_collision_this_feature_exists_to_end(self):
        """sw1.floor3 must never be taken for the 'sw1' that is really
        sw1.floor2: a former name is evidence only beside a matching serial."""
        netbox = FakeNetBox()
        chassis_in(netbox, name="sw1", address="10.0.0.2", serial="FLOOR2")
        scan = ScanResult(host="10.0.0.3", devices=[
            record("sw1.floor3", former=("sw1",), serial="FLOOR3")])
        syncer(netbox).sync(scan, site_id=1, scanned_address="10.0.0.3")
        assert sorted(d["name"] for d in netbox.devices()) == ["sw1", "sw1.floor3"]
        assert {d["name"]: d["serial"] for d in netbox.devices()}["sw1"] == "FLOOR2"


class TestAStackThroughAChangeOfRule:
    """The dangerous case. A member whose name stopped agreeing used to fall
    through to the address lookup, find the MASTER, and retire it as swapped."""

    def stack_in(self, netbox, base="dal-stack"):
        chassis = netbox.add("/dcim/virtual-chassis/", name=base)
        master = chassis_in(netbox, name=base, address="10.0.0.1", serial="M1")
        master["virtual_chassis"] = {"id": chassis["id"], "name": base}
        master["vc_position"] = 1
        member = netbox.add("/dcim/devices/", name=f"{base}-2", serial="M2", site=1,
                            virtual_chassis={"id": chassis["id"], "name": base}, vc_position=2)
        return chassis, master, member

    def scan(self, base, former):
        return ScanResult(
            host="10.0.0.1", virtual_chassis_name=base, former_chassis_names=tuple(former),
            devices=[
                record(base, former=former, serial="M1", vc_position=1, vc_is_master=True),
                record(f"{base}-2", former=[f"{f}-2" for f in former], serial="M2",
                       vc_position=2),
            ])

    def test_a_stack_whose_derived_name_changed_stays_one_stack(self):
        """No second virtual chassis, no duplicate members, nothing retired."""
        netbox = FakeNetBox()
        chassis, master, member = self.stack_in(netbox)
        syncer(netbox).sync(self.scan("dal-stack.idf2", ["dal-stack"]),
                            site_id=1, scanned_address="10.0.0.1")
        assert [d["name"] for d in netbox.devices()] == ["dal-stack", "dal-stack-2"]
        assert len(netbox.objects["/dcim/virtual-chassis/"]) == 1
        assert {_id(d["virtual_chassis"]) for d in netbox.devices()} == {chassis["id"]}
        assert {d["serial"] for d in netbox.devices()} == {"M1", "M2"}

    def test_a_stack_created_under_a_missed_domain_has_it_taken_off(self):
        netbox = FakeNetBox()
        chassis, master, member = self.stack_in(netbox, base="dal-stack.other.net")
        syncer(netbox).sync(self.scan("dal-stack", ["dal-stack.other.net"]),
                            site_id=1, scanned_address="10.0.0.1")
        assert [d["name"] for d in netbox.devices()] == ["dal-stack", "dal-stack-2"]
        assert [c["name"] for c in netbox.objects["/dcim/virtual-chassis/"]] == ["dal-stack"]

    def test_a_member_somebody_renamed_is_still_that_member_and_the_master_survives(self):
        netbox = FakeNetBox()
        chassis, master, member = self.stack_in(netbox)
        member["name"] = "IDF2 bottom switch"
        syncer(netbox).sync(self.scan("dal-stack", []), site_id=1, scanned_address="10.0.0.1")
        names = [d["name"] for d in netbox.devices()]
        assert names == ["dal-stack", "IDF2 bottom switch"]
        assert not any("retired" in n or "replaced" in n for n in names)
        assert netbox.device("dal-stack")["serial"] == "M1"

    def test_a_chassis_somebody_named_keeps_its_name_but_is_still_used(self):
        netbox = FakeNetBox()
        chassis, master, member = self.stack_in(netbox)
        netbox.objects["/dcim/virtual-chassis/"][0]["name"] = "Dallas IDF2"
        for device in netbox.devices():
            device["virtual_chassis"]["name"] = "Dallas IDF2"
        syncer(netbox).sync(self.scan("dal-stack.idf2", ["dal-stack"]),
                            site_id=1, scanned_address="10.0.0.1")
        assert [c["name"] for c in netbox.objects["/dcim/virtual-chassis/"]] == ["Dallas IDF2"]


class TestCabledNeighbors:
    """A neighbor announces itself by hostname; it is looked up under the
    name its own scan would have given it."""

    class Lookups:
        def __init__(self, names):
            self.names, self.asked = names, []

        def all(self, path, params=None):
            self.asked.append(params["name__ie"])
            return [{"id": 1, "name": n} for n in self.names
                    if n.lower() == params["name__ie"].lower()]

    class Adj:
        def __init__(self, name):
            self.remote_name = name

        def describe(self):
            return self.remote_name

    def resolve(self, names, reported, domains):
        netbox = self.Lookups(names)
        cables = CableSyncer(netbox, strip_domains=domains)
        found = cables._resolve_remote_device(self.Adj(reported), cables.report)
        return found, netbox.asked

    def test_a_dotted_hostname_is_found_under_its_stripped_name(self):
        found, asked = self.resolve(["sw1.floor2", "sw1"], "sw1.floor2.google.com", DOMAINS)
        assert found["name"] == "sw1.floor2"
        assert asked == ["sw1.floor2.google.com", "sw1.floor2"]

    def test_without_a_list_the_first_label_is_tried_as_before(self):
        found, asked = self.resolve(["sw1"], "sw1.corp.example.com", ())
        assert found["name"] == "sw1"
        assert asked == ["sw1.corp.example.com", "sw1"]


class TestTheProbe:
    def test_strip_domain_is_offered_on_the_command_line(self):
        import snmp_inventory
        args = snmp_inventory.parse_args(["--probe", "10.0.0.1", "--strip-domain", "google.com",
                                          "--strip-domain", "other.net"])
        assert args.strip_domain == ["google.com", "other.net"]
