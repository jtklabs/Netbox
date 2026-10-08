"""Inventory identity and hardware history across onboarding and daily sweeps."""

from copy import deepcopy

import pytest

from conftest import collect_fixture
from snmpinv.model import DeviceRecord, ModuleRecord, ScanResult, build_scan_result
from snmpinv.netbox import NetBoxError
from snmpinv.onboarding import _find_created_device
from snmpinv.sync import REPLACEMENT_ENDPOINT, SyncOptions, Syncer
from test_contexts import FakeNetBox, chassis_in

ISSUES = "/plugins/discovery/issues/"


def scanner(box):
    return Syncer(box, SyncOptions(sync_interfaces=False, sync_ips=False,
                                  manage_software_version=False, sync_cables=False,
                                  sync_fhrp_groups=False))


def record(serial="CHASSIS-1", name="core", modules=()):
    return DeviceRecord(name=name, serial=serial, model="N7K-C7010",
                        manufacturer="Cisco", modules=list(modules))


def sync(box, item, **kwargs):
    scanner(box).sync(ScanResult(host="192.0.2.1", devices=[item]), 1, **kwargs)


def history(box):
    return box.objects.get(REPLACEMENT_ENDPOINT, [])


def test_rescan_updates_same_device_and_records_one_change():
    box = FakeNetBox()
    sync(box, record())
    before = deepcopy(box.device("core"))
    for _ in range(3):
        sync(box, record("CHASSIS-2"), device_id=before["id"])
    assert len(box.devices()) == 1
    assert box.device("core")["id"] == before["id"]
    assert box.device("core")["serial"] == "CHASSIS-2"
    assert box.device("core")["status"] == before["status"]
    assert [(r["old_serial"], r["new_serial"]) for r in history(box)] == [("CHASSIS-1", "CHASSIS-2")]
    assert history(box)[0]["replaced_device"] is None


@pytest.mark.parametrize("serial", ["", "unknown", "N/A", "chassis-1"])
def test_empty_placeholder_or_case_change_is_not_replacement(serial):
    box = FakeNetBox()
    sync(box, record())
    sync(box, record(serial))
    assert not history(box)
    assert box.device("core")["serial"].upper() == "CHASSIS-1"


def test_onboarding_establishes_baseline_and_next_rescan_records_change():
    box = FakeNetBox()
    sync(box, record(""))
    sync(box, record("REVIEWED"), record_hardware_changes=False)
    assert not history(box)
    sync(box, record("NEW"))
    assert len(history(box)) == 1


def test_duplicate_serial_with_same_name_at_other_site_never_moves_that_device():
    box = FakeNetBox()
    other = chassis_in(box, name="core", serial="SHARED")
    other["site"] = {"id": 2}
    before = deepcopy(other)
    sync(box, record("SHARED"))
    assert other == before
    assert len(box.devices()) == 2
    assert len(box.objects[ISSUES]) == 1


def test_address_wins_over_other_devices_serial_and_name():
    box = FakeNetBox()
    other = chassis_in(box, name="core", serial="SHARED", address="192.0.2.2")
    mine = chassis_in(box, name="renamed", serial="OLD", address="192.0.2.1")
    before = deepcopy(other)
    sync(box, record("SHARED"), scanned_address="192.0.2.1")
    assert other == before
    assert mine["serial"] == "SHARED"
    assert len(history(box)) == 1


def test_target_id_wins_over_duplicate_serial_and_hostname():
    box = FakeNetBox()
    other = chassis_in(box, name="core", serial="SHARED")
    mine = chassis_in(box, name="mine", serial="OLD", address="192.0.2.1")
    before = deepcopy(other)
    for _ in range(3):
        sync(box, record("SHARED"), device_id=mine["id"], scanned_address="192.0.2.1")
    assert other == before
    assert mine["serial"] == "SHARED"
    assert len(box.objects[ISSUES]) == 1
    assert box.objects[ISSUES][0]["last_seen_at"]


def test_ambiguous_name_fails_without_overwriting_either_device():
    box = FakeNetBox()
    chassis_in(box, name="core", serial="ONE")
    chassis_in(box, name="core", serial="TWO", address="192.0.2.2")
    before = deepcopy(box.devices())
    with pytest.raises(NetBoxError, match="Ambiguous"):
        sync(box, record())
    assert box.devices() == before


def module(serial, bay="Slot 1"):
    return ModuleRecord(bay_name=bay, model="N7K-M148GT", serial=serial)


@pytest.mark.parametrize("bay", ["Slot 1", "prefix-" + "x" * 80])
def test_duplicate_bay_labels_never_alternate_serials(bay):
    box = FakeNetBox()
    sync(box, record(modules=[module("CARD-1", bay)]))
    before = deepcopy(box.objects["/dcim/modules/"])
    for _ in range(3):
        sync(box, record(modules=[module("CARD-1", bay), module("CARD-2", bay)]))
    assert box.objects["/dcim/modules/"] == before
    assert not history(box)


def test_real_module_swap_is_recorded_once_and_missing_serial_does_not_erase_it():
    box = FakeNetBox()
    sync(box, record(modules=[module("CARD-1")]))
    for serial in ("CARD-2", "CARD-2", "unknown", "", "card-2"):
        sync(box, record(modules=[module(serial)]))
    assert len(history(box)) == 1
    assert history(box)[0]["old_serial"] == "CARD-1"
    assert history(box)[0]["module_bay"] == "Slot 1"
    assert box.objects["/dcim/modules/"][0]["serial"].upper() == "CARD-2"


def test_different_long_bay_names_that_truncate_identically_are_ambiguous():
    box = FakeNetBox()
    sync(box, record(modules=[module("ONE", "left-" + "x" * 80),
                              module("TWO", "right-" + "x" * 80)]))
    assert not box.objects.get("/dcim/modules/")
    assert not history(box)


def test_duplicate_ip_owners_fail_instead_of_using_first():
    box = FakeNetBox()
    chassis_in(box, name="one", address="192.0.2.1")
    chassis_in(box, name="two", address="192.0.2.1")
    before = deepcopy(box.devices())
    with pytest.raises(NetBoxError, match="Ambiguous"):
        sync(box, record(), scanned_address="192.0.2.1")
    assert box.devices() == before


def test_history_failure_does_not_overwrite_serial(monkeypatch):
    box = FakeNetBox()
    sync(box, record())
    original = box.create

    def fail(path, payload, label=""):
        if path == REPLACEMENT_ENDPOINT:
            raise NetBoxError("audit unavailable")
        return original(path, payload, label)

    monkeypatch.setattr(box, "create", fail)
    with pytest.raises(NetBoxError, match="audit unavailable"):
        sync(box, record("NEW"))
    assert box.device("core")["serial"] == "CHASSIS-1"


def test_patch_retry_reuses_history_but_later_reverse_swaps_are_recorded(monkeypatch):
    box = FakeNetBox()
    sync(box, record())
    original = box.update

    def fail(path, object_id, payload, label=""):
        if path == "/dcim/devices/":
            raise NetBoxError("patch failed")
        return original(path, object_id, payload, label)

    monkeypatch.setattr(box, "update", fail)
    with pytest.raises(NetBoxError):
        sync(box, record("NEW"))
    monkeypatch.setattr(box, "update", original)
    sync(box, record("NEW"))
    assert len(history(box)) == 1
    sync(box, record("CHASSIS-1"))
    sync(box, record("NEW"))
    assert len(history(box)) == 3


def test_stack_member_missing_serial_does_not_borrow_masters_scalar():
    facts = collect_fixture("cisco-c9300-stack")
    for entity in facts.chassis_entities():
        entity.serial = ""
    facts.vendor_serial = "MASTER-ONLY"
    result = build_scan_result(facts)
    assert len(result.devices) > 1
    assert all(not d.serial for d in result.devices)


def test_duplicate_stack_serials_match_positions_not_first_serial():
    box = FakeNetBox()
    vc = box.add("/dcim/virtual-chassis/", name="stack")
    first = chassis_in(box, name="stack", serial="SHARED")
    second = chassis_in(box, name="stack-2", serial="SHARED", address="192.0.2.2")
    for position, device in enumerate((first, second), 1):
        device.update(virtual_chassis={"id": vc["id"]}, vc_position=position)
    item = record("SHARED", "renamed-member")
    item.vc_position = 2
    assert scanner(box)._find_device(item, 1, virtual_chassis=vc)["id"] == second["id"]


def test_failed_onboarding_cannot_report_an_unrelated_device_by_serial():
    box = FakeNetBox()
    chassis_in(box, name="someone-else", serial="SHARED")
    result = ScanResult(host="192.0.2.1", devices=[record("SHARED")])
    assert _find_created_device(box, result, 1) is None


def test_targeted_stack_uses_its_existing_chassis_after_hostname_changes():
    box = FakeNetBox()
    vc = box.add("/dcim/virtual-chassis/", name="original-stack")
    other_vc = box.add("/dcim/virtual-chassis/", name="new-stack")
    target = chassis_in(box)
    target["virtual_chassis"] = {"id": vc["id"]}
    worker = scanner(box)
    worker._target_device_id = target["id"]
    result = ScanResult(host="192.0.2.1", devices=[record()], virtual_chassis_name=other_vc["name"])
    assert worker._ensure_virtual_chassis(result, "192.0.2.1")["id"] == vc["id"]
