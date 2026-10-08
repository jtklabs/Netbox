"""Inventory sweeps keep the SSH collector inside the NetBox write boundary."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import snmp_inventory


@pytest.mark.parametrize("dry_run", [False, True])
def test_sweep_handoff_keeps_poller_scope_and_prevents_dry_run_writes(monkeypatch, dry_run):
    monkeypatch.setattr(snmp_inventory.os.path, "isfile", lambda path: True)
    run = Mock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr("subprocess.run", run)
    config = SimpleNamespace(poller_name="east", commands=SimpleNamespace(
        netops_dir="/opt/nornir-netops", python="/opt/nornir-netops/.venv/bin/python",
        extra_args="--workers 4"))
    snmp_inventory.run_command_collection(config, dry_run=dry_run)
    command = run.call_args.args[0]
    assert command[1:5] == ["configure.py", "collect", "--netbox", "--netbox-autofilter"]
    assert command[5:7] == ["--poller", "east"]
    assert "--no-ntp-discovery" not in command
    assert ("--no-upload" in command) == dry_run
    assert run.call_args.kwargs["cwd"] == "/opt/nornir-netops"
