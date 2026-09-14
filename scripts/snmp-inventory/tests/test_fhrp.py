"""HSRP/VRRP groups: read from the MIBs, named by their virtual address, written as NetBox FHRP groups."""

from conftest import ReplayCollector, fixture_path
from snmpinv.model import build_scan_result
from snmpinv.sync import SyncOptions, Syncer


def test_hsrp_and_vrrp_groups_are_collected_with_interfaces_and_peers():
    facts = ReplayCollector(fixture_path("cisco-hsrp-vrrp")).collect("192.0.2.10")
    by_protocol = {g.protocol: g for g in facts.fhrp_groups}
    hsrp, vrrp = by_protocol["hsrp"], by_protocol["vrrp"]
    assert (hsrp.if_index, hsrp.group_id, hsrp.virtual_ip, hsrp.priority, hsrp.state) == (10, 1, "10.1.10.1", 110, "active")
    assert hsrp.peer_address == "10.1.10.3"       # active: the standby router is the peer
    assert (vrrp.if_index, vrrp.group_id, vrrp.virtual_ip, vrrp.priority, vrrp.state) == (11, 5, "10.1.20.1", 90, "backup")
    assert vrrp.peer_address == "10.1.20.2"
    records = build_scan_result(facts).fhrp_groups
    assert {(r.protocol, r.interface) for r in records} == {("hsrp", "Vlan10"), ("vrrp", "Vlan20")}


class FakeNetBox:
    def __init__(self):
        self.objects = {"/ipam/fhrp-groups/": [], "/ipam/fhrp-group-assignments/": [],
                        "/dcim/interfaces/": [{"id": 7, "device_id": 1, "name": "Vlan10"}]}
        self.created, self.posted = [], []

    def endpoint_available(self, path):
        return True

    def first(self, path, params):
        for item in self.objects.get(path, []):
            if all(str(item.get(k)) == str(v) for k, v in params.items()):
                return item
        return None

    def ensure(self, path, lookup, payload, label=""):
        existing = self.first(path, lookup)
        return existing or self.create(path, payload, label)

    def create(self, path, payload, label=""):
        item = dict(payload, id=len(self.objects.setdefault(path, [])) + 100)
        for key in ("group_id", "interface_id", "protocol", "name", "interface_type"):
            if key in payload:
                item[key] = payload[key]
        if "group" in payload:
            item["group_id"] = payload["group"]
        self.objects[path].append(item)
        self.created.append((path, payload))
        return item

    def ensure_fields(self, path, existing, desired, label=""):
        existing.update({k: v for k, v in desired.items() if v not in (None, "")})
        return existing

    def post_raw(self, path, payload, label=""):
        self.posted.append(path)
        return {"groups": 1, "dependencies": 0, "stale_groups": 0, "stale_dependencies": 0}


def test_sync_writes_one_fhrp_group_per_virtual_address_and_is_idempotent():
    facts = ReplayCollector(fixture_path("cisco-hsrp-vrrp")).collect("192.0.2.10")
    result = build_scan_result(facts)
    netbox = FakeNetBox()
    syncer = Syncer(netbox, SyncOptions())
    record = result.devices[0]
    created = [(record, {"id": 1, "name": "core-a"})]
    syncer._sync_fhrp_groups(result, created)
    groups = netbox.objects["/ipam/fhrp-groups/"]
    assert [(g["protocol"], g["group_id"], g["name"]) for g in groups] == [("hsrp", 1, "HSRP 1 10.1.10.1")]
    assignments = netbox.objects["/ipam/fhrp-group-assignments/"]
    assert len(assignments) == 1 and assignments[0]["priority"] == 110 and assignments[0]["interface_id"] == 7
    syncer._sync_fhrp_groups(result, created)
    assert len(netbox.objects["/ipam/fhrp-groups/"]) == 1 and len(assignments) == 1
    syncer.refresh_upgrade_groups()
    assert netbox.posted == ["/plugins/discovery/upgrade-groups/refresh/"]
