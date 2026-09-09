"""AP versions must remain visible and survive saved-scan application."""

import io
from unittest.mock import Mock

from conftest import collect_fixture
from snmpinv.model import build_scan_result
from snmpinv.onboarding import scan_payload, scan_result_from_payload
from snmpinv.probe import _print_report, facts_to_dict
from snmpinv.sync import LIFECYCLE_REPORT_ENDPOINT, Syncer


def controller_scan():
    facts = collect_fixture("aruba-7010-wlc")
    return facts, build_scan_result(facts)


def test_json_distinguishes_ap_reading_from_controller_fallback():
    facts, result = controller_scan()
    data = facts_to_dict(facts, result)
    raw = {ap["name"]: ap["software_version"] for ap in data["access_points"]}
    effective = {ap["name"]: ap["software_version"]
                 for ap in data["would_create"]["access_point_devices"]}
    assert raw["dal-ap-101"] == "8.10.0.4_87457"
    assert raw["dal-ap-102"] == ""
    assert effective["dal-ap-101"] == "8.10.0.4_87457"
    assert effective["dal-ap-102"] == "8.10.0.4"
    assert data["would_create"]["access_points"] == len(result.access_points)


def test_text_probe_shows_ap_release_and_effective_release():
    facts, result = controller_scan()
    out = io.StringIO()
    _print_report(facts, result, out)
    raw, effective = out.getvalue().split("WHAT THIS WOULD BECOME IN NETBOX")
    assert "8.10.0.4_87457" in raw
    assert "(not reported)" in next(line for line in raw.splitlines() if "dal-ap-102" in line)
    assert "software 8.10.0.4" in next(line for line in effective.splitlines() if "dal-ap-102" in line)


def test_saved_scan_retains_complete_ap_records():
    _, result = controller_scan()
    restored = scan_result_from_payload(scan_payload(result), host=result.host)
    assert restored.access_points == result.access_points


def test_saved_ap_versions_reach_lifecycle_report():
    _, result = controller_scan()
    restored = scan_result_from_payload(scan_payload(result), host=result.host)
    netbox = Mock()
    netbox.post_raw.return_value = {"results": []}
    syncer = Syncer(netbox)
    syncer._use_lifecycle = True
    syncer._ensure_device = Mock(side_effect=[
        {"id": i, "name": ap.name} for i, ap in enumerate(restored.access_points, 1)
    ])
    syncer._sync_access_points(restored, site_id=1)
    syncer.flush_software_reports()
    args = netbox.post_raw.call_args.args
    assert args[0] == LIFECYCLE_REPORT_ENDPOINT
    assert [r["version"] for r in args[1]] == [ap.software_version for ap in result.access_points]
    assert all(r["platform"] == "ArubaOS" for r in args[1])
    assert len(args[1]) == 3


def test_old_preview_keeps_ap_identity_without_inventing_version():
    restored = scan_result_from_payload({
        "devices": [],
        "access_points": [{"name": "old-ap", "model": "AP-515", "serial": "TESTAP001"}],
    })
    ap = restored.access_points[0]
    assert ap.is_access_point
    assert ap.manufacturer == "Aruba Networks"
    assert ap.platform == "ArubaOS"
    assert ap.software_version == ""
    assert scan_result_from_payload({"devices": []}).access_points == []
