"""The adjacency modelling: canonical names, dedupe, classification.

Pure logic over Neighbor sightings — no fixtures, no network, no NetBox. The
cases here are the ones that produce wrong cables when they regress: a
long-form and a short-form spelling failing to match, one link counted twice
because both protocols saw it, and a phone classified as a switch because it
carries the bridge bit.
"""

from __future__ import annotations

from snmpinv.collect import Neighbor
from snmpinv.neighbors import (
    CLASS_AP,
    CLASS_HOST,
    CLASS_NETWORK,
    CLASS_PHONE,
    CLASS_UNKNOWN,
    Adjacency,
    build_adjacencies,
    canonical_port,
    classify,
    short_name,
)


class TestCanonicalPort:
    def test_cisco_long_and_short_forms_meet_in_the_middle(self):
        pairs = (
            ("GigabitEthernet1/0/1", "Gi1/0/1"),
            ("TenGigabitEthernet1/1/1", "Te1/1/1"),
            ("TwentyFiveGigE1/0/1", "Twe1/0/1"),
            ("TwoGigabitEthernet1/0/1", "Tw1/0/1"),
            ("FortyGigabitEthernet1/1/1", "Fo1/1/1"),
            ("HundredGigE1/0/49", "Hu1/0/49"),
            ("FastEthernet0/1", "Fa0/1"),
            ("Port-channel10", "Po10"),
            ("Vlan100", "Vl100"),
        )
        for long_form, short_form in pairs:
            assert canonical_port(long_form) == canonical_port(short_form), long_form

    def test_twentyfivegig_is_not_swallowed_by_twogig(self):
        """Longest prefix must win: Twe is 25G, Tw is 2.5G, and confusing the
        two cables the wrong pair of ports."""
        assert canonical_port("TwentyFiveGigE1/0/1") == "twe1/0/1"
        assert canonical_port("TwoGigabitEthernet1/0/1") == "tw1/0/1"
        assert canonical_port("Twe1/0/1") != canonical_port("Tw1/0/1")

    def test_case_is_irrelevant(self):
        assert canonical_port("gigabitethernet1/0/1") == canonical_port("Gi1/0/1")

    def test_non_cisco_names_pass_through(self):
        # Juniper and F5 spell the same name on both sides already.
        assert canonical_port("ge-0/0/0") == "ge-0/0/0"
        assert canonical_port("xe-0/1/0") == "xe-0/1/0"
        assert canonical_port("1.1") == "1.1"
        # Arista's full spelling compresses the same way from either side.
        assert canonical_port("Ethernet49/1") == canonical_port("ethernet49/1")

    def test_an_unknown_prefix_is_left_alone_not_guessed(self):
        """The failure mode for exotic hardware must be a visible non-match,
        never a half-translated name that happens to hit something else."""
        assert canonical_port("FourHundredGigE1/0/1") == "fourhundredgige1/0/1"

    def test_a_prefix_must_end_where_numbering_starts(self):
        assert canonical_port("EthernetSwitchModule3") == "ethernetswitchmodule3"


class TestShortName:
    def test_domain_suffix_and_case_are_stripped(self):
        assert short_name("SW1.Corp.Example.COM") == "sw1"
        assert short_name("sw1") == "sw1"

    def test_empty_stays_empty(self):
        assert short_name("") == ""


def sighting(protocol="lldp", port="Gi1/0/1", if_index=1, sys_name="", port_id="",
             port_desc="", chassis="", caps=(), platform="", mgmt=""):
    return Neighbor(
        protocol=protocol, local_if_index=if_index, local_port=port,
        sys_name=sys_name, port_id=port_id, port_desc=port_desc,
        chassis_id=chassis,
        chassis_id_subtype=4 if ":" in chassis else 0,
        capabilities=frozenset(caps), platform=platform, mgmt_address=mgmt,
    )


class TestDedupe:
    def test_one_link_seen_by_both_protocols_is_one_adjacency(self):
        """Cisco boxes answer CDP and LLDP; long and short spellings of the
        remote port describe one link."""
        adjacencies = build_adjacencies([
            sighting("lldp", sys_name="sw2", port_id="Gi1/0/24",
                     chassis="AA:BB:CC:DD:EE:FF", caps={"bridge"}),
            sighting("cdp", sys_name="sw2.corp.example.com",
                     port_id="GigabitEthernet1/0/24",
                     platform="cisco WS-C2960X-48FPD-L", mgmt="10.0.0.2"),
        ])
        assert len(adjacencies) == 1
        merged = adjacencies[0]
        assert merged.protocols == ("cdp", "lldp")
        # The merge keeps the richer half of each field.
        assert merged.chassis_mac == "AA:BB:CC:DD:EE:FF"
        assert merged.platform == "cisco WS-C2960X-48FPD-L"
        assert merged.mgmt_address == "10.0.0.2"
        assert merged.remote_port == "Gi1/0/24"

    def test_ports_differing_but_names_matching_still_merge(self):
        """A phone's LLDP names its port in portDesc ("SW PORT") while CDP
        says "Port 1" — same box, same link."""
        adjacencies = build_adjacencies([
            sighting("lldp", sys_name="SEP001122334455",
                     port_id="00:11:22:33:44:55", port_desc="SW PORT",
                     caps={"telephone", "bridge"}),
            sighting("cdp", sys_name="SEP001122334455", port_id="Port 1",
                     platform="Cisco IP Phone 7962"),
        ])
        assert len(adjacencies) == 1
        # CDP's devicePort outranks LLDP's descriptive text.
        assert adjacencies[0].remote_port == "Port 1"

    def test_two_devices_on_one_port_stay_two_adjacencies(self):
        """A PC heard through a phone's pass-through port is a second,
        distinct neighbor on the same local interface."""
        adjacencies = build_adjacencies([
            sighting("lldp", sys_name="SEP001122334455", port_id="Port 1",
                     caps={"telephone", "bridge"}),
            sighting("lldp", sys_name="user-laptop", port_id="eth0",
                     caps={"stationOnly"}),
        ])
        assert len(adjacencies) == 2

    def test_incomparable_sightings_are_not_guessed_together(self):
        adjacencies = build_adjacencies([
            sighting("lldp", port_id="00:11:22:33:44:55", sys_name=""),
            sighting("cdp", port_id="", sys_name=""),
        ])
        assert len(adjacencies) == 2

    def test_different_local_ports_never_merge(self):
        adjacencies = build_adjacencies([
            sighting("lldp", port="Gi1/0/1", if_index=1, sys_name="sw2",
                     port_id="Gi1/0/24"),
            sighting("lldp", port="Gi1/0/2", if_index=2, sys_name="sw2",
                     port_id="Gi1/0/25"),
        ])
        assert len(adjacencies) == 2


def adjacency(caps=(), platform="", sys_name="peer"):
    return Adjacency(
        local_port="Gi1/0/1", local_if_index=1, local_port_source="",
        remote_name=sys_name, remote_port="x", remote_port_source="",
        platform=platform, capabilities=frozenset(caps),
    )


class TestClassify:
    def test_switches_and_routers_are_network(self):
        assert classify(adjacency(caps={"bridge", "router"})) == CLASS_NETWORK
        assert classify(adjacency(caps={"switch", "igmp"})) == CLASS_NETWORK
        assert classify(adjacency(caps={"router"})) == CLASS_NETWORK

    def test_a_phone_is_a_phone_despite_its_bridge_bit(self):
        assert classify(adjacency(caps={"bridge", "telephone"})) == CLASS_PHONE

    def test_cdp_phones_are_recognised_by_platform(self):
        """CDP's capability word is spec-defined rather than MIB-defined, so
        the platform string the phone reports about itself is the signal."""
        assert classify(adjacency(caps={"host"},
                                  platform="Cisco IP Phone 7962")) == CLASS_PHONE

    def test_an_ap_is_an_ap_despite_its_bridge_bit(self):
        assert classify(adjacency(caps={"bridge", "wlanAccessPoint"})) == CLASS_AP
        assert classify(adjacency(caps={"host"},
                                  platform="cisco AIR-AP2802I-B-K9")) == CLASS_AP

    def test_hosts_and_stations(self):
        assert classify(adjacency(caps={"host"})) == CLASS_HOST
        assert classify(adjacency(caps={"stationOnly"})) == CLASS_HOST

    def test_nothing_reported_is_unknown_not_network(self):
        """No capabilities and no platform must not default into the class
        that gets cabled."""
        assert classify(adjacency()) == CLASS_UNKNOWN
