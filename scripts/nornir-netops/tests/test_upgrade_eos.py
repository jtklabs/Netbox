"""Arista EOS upgrades against offline SSH transcripts; never a real switch."""

import re
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import yaml

from netops.upgrade import checks, eos_checks, eos_workflow, report, scheduler, workflow
from netops.upgrade.profile import Profile, eos_version

MODEL = "DCS-7050SX3-48YC8"
INSTALL = "install source flash:EOS-4.32.1F.swi now reload"
# Single-supervisor output as Arista documents it for `install source`.
INSTALL_OUTPUT = "Preparing new boot-config... done.\nCommitting changes on this supervisor... done.\nReloading this supervisor...\n"


@pytest.fixture
def profile():
    return Profile("lab-eos", (MODEL,), ("4.30.5M",), "4.32.1F", "EOS-4.32.1F.swi", "a" * 32, 200_000_000)


def boot_config(image):
    return f"Current CLI settings:\nSoftware image: flash:/{image}\nConsole speed: (not set)\nAboot password (encrypted): (not set)\nMemory test iterations: (not set)\n"


CONFIG = """hostname leaf1
!
spanning-tree mode mstp
!
vlan 10
   name users
!
vlan 20
   name cust
!
vlan 4094
   name mlag-peer
   trunk group mlag-peer
!
vrf instance CUST
!
interface Port-Channel10
   switchport mode trunk
   mlag 10
!
interface Port-Channel999
   switchport mode trunk
   switchport trunk group mlag-peer
!
interface Ethernet1
   !! server-facing port
   switchport access vlan 10
!
interface Ethernet3
   channel-group 10 mode active
!
interface Ethernet4
   channel-group 10 mode active
!
interface Ethernet47
   channel-group 999 mode active
!
interface Ethernet48
   channel-group 999 mode active
!
interface Vlan10
   ip address 10.10.0.1/24
   vrrp 1 ipv4 10.10.0.254
!
interface Vlan20
   vrf CUST
   ip address 10.20.0.1/24
!
interface Vlan4094
   ip address 10.255.255.1/30
!
ip routing
ip routing vrf CUST
!
mlag configuration
   domain-id mlag1
   local-interface Vlan4094
   peer-address 10.255.255.2
   peer-link Port-Channel999
!
router bgp 65001
   router-id 192.0.2.1
   neighbor 10.0.0.2 remote-as 65100
   !
   vrf CUST
      neighbor 10.20.0.2 remote-as 65200
!
router general
   router-id ipv4 192.0.2.1
!
end
"""


def transcript(release="4.30.5M", boot="EOS-4.30.5M.swi", mlag_state="Active"):
    return {
        "show version": (f"Arista {MODEL}\nHardware version: 11.00\nSerial number: JPE12345678\nHardware MAC address: 2cdd.e9aa.bbcc\n"
                         f"System MAC address: 2cdd.e9aa.bbcc\n\nSoftware image version: {release}\nArchitecture: x86_64\n"
                         f"Internal build version: {release}-12345678\nInternal build ID: 1d1c3a5b-6f2e-4a7b-8c9d-0e1f2a3b4c5d\n"
                         "Image format version: 3.0\nImage optimization: Default\n\nUptime: 3 weeks, 2 days, 4 hours and 12 minutes\n"
                         "Total memory: 8099732 kB\nFree memory: 5432100 kB\n"),
        "show boot-config": boot_config(boot),
        "show running-config": f"! Command: show running-config\n! device: leaf1 ({MODEL}, EOS-{release})\n!\n! boot system flash:/{boot}\n!\n" + CONFIG,
        "show startup-config": (f"! Command: show startup-config\n! Startup-config last modified at Thu Sep 11 19:00:00 2026 by admin\n"
                                f"! device: leaf1 ({MODEL}, EOS-{release})\n!\n! boot system flash:/{boot}\n!\n" + CONFIG),
        "show interfaces status": (
            "Port       Name              Status       Vlan     Duplex Speed  Type            Flags Encapsulation\n"
            "Et1        to-server1        connected    10       full   10G    10GBASE-SR\n"
            "Et2                          notconnect   1        full   10G    Not Present\n"
            "Et3        mlag 10           connected    trunk    full   10G    10GBASE-SR\n"
            "Et4        mlag 10           connected    trunk    full   10G    10GBASE-SR\n"
            "Et47       mlag peer         connected    trunk    full   10G    10GBASE-SR\n"
            "Et48       mlag peer         connected    trunk    full   10G    10GBASE-SR\n"
            "Po10       mlag-10           connected    trunk    full   20G    N/A\n"
            "Po999      mlag-peer         connected    trunk    full   20G    N/A\n"
            "Ma1                          connected    routed   a-full a-1G   10/100/1000\n"),
        "show ip interface brief": (
            "                                                                              Address\n"
            "Interface         IP Address           Status       Protocol           MTU    Owner  \n"
            "----------------- -------------------- ------------ -------------- ---------- -------\n"
            "Management1       192.0.2.10/24        up           up                1500           \n"
            "Vlan10            10.10.0.1/24         up           up                1500           \n"
            "Vlan4094          10.255.255.1/30      up           up                1500           \n"),
        "show mac address-table": (
            "          Mac Address Table\n------------------------------------------------------------------\n\n"
            "Vlan    Mac Address       Type        Ports      Moves   Last Move\n----    -----------       ----        -----      -----   ---------\n"
            "  10    001c.7300.0110    DYNAMIC     Et1        1       0:12:34 ago\n"
            "  10    2cdd.e9aa.bbcd    STATIC      Po10\n"
            "Total Mac Addresses for this criterion: 2\n\n"
            "          Multicast Mac Address Table\n------------------------------------------------------------------\n\n"
            "Vlan    Mac Address       Type        Ports\n----    -----------       ----        -----\n"
            "Total Mac Addresses for this criterion: 0\n"),
        "show vlan": (
            "VLAN  Name                             Status    Ports\n----- -------------------------------- --------- -------------------------------\n"
            "1     default                          active    Et2\n"
            "10    users                            active    Cpu, Et1, Po10\n"
            "20    cust                             active    Cpu\n"
            "4094  mlag-peer                        active    Cpu, Po999\n"),
        "show port-channel summary": (
            "Flags\n------------------------ ---------------------------- -------------------------\n"
            "  a - LACP Active          p - LACP Passive           * - static\n"
            "  s - Suspended            c - Config Error           R - Reload Pending\n"
            "Number of channels in use: 2\nNumber of aggregators:2\n\n"
            "   Port-Channel       Protocol    Ports\n------------------ -------------- ------------------\n"
            "   Po10(U)            LACP(a)     Et3(PG+) Et4(PG+)\n"
            "   Po999(U)           LACP(a)     Et47(PG+) Et48(PG+)\n"),
        "show ip arp": ("Address         Age (sec)  Hardware Addr   Interface\n"
                        "10.10.0.2         0:00:12  001c.7300.0110  Vlan10, Ethernet1\n"
                        "10.255.255.2      0:00:05  2cdd.e9aa.bbdd  Vlan4094, Port-Channel999\n"),
        "show ip arp vrf CUST": "Address         Age (sec)  Hardware Addr   Interface\n10.20.0.2         0:00:30  001c.7300.0220  Vlan20, not learned\n",
        "show ip route": (
            "VRF: default\nCodes: C - connected, S - static, K - kernel,\n       O - OSPF, IA - OSPF inter area, E2 - OSPF external type 2,\n"
            "       B - Other BGP Routes, B I - iBGP, B E - eBGP\n\nGateway of last resort:\n"
            " S        0.0.0.0/0 [1/0] via 10.10.0.254, Vlan10\n\n"
            " C        10.10.0.0/24 is directly connected, Vlan10\n"
            " C        10.255.255.0/30 is directly connected, Vlan4094\n"
            " B E      10.1.0.0/24 [200/0] via 10.10.0.2, Vlan10\n\n"),
        "show ip route vrf CUST": (
            "VRF: CUST\nCodes: C - connected, S - static, K - kernel,\n       B - Other BGP Routes, B I - iBGP, B E - eBGP\n\n"
            "Gateway of last resort is not set\n\n C        10.20.0.0/24 is directly connected, Vlan20\n\n"),
        "show vrf": (
            "Maximum number of vrfs allowed: 1023\n"
            "   VRF         Protocols       State                   Interfaces\n"
            "------------- --------------- ---------------------- ---------------------------\n"
            "   CUST        ipv4,ipv6       v4:routing,            Vlan20\n"
            "                               v6:no routing\n"),
        "show mlag": (
            "MLAG Configuration:\ndomain-id                          :             mlag1\nlocal-interface                    :           Vlan4094\n"
            "peer-address                       :       10.255.255.2\npeer-link                          :    Port-Channel999\n"
            "peer-config                        :         consistent\n\nMLAG Status:\n"
            f"state                              :            {mlag_state}\nnegotiation status                 :         Connected\n"
            "peer-link status                   :                Up\nlocal-int status                   :                Up\n"
            "system-id                          :  02:1c:73:aa:bb:cc\ndual-primary detection             :          Disabled\n\n"
            "MLAG Ports:\nDisabled                           :                  0\nConfigured                         :                  0\n"
            "Inactive                           :                  0\nActive-partial                     :                  0\n"
            "Active-full                        :                  1\n"),
        "show mlag interfaces": (
            "                                                                 local/remote\n"
            "   mlag       desc                     state       local       remote          status\n"
            "---------- ---------------------- ----------------- ----------- ------------ ------------\n"
            "      10                              active-full        Po10         Po10        up/up\n"),
        "show spanning-tree": (
            "MST0\n  Spanning tree enabled protocol mstp\n  Root ID    Priority    4096\n             Address     001c.7300.0001\n"
            "             This bridge is the root\n\n  Bridge ID  Priority    4096  (priority 4096 sys-id-ext 0)\n"
            "             Address     001c.7300.0001\n             Hello Time  2.000 sec  Max Age 20 sec  Forward Delay 15 sec\n\n"
            "Interface        Role       State      Cost      Prio.Nbr Type\n---------------- ---------- ---------- --------- -------- --------------------\n"
            "Et1              designated forwarding 2000      128.1    P2p\n"
            "Po10             designated forwarding 1999      128.4096 P2p\n"
            "Po999            designated forwarding 1999      128.4095 P2p\n"),
        "show lldp neighbors": (
            "Last table change time   : 3 days, 2:00:00 ago\nNumber of table inserts  : 5\nNumber of table deletes  : 1\n"
            "Number of table drops    : 0\nNumber of table age-outs : 1\n\n"
            "Port          Neighbor Device ID       Neighbor Port ID    TTL\n---------- ------------------------ ---------------------- ---\n"
            "Et1           server1                  eth0                120\n"
            "Et47          leaf2                    Ethernet47          120\n"
            "Et48          leaf2                    Ethernet48          120\n"
            "Ma1           mgmt-sw                  Gi1/0/5             120\n"),
        "show vrrp": ("Vlan10 - Group 1\n  VRF is default\n  VRRP Version 3\n  State is Master\n  Virtual IPv4 address is 10.10.0.254\n"
                      "  Virtual MAC address is 00:00:5e:00:01:01\n  Priority is 200\n  Master Router is 10.10.0.1 (local), priority is 200\n"),
        "show ip bgp summary vrf all": (
            "BGP summary information for VRF default\nRouter identifier 192.0.2.1, local AS number 65001\n"
            "Neighbor Status Codes: m - Under maintenance\n"
            "  Description              Neighbor V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc\n"
            "  spine1                   10.0.0.2 4 65100            100       100    0    0 01:00:00 Estab   10     10\n\n"
            "BGP summary information for VRF CUST\nRouter identifier 192.0.2.1, local AS number 65001\n"
            "Neighbor Status Codes: m - Under maintenance\n"
            "  Description              Neighbor V AS           MsgRcvd   MsgSent  InQ OutQ  Up/Down State   PfxRcd PfxAcc\n"
            "                           10.20.0.2 4 65200            50        50    0    0 00:30:00 Estab   3      3\n"),
        "show reload cause": "Reload Cause 1:\n-------------------\nReload requested by the user.\n\nRecommended Action:\n-------------------\nNo action necessary.\n",
        "show inventory": f"System information\n  Model                    Description\n  ------------------------ ----------------------------------------------------\n  {MODEL}        48x 25GbE SFP28 + 8x 100GbE QSFP28\n",
        "show environment all": "System temperature status is: Ok\n\nSensor  Description   Temperature  Alert  Overheat\n1       Cpu temp      45.0C        False  95.0C\nSystem power status: Ok\n",
        "show interfaces counters errors": "Port      FCSErr    AlignErr   SymbolErr     Rx     Runts    Giants     Tx\nEt1            0           0           0      0         0         0      0\nEt3            0           0           0      0         0         0      0\n",
        "show processes top once": ("top - 20:00:00 up 3 days,  2:00,  1 user,  load average: 0.52, 0.44, 0.40\nTasks: 300 total,   1 running, 299 sleeping,   0 stopped,   0 zombie\n"
                                    "%Cpu(s):  5.2 us,  2.1 sy,  0.0 ni, 92.4 id,  0.0 wa,  0.1 hi,  0.2 si,  0.0 st\n"),
        "show ntp status": "synchronised to NTP server (10.0.0.5) at stratum 2\n   time correct to within 20 ms\n   polling server every 1024 s\n",
        "show ntp associations": ("     remote           refid      st t when poll reach   delay   offset  jitter\n"
                                  "==============================================================================\n"
                                  "*10.0.0.5        .GPS.            1 u  100 1024  377    0.500    0.100   0.050\n"
                                  "+10.0.0.6        10.0.0.5         2 u  200 1024  377    0.600    0.200   0.060\n"),
        "show clock": "Thu Sep 11 20:00:00 2026\nTimezone: UTC\nClock source: NTP server (10.0.0.5)\n",
        "show logging": "Sep 11 19:59:00 leaf1 Cli: %SYS-5-CONFIG_I: Configured from console by admin on vty4 (192.0.2.50)\n",
        "show interfaces trunk": "Port       Mode     Status     Native vlan\nPo10       on       trunking   1\nPo999      on       trunking   1\n",
        "show ipv6 route": "VRF: default\n! IPv6 routing not enabled\n",
        "show ipv6 neighbors": "IPv6 Address  Age  Hardware Addr  State  Interface\n",
        "dir flash:": (f"Directory of flash:/\n\n       -rwx   1100000000           Sep 11  2026  {boot}\n       -rwx          12           Sep 11  2026  boot-config\n\n"
                       "3959422976 bytes total (2500000000 bytes free)\n"),
    }


def baseline(raw=None):
    data = transcript() if raw is None else raw
    return eos_checks.collect(lambda cmd: data.get(cmd, "% Invalid input"), lambda cmd: None)


def test_baseline_parses_realistic_eos_output_without_errors_or_warnings():
    snapshot = baseline()
    assert snapshot["errors"] == {} and snapshot["warnings"] == {}
    assert snapshot["software"] == {"1": {"model": MODEL, "version": "4.30.5M", "mode": "x86_64"}}
    assert snapshot["stack"] == {"1": {"mac": "2cdd.e9aa.bbcc", "serial": "JPE12345678", "state": "standalone"}}
    assert snapshot["boot"] == {"image": "EOS-4.30.5M.swi"}
    assert snapshot["config"] == snapshot["startup_config"]
    tables = snapshot["tables"]
    assert {"port": "Et1", "status": "connected", "vlan_id": "10", "duplex": "full", "speed": "10G"} in tables["interfaces"]
    assert len(tables["interfaces"]) == 9
    assert [row["interface"] for row in tables["ip_interfaces"]] == ["Management1", "Vlan10", "Vlan4094"]
    assert {"vlan_id": "10", "mac_address": "2cdd.e9aa.bbcd", "type": "STATIC", "destination_port": ["Po10"]} in tables["mac"]
    assert len(tables["mac"]) == 2
    assert [row["vlan_id"] for row in tables["vlans"]] == ["1", "10", "20", "4094"]
    assert {row["bundle_name"] for row in tables["port_channels"]} == {"Po10", "Po999"}
    assert tables["mlag"][0]["state"] == "Active" and tables["mlag"][0]["ports_active_full"] == "1"
    assert tables["mlag_interfaces"] == [{"mlag": "10", "state": "active-full", "local_interface": "Po10", "remote_interface": "Po10", "status": "up/up"}]
    assert [row["interface"] for row in tables["spanning_tree"]] == ["Et1", "Po10", "Po999"]
    assert tables["stp_roots"] == [{"instance": "MST0", "root_priority": "4096", "root_address": "001c.7300.0001", "local_root": True}]
    assert tables["vrrp"] == [{"interface": "Vlan10", "group": "1", "state": "Master", "virtual_ip": "10.10.0.254", "priority": "200"}]
    assert len(tables["lldp"]) == 4 and tables["lldp"][0] == {"local_interface": "Et1", "neighbor_name": "server1", "neighbor_port_id": "eth0"}
    assert tables["vrfs"] == [{"name": "CUST", "protocols": "ipv4,ipv6"}]
    assert len(tables["routes"]) == 4 and len(tables["show ip route vrf CUST"]) == 1
    assert len(tables["arp"]) == 2 and len(tables["show ip arp vrf CUST"]) == 1
    assert snapshot["routing_commands"] == {"bgp": ("show ip bgp summary vrf all", "bgp")}
    assert [(p["scope"], p["neighbor"], p["state"], p["prefixes"]) for p in snapshot["routing"]["bgp"]] == [
        ("default", "10.0.0.2", "Established", "10"), ("CUST", "10.20.0.2", "Established", "3")]
    health = snapshot["health"]
    assert health["cpu_percent"] == {"one_minute": 7.6}
    assert 60 < health["memory_free_percent"] < 70
    assert health["ntp"] == [{"server": "10.0.0.5", "selected": True, "reachable": True, "stratum": 1},
                             {"server": "10.0.0.6", "selected": False, "reachable": True, "stratum": 2}]
    assert health["interface_errors"]["Et1"]["FCSErr"] == 0 and health["alarms"] == []
    assert snapshot["metrics"]["mlag_state"] == "Active" and snapshot["metrics"]["mac_by_port"] == {"Et1": 1, "Po10": 1}
    assert snapshot["diagnostic_commands"] == list(eos_checks.DIAGNOSTICS)


def test_normalized_config_drops_metadata_comments_and_keeps_operator_comments():
    running = transcript()["show running-config"]
    assert "! Command:" in running and "! boot system" in running
    normalized = eos_checks.normalized_config(running)
    assert "! Command" not in normalized and "boot system" not in normalized and "EOS-4.30.5M" not in normalized
    assert "   !! server-facing port" in normalized
    assert normalized.startswith("hostname leaf1") and normalized.endswith("end")


@pytest.mark.parametrize("raw_change,key", [
    (lambda r: r.update({"show mlag": "garbage"}), "mlag"),
    (lambda r: r.update({"show spanning-tree": "MST0\n  Spanning tree enabled protocol mstp\nInterface Role State Cost Prio.Nbr Type\nEt1 weird forwarding 2000 128.1 P2p\n"}), "spanning_tree"),
    (lambda r: r.update({"show mlag interfaces": r["show mlag interfaces"] + "      20 to-x broken Po20 Po20 up/up extra\n"}), "mlag_interfaces"),
    (lambda r: r.update({"show mac address-table": r["show mac address-table"].replace("criterion: 2", "criterion: 3")}), "mac"),
    (lambda r: r.update({"show boot-config": "Current CLI settings:\nConsole speed: (not set)\n"}), "boot"),
    (lambda r: r.update({"show startup-config": "% Startup config not found"}), "startup_config"),
])
def test_unrecognized_critical_output_fails_closed(raw_change, key):
    raw = transcript()
    raw_change(raw)
    assert key in baseline(raw)["errors"]


BRIEF = ("Port Channel Port-Channel10:\n  Active Ports: Ethernet4 Ethernet3\n"
         "Port Channel Port-Channel999:\n  Active Ports: Ethernet47 Ethernet48\n")


def test_port_channels_fall_back_to_brief_where_summary_is_not_a_command():
    # A release without `show port-channel summary` rejects it; `brief` answers.
    raw = transcript()
    raw["show port-channel summary"] = "% Invalid input (at token 2: 'summary')"
    raw["show port-channel brief"] = BRIEF
    snapshot = baseline(raw)
    assert snapshot["errors"] == {}
    assert snapshot["tables"]["port_channels"] == [{"name": "Port-Channel10", "active_ports": ["Ethernet3", "Ethernet4"]},
                                                    {"name": "Port-Channel999", "active_ports": ["Ethernet47", "Ethernet48"]}]
    assert snapshot["raw"]["show port-channel brief"] == BRIEF
    # Neither command answering is not "no port-channels".
    del raw["show port-channel brief"]
    assert "device rejected 'show port-channel brief'" in baseline(raw)["errors"]["port_channels"]
    raw["show port-channel summary"] = raw["show port-channel brief"] = ""
    assert "no port-channel reported but 2 configured" in baseline(raw)["errors"]["port_channels"]
    raw["show port-channel brief"] = "Port Channel Port-Channel10:\n  Inactive Ports: Ethernet3\n"
    assert "unrecognized line 'Inactive Ports: Ethernet3'" in baseline(raw)["errors"]["port_channels"]


def test_no_port_channels_is_an_empty_table_only_when_none_is_configured():
    raw = transcript()
    raw["show port-channel summary"] = raw["show port-channel brief"] = ""
    for command in ("show running-config", "show startup-config"):
        raw[command] = re.sub(r"(?ms)^interface Port-Channel\d+\n(?:   .*\n)*!\n", "", raw[command])
    snapshot = baseline(raw)
    assert snapshot["tables"]["port_channels"] == [] and "port_channels" not in snapshot["errors"]


def test_flash_space_errors_carry_the_numbers(profile, options, fake_device):
    fake_device.raw = dict(fake_device.raw, **{"dir flash:": fake_device.raw["dir flash:"].replace("2500000000 bytes free", "150000000 bytes free")})
    result, reporter = run_device(profile, options)
    assert result.failed and reporter.emit.call_args.args[1] == "blocked"
    assert "insufficient free flash space: 150000000 bytes free, 200000000 required (200000000 reserve)" in reporter.emit.call_args.args[2]
    fake_device.instances.clear()
    fake_device.raw = dict(fake_device.raw, **{"dir flash:": "Directory of flash:/\n\nsomething new\n"})
    result, reporter = run_device(profile, options)
    assert result.failed and "free flash space could not be read" in reporter.emit.call_args.args[2]


def test_optional_tables_become_warnings_not_empty_tables():
    raw = transcript()
    del raw["show lldp neighbors"]
    del raw["show ip arp"]
    snapshot = baseline(raw)
    assert snapshot["errors"] == {}
    assert {"lldp", "arp"} <= set(snapshot["warnings"])
    assert "lldp" not in snapshot["tables"]


def test_disabled_mlag_skips_the_interface_table_and_is_not_a_blocker(profile):
    raw = transcript(mlag_state="Disabled")
    del raw["show mlag interfaces"]
    snapshot = baseline(raw)
    assert snapshot["errors"] == {} and "mlag_interfaces" not in snapshot["tables"]
    assert eos_workflow.preflight(snapshot, profile)["blockers"] == []


def test_vrrp_is_required_only_when_configured():
    raw = transcript()
    del raw["show vrrp"]
    assert "vrrp" in baseline(raw)["errors"]
    raw["show running-config"] = raw["show running-config"].replace("   vrrp 1 ipv4 10.10.0.254\n", "")
    raw["show startup-config"] = raw["show startup-config"].replace("   vrrp 1 ipv4 10.10.0.254\n", "")
    snapshot = baseline(raw)
    assert "vrrp" not in snapshot["errors"] and "vrrp" in snapshot["warnings"]


def test_routing_detection_ignores_non_neighbor_router_blocks_and_blocks_unknown_protocols():
    config = "router bgp 65001\n   neighbor 10.0.0.2 remote-as 65100\n   address-family ipv6\n      neighbor 2001:db8::2 activate\nrouter general\n   router-id ipv4 1.1.1.1\nrouter multicast\n   ipv4\n      routing\nrouter ospf 1\n   router-id 1.1.1.1\nrouter isis CORE\n   net 49.0001.0000.0000.0001.00\n"
    found, unsupported = eos_checks.routing_checks(config)
    assert found == {"bgp": ("show ip bgp summary vrf all", "bgp"), "bgp_ipv6": ("show ipv6 bgp summary vrf all", "bgp"),
                     "ospf": ("show ip ospf neighbor vrf all", "ospf"), "isis": ("show isis neighbors", "isis")}
    assert unsupported == []
    assert eos_checks.routing_checks("router rip\n   network 10.0.0.0/8\n")[1] == ["router rip"]


def test_bgp_summary_parsing_fails_closed_on_incomplete_rows():
    summary = transcript()["show ip bgp summary vrf all"]
    down = summary.replace("01:00:00 Estab   10     10", "never    Active")
    rows = eos_checks.peers(down, "bgp")
    assert rows[0]["state"] == "Active" and rows[0]["prefixes"] is None
    with pytest.raises(ValueError, match="incomplete neighbor row"):
        eos_checks.peers(summary.replace("0    0 01:00:00 Estab   10     10", "0    0"), "bgp")
    with pytest.raises(ValueError, match="unrecognized summary"):
        eos_checks.peers("% BGP inactive", "bgp")
    with pytest.raises(ValueError, match="outside a VRF summary"):
        eos_checks.peers("  10.0.0.2 4 65100 1 1 0 0 01:00:00 Estab 1 1\n", "bgp")


def test_ospf_and_isis_neighbor_parsing():
    ospf = ("Neighbor ID     Instance VRF      Pri State                  Dead Time   Address         Interface\n"
            "192.0.2.2       1        default  1   FULL/DR                00:00:35    10.1.1.2        Vlan100\n"
            "192.0.2.3       1        CUST     1   2WAY/DROTHER           00:00:31    10.2.2.3        Vlan200\n")
    assert eos_checks.peers(ospf, "ospf") == [
        {"scope": "1 default", "neighbor": "192.0.2.2", "state": "FULL/DR", "interface": "Vlan100"},
        {"scope": "1 CUST", "neighbor": "192.0.2.3", "state": "2WAY/DROTHER", "interface": "Vlan200"}]
    assert eos_checks.peers("", "ospf") == []
    with pytest.raises(ValueError, match="incomplete"):
        eos_checks.peers("Neighbor ID  Instance VRF  Pri State\n192.0.2.2  1  default  1  BOGUS  00:00:35  10.1.1.2  Vlan100\n", "ospf")
    isis = ("Instance  VRF      System Id        Type Interface   SNPA              State Hold time   Circuit Id\n"
            "CORE      default  0000.0000.0002   L2   Ethernet1   P2P               UP    28          10\n")
    assert eos_checks.peers(isis, "isis") == [{"instance": "CORE", "vrf": "default", "system_id": "0000.0000.0002", "type": "L2", "interface": "Ethernet1", "state": "UP"}]
    with pytest.raises(ValueError, match="every neighbor row"):
        eos_checks.peers(isis + "CORE default 0000.0000.0003 L2 Ethernet2 P2P UP\n", "isis")


def test_eos_versions_order_numerically_then_by_train():
    assert eos_version("4.30.5M") < eos_version("4.32.1F") < eos_version("4.32.2F")
    assert eos_version("4.28.10.1M") > eos_version("4.28.9M")
    with pytest.raises(ValueError):
        eos_version("17.9.4a")


def test_profile_family_and_release_rules(profile):
    assert profile.family == "eos" and profile.release("4.32.1F") == (4, 32, 1, 0, "F")
    assert replace(profile, image="EOS64-4.32.1F.swi").family == "eos"
    assert Profile.from_mapping({**asdict(profile), "models": [MODEL], "starting_versions": ["4.30.5M"], "image": "EOS64-4.32.1F.swi"}).image == "EOS64-4.32.1F.swi"


@pytest.mark.parametrize("field,value", [
    ("image", "EOS-4.30.5M.swi"), ("image", "cat9k_iosxe.17.12.04.SPA.bin\n"), ("image", "EOS-4.32.1F.swi; reload"),
    ("starting_versions", ["4.32.1F"]), ("starting_versions", ["17.9.4a"]),
    ("models", ["C9300-48P"]), ("models", ["DCS-7050SX3-48YC8; rm"]),
    ("minimum_free_bytes", 50_000_000), ("bundle_conversion_validated", True), ("volume", "HD1.2"),
    ("allow_active", True), ("license_check_date", "2025-01-01"),
    ("image_source", "scp://user:secret@example.com/EOS-4.32.1F.swi"), ("image_source", "http://example.com/other.swi"),
])
def test_profile_rejects_unvalidated_or_foreign_values(profile, tmp_path, field, value):
    data = asdict(profile)
    data["models"], data["starting_versions"] = list(data["models"]), list(data["starting_versions"])
    data[field] = value
    path = tmp_path / "profile.yaml"
    path.write_text(yaml.safe_dump(data))
    with pytest.raises(ValueError):
        Profile.load(path)


@pytest.mark.parametrize("change,expected", [
    (lambda s: s["software"]["1"].update(version="4.29.2F"), "starting version"),
    (lambda s: s["software"]["1"].update(model="DCS-7280SR3-48YC8"), "model is not approved"),
    (lambda s: s["tables"]["mlag"][0].update(state="Inactive"), "MLAG state is Inactive"),
    (lambda s: s["tables"]["mlag"][0].update(peer_link_status="Down"), "peer link"),
    (lambda s: s["tables"]["mlag"][0].update(peer_config="inconsistent"), "not consistent"),
    (lambda s: s["errors"].update(routes="unreadable"), "routes: unreadable"),
])
def test_preflight_gates(profile, change, expected):
    snapshot = baseline()
    change(snapshot)
    assert expected in " ".join(eos_workflow.preflight(snapshot, profile)["blockers"])


def test_already_current_requires_boot_config_to_point_at_the_target(profile):
    plan = eos_workflow.preflight(baseline(transcript("4.32.1F", "EOS-4.32.1F.swi")), profile)
    assert plan["already_current"] and not plan["upgrade_needed"] and not plan["blockers"]
    plan = eos_workflow.preflight(baseline(transcript("4.32.1F", "EOS-4.30.5M.swi")), profile)
    assert not plan["already_current"]
    assert any("boot-config points at EOS-4.30.5M.swi" in reason for reason in plan["blockers"])


def test_unsaved_configuration_is_recorded_then_blocks_after_a_save(profile):
    snapshot = baseline()
    snapshot["config"] = snapshot["config"].replace("switchport access vlan 10", "switchport access vlan 20")
    plan = eos_workflow.preflight(snapshot, profile)
    assert plan["unsaved_changes"] and not plan["blockers"] and "+   switchport access vlan 20" in plan["saved_config_diff"]
    plan = eos_workflow.preflight(snapshot, profile, saved=True)
    assert any("still differ after write memory" in reason for reason in plan["blockers"])
    assert eos_workflow.preflight(snapshot, profile, saved=True, allow_mismatch=True)["config_mismatch_overridden"]


@pytest.fixture
def options(tmp_path):
    return SimpleNamespace(apply=False, lock_dir=tmp_path / "locks", show_timeout=60, install_timeout=30,
                           reload_timeout=3, settle_seconds=0, validation_timeout=1, poll_interval=0)


@pytest.fixture
def fake_device(monkeypatch, profile):
    class FakeDevice:
        instances = []
        raw = transcript()
        digest = "a" * 32
        fail_reload = False
        image_exists = True
        lose_mlag = False

        def __init__(self, task, options, emit):
            self.options, self.emit = options, emit
            self.raw = dict(self.raw)
            self.connection = Mock()
            self.mutations = []
            self.instances.append(self)

        def connect(self):
            pass

        def close(self):
            pass

        def read(self, command):
            if command == f"dir flash:{profile.image}":
                if self.image_exists:
                    return f"Directory of flash:/{profile.image}\n\n       -rwx   1150000000           Sep 11  2026  {profile.image}\n\n3959422976 bytes total (2500000000 bytes free)\n"
                return f"Directory of flash:/{profile.image}\n\nNo files\n\n3959422976 bytes total (2500000000 bytes free)\n"
            return self.raw.get(command, "% Invalid input")

        def write(self, command, timeout=120):
            if command.startswith("verify /md5"):
                return f"verify /md5 (flash:{profile.image}) = {self.digest}"
            self.mutations.append(command)
            if command == "write memory":
                self.raw["show startup-config"] = self.raw["show running-config"].replace(
                    "! Command: show running-config", "! Command: show startup-config\n! Startup-config last modified at Thu Sep 11 20:00:00 2026 by admin")
            return "Copy completed successfully."

        def interactive(self, command, timeout, reload=False):
            self.mutations.append(command)
            if command.startswith("copy "):
                self.image_exists = True
            if reload and self.fail_reload:
                raise ValueError("injected reload failure")

        def wait_for_target(self, profile):
            self.raw = transcript(profile.target_version, profile.image)
            if self.lose_mlag:
                self.raw["show mlag"] = self.raw["show mlag"].replace("Active-full                        :                  1",
                                                                      "Active-full                        :                  0")
                self.raw["show mlag interfaces"] = ""

    monkeypatch.setattr(eos_workflow, "EosDevice", FakeDevice)
    return FakeDevice


def run_device(profile, options, platform="arista_eos"):
    host = SimpleNamespace(name="leaf1", hostname="192.0.2.10", port=22, platform=platform, data={})
    reporter = Mock()
    reporter.emit.return_value = True
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    return result, reporter


def test_dry_run_never_mutates(profile, options, fake_device):
    result, reporter = run_device(profile, options)
    assert not result.failed
    device = fake_device.instances[0]
    assert device.mutations == []
    device.connection.send_config_set.assert_not_called()
    assert reporter.emit.call_args.args[1] == "dry_run_complete"
    plan = reporter.emit.call_args.args[3]["upgrade_plan"]
    assert plan["commands"] == ["write memory", INSTALL]
    assert plan["image_verification"] == "verified" and plan["boot_image"] == "EOS-4.30.5M.swi"


def test_apply_installs_the_target_and_validates(profile, options, fake_device):
    options.apply = True
    result, reporter = run_device(profile, options)
    assert not result.failed and result.changed
    device = fake_device.instances[0]
    assert device.mutations == ["write memory", INSTALL]
    # Install mode owns boot-config: the driver never writes `boot system` itself.
    device.connection.send_config_set.assert_not_called()
    stages = [call.args[1] for call in reporter.emit.call_args_list]
    assert "configuring_boot" not in stages and stages.index("ready") < stages.index("installing") < stages.index("validating")
    assert stages.count("validating") == 2 and stages[-1] == "completed"
    assert result.result["status"] == "completed" and result.result["findings"] == []


def test_wrong_platform_is_refused_before_connecting(profile, options, fake_device):
    result, reporter = run_device(profile, options, platform="cisco_ios")
    assert result.failed and reporter.emit.call_args.args[1] == "failed"
    assert "arista_eos" in reporter.emit.call_args.args[2]


def test_missing_image_blocks_without_a_source_and_is_copied_with_one(profile, options, fake_device):
    fake_device.image_exists = False
    result, reporter = run_device(profile, options)
    assert result.failed and reporter.emit.call_args.args[1] == "blocked"
    assert "image_source is not configured" in reporter.emit.call_args.args[2]
    sourced = replace(profile, image_source=f"http://images.example.com/{profile.image}")
    fake_device.instances.clear()
    result, reporter = run_device(sourced, options)
    assert not result.failed and reporter.emit.call_args.args[1] == "dry_run_complete"
    plan = reporter.emit.call_args.args[3]["upgrade_plan"]
    assert plan["image_verification"] == "pending_transfer" and plan["staging_command"] == f"copy {sourced.image_source} flash:{profile.image}"
    assert fake_device.instances[0].mutations == []
    options.apply = True
    fake_device.instances.clear()
    fake_device.image_exists = False
    result, reporter = run_device(sourced, options)
    assert not result.failed
    assert fake_device.instances[0].mutations == ["write memory", f"copy {sourced.image_source} flash:{profile.image}", INSTALL]


def test_stage_only_copies_and_verifies_without_boot_or_reload(profile, options, fake_device):
    options.apply, options.stage_only = True, True
    fake_device.image_exists = False
    sourced = replace(profile, image_source=f"http://images.example.com/{profile.image}")
    result, reporter = run_device(sourced, options)
    assert not result.failed and result.changed
    assert fake_device.instances[0].mutations == [f"copy {sourced.image_source} flash:{profile.image}"]
    assert reporter.emit.call_args.args[1] == "staged"
    fake_device.instances[0].connection.send_config_set.assert_not_called()


def test_bad_checksum_blocks_all_writes(profile, options, fake_device):
    options.apply = True
    fake_device.digest = "b" * 32
    result, reporter = run_device(profile, options)
    assert result.failed and not result.changed
    assert fake_device.instances[0].mutations == ["write memory"]
    assert reporter.emit.call_args.args[1] == "blocked"


def test_install_failure_requires_recovery(profile, options, fake_device):
    options.apply = True
    fake_device.fail_reload = True
    result, reporter = run_device(profile, options)
    assert result.failed and result.changed
    assert reporter.emit.call_args.args[1] == "recovery_required"


def test_lost_mlag_port_fails_validation_with_a_reason(profile, options, fake_device):
    options.apply = True
    fake_device.lose_mlag = True
    result, reporter = run_device(profile, options)
    assert result.failed and result.changed
    final = reporter.emit.call_args
    assert final.args[1] == "validation_failed"
    assert "mlag_interfaces: 1->0 rows" in final.args[2] and "mlag: 1->1 rows" in final.args[2]


def test_webhook_failure_at_ready_gate_stops_before_boot_changes(profile, options, fake_device):
    options.apply = True
    host = SimpleNamespace(name="leaf1", hostname="192.0.2.10", port=22, platform="arista_eos", data={})
    reporter = Mock()
    reporter.emit.side_effect = lambda h, stage, *args, **kwargs: stage != "ready"
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    assert result.failed and not result.changed
    assert fake_device.instances[0].mutations == ["write memory"]


def test_install_dialogue_answers_confirmation_and_accepts_the_dropped_session():
    device = eos_workflow.EosDevice(SimpleNamespace(), SimpleNamespace(show_timeout=60), Mock())
    conn = device.connection = Mock()
    conn.find_prompt.return_value = "leaf1#"
    conn.read_channel.side_effect = [INSTALL_OUTPUT + "Proceed with reload? [confirm]",
                                     "\nBroadcast message from root@leaf1\n\nThe system is going down for reboot NOW!\n", ""]
    conn.is_alive.side_effect = [True, False, False]
    transcript_text = device.interactive(INSTALL, 5, reload=True)
    assert [call.args[0] for call in conn.write_channel.call_args_list] == [INSTALL + "\n", "\n"]
    assert "going down for reboot" in transcript_text


def test_install_dialogue_refuses_a_save_prompt_a_silent_return_to_prompt_and_a_device_error():
    device = eos_workflow.EosDevice(SimpleNamespace(), SimpleNamespace(show_timeout=60), Mock())
    conn = device.connection = Mock()
    conn.find_prompt.return_value = "leaf1#"
    conn.read_channel.return_value = "System configuration has been modified. Save? [yes/no/cancel/diff]:"
    conn.is_alive.return_value = True
    with pytest.raises(ValueError, match="unrecognized interactive prompt"):
        device.interactive(INSTALL, 5, reload=True)
    assert conn.write_channel.call_count == 1
    # boot-config written but the reload never started: not a success.
    conn.read_channel.return_value = INSTALL + "\n" + INSTALL_OUTPUT.replace("Reloading this supervisor...\n", "") + "leaf1#"
    with pytest.raises(ValueError, match="without success evidence"):
        device.interactive(INSTALL, 5, reload=True)
    conn.read_channel.return_value = INSTALL + "\n% Error: flash:/EOS-4.32.1F.swi is not a valid EOS image\nleaf1#"
    with pytest.raises(ValueError, match="device rejected"):
        device.interactive(INSTALL, 5, reload=True)


def test_copy_dialogue_treats_device_errors_as_failure():
    device = eos_workflow.EosDevice(SimpleNamespace(), SimpleNamespace(show_timeout=60), Mock())
    conn = device.connection = Mock()
    conn.find_prompt.return_value = "leaf1#"
    conn.read_channel.return_value = "% Error copying http://images.example.com/EOS-4.32.1F.swi to flash: (Connection refused)\nleaf1#"
    conn.is_alive.return_value = True
    with pytest.raises(ValueError, match="rejected"):
        device.interactive("copy http://images.example.com/EOS-4.32.1F.swi flash:EOS-4.32.1F.swi", 5)


def test_wait_for_target_polls_until_the_target_release_answers(profile, monkeypatch):
    monkeypatch.setattr("netops.upgrade.eos_workflow.time.sleep", lambda _: None)
    emit = Mock()
    device = eos_workflow.EosDevice(SimpleNamespace(), SimpleNamespace(reload_timeout=5, poll_interval=0, show_timeout=60, config_timeout=300), emit)
    outputs = iter([OSError("refused"), transcript()["show version"], transcript("4.32.1F")["show version"]])

    def read(command):
        value = next(outputs)
        if isinstance(value, Exception):
            raise value
        return value
    device.connect, device.close, device.read = lambda: None, lambda: None, read
    device.wait_for_target(profile)
    assert [call.args[0] for call in emit.call_args_list].count("reconnecting") == 3


def test_target_findings_require_target_boot_and_saved_config(profile):
    before = baseline()
    after = baseline(transcript("4.32.1F", "EOS-4.32.1F.swi"))
    assert eos_workflow.target_findings(after, profile, before) == []
    wrong_boot = baseline(transcript("4.32.1F", "EOS-4.30.5M.swi"))
    assert [f["check"] for f in eos_workflow.target_findings(wrong_boot, profile, before)] == ["boot"]
    stale = baseline(transcript("4.30.5M", "EOS-4.32.1F.swi"))
    assert [f["check"] for f in eos_workflow.target_findings(stale, profile, before)] == ["software"]
    after["startup_config"] += "\nusername late"
    assert [f["check"] for f in eos_workflow.target_findings(after, profile, before)] == ["saved_config"]
    assert eos_workflow.target_findings(after, profile, before, saved=False) == []
    raw = transcript("4.32.1F", "EOS-4.32.1F.swi")
    raw["show reload cause"] = "Reload Cause 1:\n-------------------\nKernel panic\n"
    assert [(f["check"], f["severity"]) for f in eos_workflow.target_findings(baseline(raw), profile, before)] == [("reload_cause", "warning")]


def test_comparison_report_shows_the_boot_image_and_eos_diagnostics(profile):
    before = baseline()
    after = baseline(transcript("4.32.1F", "EOS-4.32.1F.swi"))
    text = report.build(SimpleNamespace(name="leaf1", hostname="192.0.2.10"), before, after, checks.compare(before, after), {"profile": "lab-eos", "target_version": "4.32.1F"}, "completed")
    assert "Boot settings: before `EOS-4.30.5M.swi`; after `EOS-4.32.1F.swi`." in text
    assert "| 1 | DCS-7050SX3-48YC8 | 4.30.5M | 4.32.1F | x86_64 | x86_64 | standalone | standalone |" in text
    assert "`show boot-config`: 1 line(s) only before, 1 only after" in text
    assert "show reload cause (changes with every reload, see Findings)" in text
    assert "show processes cpu sorted" not in text


def test_scheduler_and_cli_recognize_swi_profiles(profile):
    assert scheduler.platform_for({"image": "EOS-4.32.1F.swi"}) == "arista_eos"
    assert scheduler.platform_for({"image": "BIGIP-17.5.1.8-0.0.19.iso"}) == "f5_tmsh"
    assert scheduler.platform_for({"image": "cat9k_iosxe.17.12.04.SPA.bin"}) == "cisco_ios"
    job = {"id": 1, "device_id": 2, "claim_token": "3fa85f64-5717-4562-b3fc-2c963f66afa6", "hostname": "192.0.2.10",
           "operation": "audit", "device": "leaf1", "profile": {**asdict(profile), "models": [MODEL], "starting_versions": ["4.30.5M"]}}
    assert scheduler.validate_assignment(job, apply=False).family == "eos"
