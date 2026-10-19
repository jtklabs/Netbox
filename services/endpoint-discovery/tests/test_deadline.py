from types import SimpleNamespace

import pytest

from snmpinv import snmp


def test_snmp_commands_share_remaining_device_budget_and_stop_at_deadline(monkeypatch):
    clock = [100.0]
    calls = []
    monkeypatch.setattr(snmp, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(snmp.shutil, "which", lambda tool: "/usr/bin/" + tool)

    def run(*args, **kwargs):
        calls.append(kwargs["timeout"])
        return SimpleNamespace(stdout=".1.3.6.1.2.1.1.2.0 = INTEGER: 1", stderr="", returncode=0)

    monkeypatch.setattr(snmp.subprocess, "run", run)
    credential = snmp.Credential(name="test", security_name="reader")
    with snmp.CredentialSession(credential, deadline=105) as session:
        session.walk("127.0.0.1", "1.3.6.1.2.1.1")
        clock[0] = 103
        session.get("127.0.0.1", ["1.3.6.1.2.1.1.2.0"])
        clock[0] = 106
        with pytest.raises(snmp.SnmpTimeoutError):
            session.get("127.0.0.1", ["1.3.6.1.2.1.1.2.0"])
    assert calls == [5, 2]
