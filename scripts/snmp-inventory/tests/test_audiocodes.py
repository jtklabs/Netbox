"""Synthetic AudioCodes replies, including the reported M800C mfg-column issue."""

import pytest

from conftest import ReplayCollector
from snmpinv import vendors
from snmpinv.model import build_scan_result


def scan(tmp_path, *, manufacturer="M800C", model="", descr="AudioCodes SBC",
         enterprise=5003, scalars=(), chassis=True):
    lines = [
        f'.1.3.6.1.2.1.1.1.0 = STRING: "{descr}"',
        f'.1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.{enterprise}.1',
        '.1.3.6.1.2.1.1.5.0 = STRING: "sbc-test"',
    ]
    if chassis:
        lines += [
            '.1.3.6.1.2.1.47.1.1.1.1.5.1 = INTEGER: 3',
            '.1.3.6.1.2.1.47.1.1.1.1.11.1 = STRING: "ENTITY-SERIAL"',
            f'.1.3.6.1.2.1.47.1.1.1.1.12.1 = STRING: "{manufacturer}"',
            f'.1.3.6.1.2.1.47.1.1.1.1.13.1 = STRING: "{model}"',
        ]
    lines += scalars
    path = tmp_path / "audiocodes.walk"
    path.write_text("\n".join(lines) + "\n")
    facts = ReplayCollector(str(path)).collect("192.0.2.10")
    return build_scan_result(facts)


def test_m800c_in_manufacturer_is_recovered_without_losing_raw_observation(tmp_path):
    result = scan(tmp_path)
    assert result.primary.manufacturer == "AudioCodes"
    assert result.primary.model == "M800C"
    assert result.primary.platform == "AudioCodes"
    assert result.primary.serial == "ENTITY-SERIAL"
    assert result.facts.chassis_entities()[0].mfg_name == "M800C"


def test_real_entity_model_wins_over_misplaced_model(tmp_path):
    result = scan(tmp_path, model="M800C-REPORTED")
    assert result.primary.model == "M800C-REPORTED"
    assert result.primary.manufacturer == "AudioCodes"


def test_vendor_identity_and_running_release_without_entity_mib(tmp_path):
    profile = vendors.PROFILES[5003]
    result = scan(tmp_path, chassis=False, descr="AudioCodes SBC Version 7.20", scalars=[
        f'.{profile.model_oids[0]} = STRING: "M800C"',
        f'.{profile.serial_oids[0]} = STRING: "AC-TEST-123"',
        f'.{profile.serial_oids[1]} = Gauge32: 123',
        f'.{profile.version_oids[0]} = STRING: "7.40A.600.001"',
    ])
    assert result.primary.model == "M800C"
    assert result.primary.manufacturer == "AudioCodes"
    assert result.primary.platform == "AudioCodes"
    assert result.primary.serial == "AC-TEST-123"
    assert result.primary.software_version == "7.40A.600.001"


def test_numeric_serial_fallback(tmp_path):
    profile = vendors.PROFILES[5003]
    result = scan(tmp_path, chassis=False, scalars=[
        f'.{profile.serial_oids[1]} = Gauge32: 123456',
    ])
    assert result.primary.serial == "123456"


def test_vendor_model_wins_over_misplaced_model(tmp_path):
    profile = vendors.PROFILES[5003]
    result = scan(tmp_path, scalars=[
        f'.{profile.model_oids[0]} = STRING: "M800C-REPORTED"',
        f'.{profile.serial_oids[0]} = STRING: "VENDOR-SERIAL"',
    ])
    assert result.primary.model == "M800C-REPORTED"
    assert result.primary.serial == "ENTITY-SERIAL"


@pytest.mark.parametrize("manufacturer", ["AudioCodes Ltd.", "Audio Codes Ltd."])
def test_canonical_manufacturer_and_sysdescr_fallback(tmp_path, manufacturer):
    result = scan(tmp_path, manufacturer=manufacturer, descr="AudioCodes M800C SBC Version 7.40A.600.001")
    assert result.primary.model == "M800C"
    assert result.primary.manufacturer == "AudioCodes"
    assert result.primary.software_version == "7.40A.600.001"


def test_unknown_audio_codes_model_stays_missing(tmp_path):
    result = scan(tmp_path, manufacturer="AudioCodes")
    assert result.primary.model == ""


def test_mapping_is_not_applied_to_another_enterprise(tmp_path):
    result = scan(tmp_path, enterprise=99999)
    assert result.primary.manufacturer == "M800C"
    assert result.primary.model == ""
    assert result.primary.platform == ""


def test_arbitrary_manufacturer_never_becomes_a_model(tmp_path):
    result = scan(tmp_path, manufacturer="Some OEM")
    assert result.primary.manufacturer == "Some OEM"
    assert result.primary.model == ""
