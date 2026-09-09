"""Synthetic FortiManager/Analyzer replies with no FortiGate version scalar."""

import pytest

from conftest import ReplayCollector
from snmpinv.model import build_scan_result


@pytest.mark.parametrize("product", ["FortiManager", "FortiAnalyzer"])
@pytest.mark.parametrize("description_version", ["", " v7.0.0"])
def test_management_version_scalar_reaches_device(tmp_path, product, description_version):
    version = "v7.4.6-build2731 250701 (GA)"
    path = tmp_path / "management.walk"
    path.write_text("\n".join([
        f'.1.3.6.1.2.1.1.1.0 = STRING: "{product}-VM64{description_version}"',
        '.1.3.6.1.2.1.1.2.0 = OID: 1.3.6.1.4.1.12356.103',
        '.1.3.6.1.2.1.1.5.0 = STRING: "management-test"',
        '.1.3.6.1.4.1.12356.100.1.1.1.0 = STRING: "TEST-123456"',
        f'.1.3.6.1.4.1.12356.103.2.1.7.0 = STRING: "{version}"',
    ]) + "\n")
    collector = ReplayCollector(str(path))
    facts = collector.collect("192.0.2.10")
    device = build_scan_result(facts).primary
    assert device.software_version == version
    assert device.platform == product
    assert device.model == f"{product}-VM64"
    assert device.serial == "TEST-123456"
    assert facts.vendor_scalars["1.3.6.1.4.1.12356.103.2.1.7.0"] == version


@pytest.mark.parametrize("product", ["FortiManager", "FortiAnalyzer"])
def test_management_description_fallback_without_scalar(tmp_path, product):
    path = tmp_path / "management.walk"
    path.write_text("\n".join([
        f'.1.3.6.1.2.1.1.1.0 = STRING: "{product}-VM64 v7.4.6"',
        '.1.3.6.1.2.1.1.2.0 = OID: 1.3.6.1.4.1.12356.103',
    ]) + "\n")
    device = build_scan_result(ReplayCollector(str(path)).collect("192.0.2.10")).primary
    assert device.software_version == "7.4.6"
    assert device.platform == product
