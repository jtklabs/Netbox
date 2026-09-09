"""Synthetic vendor replies covering release selection and missing serials."""

from unittest.mock import Mock

import pytest

from conftest import ReplayCollector
from snmpinv import vendors
from snmpinv.model import DeviceRecord, build_scan_result
from snmpinv.sync import Syncer


def collect(tmp_path, lines):
    path = tmp_path / "vendor.walk"
    path.write_text("\n".join(lines) + "\n")
    return build_scan_result(ReplayCollector(str(path)).collect("192.0.2.10"))


def force10_lines():
    # Deliberately includes both OS and application versions, and an ENTITY
    # chassis reporting NA. No values here are captures from production gear.
    return [
        '.1.3.6.1.2.1.1.1.0 = STRING: "Dell Force10 OS Operating System Version: 2.0 '
        'Application Software Version: 9.14(2.21) Series: S4810"',
        '.1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.6027.1.3.12',
        '.1.3.6.1.2.1.1.5.0 = STRING: "force10-test"',
        '.1.3.6.1.2.1.47.1.1.1.1.5.1 = INTEGER: 3',
        '.1.3.6.1.2.1.47.1.1.1.1.11.1 = STRING: "NA"',
        '.1.3.6.1.2.1.47.1.1.1.1.13.1 = STRING: "S4810"',
    ]


def test_force10_reads_application_release_and_vendor_serial(tmp_path):
    result = collect(tmp_path, force10_lines() + [
        f'.{vendors.FORCE10_CHASSIS_SERIAL} = STRING: "N/A"',
        # Stack indexes are not assumed to start at 1.
        f'.{vendors.FORCE10_UNIT_SERIAL[:-2]}.7 = STRING: "F10-TEST-123"',
    ])
    assert result.primary.serial == "F10-TEST-123"
    assert result.primary.software_version == "9.14(2.21)"
    assert result.primary.platform == "Dell FTOS"


def test_force10_running_code_oid_wins_over_description(tmp_path):
    result = collect(tmp_path, force10_lines() + [
        f'.{vendors.FORCE10_UNIT_VERSION[:-2]}.0 = STRING: "9.14(2.22)"',
        # The image in flash need not be the image currently running.
        f'.{vendors.FORCE10_UNIT_ENTRY}.11.0 = STRING: "9.14(2.23)"',
        f'.{vendors.FORCE10_CHASSIS_SERIAL} = STRING: "F10-CHASSIS-123"',
    ])
    assert result.primary.software_version == "9.14(2.22)"
    assert result.primary.serial == "F10-CHASSIS-123"


def test_force10_does_not_reuse_one_stack_units_serial(tmp_path):
    result = collect(tmp_path, force10_lines() + [
        f'.{vendors.FORCE10_UNIT_SERIAL[:-2]}.0 = STRING: "UNIT-A"',
        f'.{vendors.FORCE10_UNIT_SERIAL[:-2]}.1 = STRING: "UNIT-B"',
    ])
    assert result.primary.serial == ""


def test_force10_os_version_alone_is_not_a_switch_release(tmp_path):
    lines = force10_lines()
    lines[0] = '.1.3.6.1.2.1.1.1.0 = STRING: "Dell Force10 OS Operating System Version: 2.0"'
    assert collect(tmp_path, lines).primary.software_version == ""


def test_force10_real_entity_serial_still_wins(tmp_path):
    lines = [line.replace('STRING: "NA"', 'STRING: "ENTITY-123"')
             for line in force10_lines()]
    result = collect(tmp_path, lines + [
        f'.{vendors.FORCE10_CHASSIS_SERIAL} = STRING: "VENDOR-123"',
    ])
    assert result.primary.serial == "ENTITY-123"


@pytest.mark.parametrize("release", ["8.3.12.1", "9.14(2.21)", "E_MAIN4.9.4.0.0"])
def test_force10_sysdescr_fallback_without_entity_or_vendor_tables(tmp_path, release):
    lines = [line.replace("9.14(2.21)", release) for line in force10_lines()[:3]]
    device = collect(tmp_path, lines).primary
    assert device.model == "S4810"
    assert device.software_version == release
    assert device.serial == ""


@pytest.mark.parametrize("model", ["C2000V", "C3000V"])
@pytest.mark.parametrize("serial", [None, "", "NA", "N/A", "None", "VM-REPORTED-123"])
def test_clearpass_virtual_appliance_keeps_only_reported_serial(tmp_path, model, serial):
    lines = [
        f'.1.3.6.1.2.1.1.1.0 = STRING: "ClearPass Policy Manager 6.11.5.253053, Model: {model}"',
        '.1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.14823.1.6.1',
        '.1.3.6.1.2.1.1.5.0 = STRING: "clearpass-test"',
        f'.{vendors.CPPM_SYSTEM_MODEL[:-2]}.7 = STRING: "{model}"',
        f'.{vendors.CPPM_SYSTEM_VERSION[:-2]}.7 = STRING: "6.11.5.253053"',
    ]
    if serial is not None:
        lines.append(f'.{vendors.CPPM_SYSTEM_SERIAL[:-2]}.7 = STRING: "{serial}"')
    device = collect(tmp_path, lines).primary
    assert device.model == model
    assert device.platform == "Aruba ClearPass"
    assert device.software_version == "6.11.5.253053"
    assert device.serial == ("VM-REPORTED-123" if serial == "VM-REPORTED-123" else "")


@pytest.mark.parametrize("old_serial", ["NA", "N/A", " None "])
def test_correcting_placeholder_does_not_retire_existing_hardware(old_serial):
    netbox = Mock()
    syncer = Syncer(netbox)
    assert syncer._handle_serial_change(
        {"id": 1, "serial": old_serial},
        DeviceRecord(name="force10-test", serial="F10-REAL-123"), 1,
    ) is None
    netbox.update.assert_not_called()
    netbox.create.assert_not_called()
