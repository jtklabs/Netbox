"""Exercise real SNMPv3 authentication, walks, parsing, and report ingestion."""
import shutil
import sys
from pathlib import Path

import pytest
from conftest import authorization

from discovery.collector import collect
from discovery.schemas import Target
from discovery.store import provision

EMULATOR = Path(__file__).resolve().parents[3] / "scripts" / "snmp-inventory" / "tests" / "emulator"
sys.path.insert(0, str(EMULATOR))
from emulator import EmulatedDevice  # noqa: E402


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("snmpd") or not shutil.which("snmpbulkwalk"),
                    reason="requires Linux net-snmp with SNMPv3; run Dockerfile.test")
def test_real_snmpv3_roundtrip_to_api(api, tmp_path):
    walk = tmp_path / "endpoint.walk"
    walk.write_text('''
.1.3.6.1.2.1.1.2.0 = OID: .1.3.6.1.4.1.9.1.1
.1.3.6.1.2.1.31.1.1.1.1.101 = STRING: "Gi1"
.1.3.6.1.2.1.31.1.1.1.1.110 = STRING: "Vlan10"
.1.3.6.1.2.1.17.1.4.1.2.5 = INTEGER: 101
.1.3.6.1.2.1.17.7.1.2.2.1.2.77.0.17.34.51.68.85 = INTEGER: 5
.1.3.6.1.2.1.17.7.1.2.2.1.3.77.0.17.34.51.68.85 = INTEGER: 3
.1.3.6.1.2.1.17.7.1.4.2.1.3.0.10 = INTEGER: 77
.1.3.6.1.2.1.4.35.1.4.110.1.4.192.0.2.25 = Hex-STRING: 00 11 22 33 44 55
.1.3.6.1.2.1.4.35.1.7.110.1.4.192.0.2.25 = INTEGER: 1
''')
    client, app = api
    with EmulatedDevice(str(walk), port=11963) as device:
        target = Target(key="wire", scope="a/site", poller="poller-a", address=device.listen,
                        snmp_port=device.port, name="wire-switch")
        provision(app.state.factory, [target])
        targets = client.get("/v1/targets", headers=authorization("poller")).json()["items"]
        assigned = Target.model_validate(next(t for t in targets if t["key"] == "wire"))
        report = collect(assigned, assigned.contexts[0], {
            "security_name": device.user, "auth_protocol": device.auth_protocol,
            "auth_passphrase": device.auth_pass, "priv_protocol": device.priv_protocol,
            "priv_passphrase": device.priv_pass,
        })
    assert [(t.kind, t.status) for t in report.tables] == [("fdb", "complete"), ("neighbors", "complete")]
    assert client.post("/v1/reports", json=report.model_dump(), headers=authorization("poller")).status_code == 200
    result = client.get("/v1/locate?ip=192.0.2.25").json()
    assert result["locations"][0]["interface"] == "Gi1"
    assert result["locations"][0]["vlan"] == 10
    assert result["neighbors"][0]["ip"] == "192.0.2.25"
