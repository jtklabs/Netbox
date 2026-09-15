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


# --- when the agent will not answer a batched GET ---------------------------
#
# The Fortinet profile asks for the FortiGate version scalar and the
# FortiManager/FortiAnalyzer one in a single GET. An agent that fails the
# whole request over the one it does not serve would leave the version blank
# on a device that publishes it perfectly well.

from conftest import ReplaySession  # noqa: E402
from snmpinv.snmp import SnmpAuthError, SnmpTimeoutError  # noqa: E402


class RefusesBatches(ReplaySession):
    """An agent that answers authorizationError to a GET naming an OID
    outside its view, rather than marking that varbind noSuchObject."""

    def get(self, host, oids):
        if len(oids) > 1:
            self.get_calls.append(list(oids))
            raise SnmpAuthError(f"{host}: authorizationError")
        return super().get(host, oids)


class AnswersNothingToBatches(ReplaySession):
    """An agent whose batched reply carries no usable varbinds at all."""

    def get(self, host, oids):
        if len(oids) > 1:
            self.get_calls.append(list(oids))
            return {}
        return super().get(host, oids)


class TimesOutOnBatches(ReplaySession):
    def get(self, host, oids):
        self.get_calls.append(list(oids))
        raise SnmpTimeoutError(f"{host}: no response")


def _fortianalyzer_walk(tmp_path):
    path = tmp_path / "faz.walk"
    path.write_text("\n".join([
        '.1.3.6.1.2.1.1.1.0 = STRING: "FortiAnalyzer-VM64"',
        '.1.3.6.1.2.1.1.2.0 = OID: 1.3.6.1.4.1.12356.103',
        '.1.3.6.1.2.1.1.5.0 = STRING: "faz-01"',
        '.1.3.6.1.4.1.12356.100.1.1.1.0 = STRING: "FAZ-VMTM12345678"',
        '.1.3.6.1.4.1.12356.103.2.1.7.0 = STRING: "v7.4.6-build2731 250701 (GA)"',
    ]) + "\n")
    return str(path)


@pytest.mark.parametrize("session_class", [RefusesBatches, AnswersNothingToBatches])
def test_a_failed_batch_is_asked_again_one_oid_at_a_time(tmp_path, session_class):
    path = _fortianalyzer_walk(tmp_path)
    collector = ReplayCollector(path)
    collector.session = session_class(path)
    device = build_scan_result(collector.collect("192.0.2.10")).primary

    assert device.software_version == "v7.4.6-build2731 250701 (GA)"
    assert device.serial == "FAZ-VMTM12345678"
    assert device.platform == "FortiAnalyzer"
    calls = collector.session.get_calls
    # One batch, then each OID on its own -- and nothing more.
    assert len(calls[0]) == 3
    assert calls[1:] == [[oid] for oid in calls[0]]


def test_a_healthy_agent_is_asked_once(tmp_path):
    """The fallback must cost nothing on the ordinary path."""
    collector = ReplayCollector(_fortianalyzer_walk(tmp_path))
    collector.collect("192.0.2.10")
    assert len(collector.session.get_calls) == 1


def test_a_timeout_is_not_retried(tmp_path):
    """Silence is silence; four more GETs would only multiply the wait."""
    path = _fortianalyzer_walk(tmp_path)
    collector = ReplayCollector(path)
    collector.session = TimesOutOnBatches(path)
    device = build_scan_result(collector.collect("192.0.2.10")).primary
    assert device.software_version == ""
    assert len(collector.session.get_calls) == 1


def test_the_probe_shows_which_oid_answered(tmp_path):
    """Blank in NetBox has two causes -- wrong OID, or the device answered
    nothing -- and only the raw result says which. Kept for the fallback
    path too, since that is exactly when somebody will be looking."""
    path = _fortianalyzer_walk(tmp_path)
    collector = ReplayCollector(path)
    collector.session = RefusesBatches(path)
    facts = collector.collect("192.0.2.10")
    assert facts.vendor_scalars["1.3.6.1.4.1.12356.103.2.1.7.0"] == "v7.4.6-build2731 250701 (GA)"
    assert facts.vendor_scalars["1.3.6.1.4.1.12356.101.4.1.1.0"] is None
