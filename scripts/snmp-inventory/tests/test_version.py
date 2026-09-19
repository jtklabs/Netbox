"""The build a poller reports to NetBox.

The number in `__version__` is bumped by hand and sat at 1.0.0 for the first
forty changes, so NetBox showed 1.0.0 against every poller whatever it was
running. These pin the two things that fix that: the reported version moves
when the code does, and the sweep reports it as well as the onboarding queue.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import snmp_inventory
import snmpinv
from snmpinv.netbox import NetBoxError


def _copy_of_scanner(tmp_path, monkeypatch) -> Path:
    """Point build_version() at a throwaway copy it is safe to edit."""
    root = Path(snmpinv.__file__).resolve().parent.parent
    shutil.copytree(root / "snmpinv", tmp_path / "snmpinv",
                    ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copy(root / "snmp_inventory.py", tmp_path / "snmp_inventory.py")
    monkeypatch.setattr(snmpinv, "__file__", str(tmp_path / "snmpinv" / "__init__.py"))
    return tmp_path


def test_version_carries_a_fingerprint_and_fits_the_plugin_field():
    version = snmpinv.build_version()
    assert re.fullmatch(re.escape(snmpinv.__version__) + r"\+[0-9a-f]{7}", version)
    assert len(version) <= 50  # DiscoveryPoller.version is a CharField(50)
    assert version == snmpinv.build_version()


def test_a_code_change_moves_the_version_without_a_bump(tmp_path, monkeypatch):
    root = _copy_of_scanner(tmp_path, monkeypatch)
    before = snmpinv.build_version()
    with open(root / "snmpinv" / "vendors.py", "a") as handle:
        handle.write("\n# a fix nobody bumped the version for\n")
    assert snmpinv.build_version() != before


def test_the_entry_script_is_part_of_the_build(tmp_path, monkeypatch):
    root = _copy_of_scanner(tmp_path, monkeypatch)
    before = snmpinv.build_version()
    with open(root / "snmp_inventory.py", "a") as handle:
        handle.write("\n# changed\n")
    assert snmpinv.build_version() != before


def test_an_unreadable_source_still_reports_the_number(tmp_path, monkeypatch):
    _copy_of_scanner(tmp_path, monkeypatch)
    monkeypatch.setattr(Path, "read_bytes", lambda self: (_ for _ in ()).throw(OSError()))
    assert snmpinv.build_version() == snmpinv.__version__


def test_a_sweep_reports_its_build_without_taking_work(monkeypatch):
    sent = {}

    def check_in(netbox, poller_name, **kwargs):
        sent.update(kwargs, poller_name=poller_name)
        return []

    monkeypatch.setattr(snmp_inventory.onboarding, "check_in", check_in)
    snmp_inventory.report_sweep(object(), "dallas", "sweep: 4 scanned, 0 failed in 9s")

    assert sent["poller_name"] == "dallas"
    assert sent["version"] == snmpinv.build_version()
    assert sent["summary"] == "sweep: 4 scanned, 0 failed in 9s"
    assert sent["claim"] is False


def test_a_sweep_survives_a_netbox_with_no_discovery_plugin(monkeypatch):
    def check_in(*args, **kwargs):
        raise NetBoxError("POST /plugins/discovery/pollers/check-in/ -> 404")

    monkeypatch.setattr(snmp_inventory.onboarding, "check_in", check_in)
    snmp_inventory.report_sweep(object(), "dallas", "")
