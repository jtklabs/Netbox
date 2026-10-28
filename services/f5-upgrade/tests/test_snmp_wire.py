"""Real UDP loopback coverage of SNMP v3 discovery, authentication and privacy."""

import asyncio
import socket

import pytest
from pysnmp.carrier.asyncio.dgram import udp, udp6
from pysnmp.entity import config, engine
from pysnmp.entity.rfc3413 import cmdrsp, context

from f5_upgrade.snmp import SnmpRunner
from f5_upgrade.snmp_models import SnmpProfile, SnmpWalkRequest


@pytest.mark.parametrize(
    "level,ipv6,bad_password",
    [
        ("authPriv", False, False),
        ("authNoPriv", False, False),
        ("noAuthNoPriv", True, False),
        ("authPriv", False, True),
    ],
)
def test_loopback_v3(settings, store, monkeypatch, level, ipv6, bad_password):
    monkeypatch.setenv("TEST_SNMP_AUTH", "wrong-passphrase" if bad_password else "loopback-auth-passphrase")
    monkeypatch.setenv("TEST_SNMP_PRIV", "loopback-priv-passphrase")
    settings.snmp_profiles = {
        "default": SnmpProfile(
            id="default",
            username="test-reader",
            security_level=level,
            auth_password_env="TEST_SNMP_AUTH" if level != "noAuthNoPriv" else None,
            priv_password_env="TEST_SNMP_PRIV" if level == "authPriv" else None,
        )
    }

    async def exercise():
        agent = engine.SnmpEngine()
        sock = socket.socket(socket.AF_INET6 if ipv6 else socket.AF_INET, socket.SOCK_DGRAM)
        address = "::1" if ipv6 else "127.0.0.1"
        sock.bind((address, 0))
        port = sock.getsockname()[1]
        transport = udp6 if ipv6 else udp
        config.add_transport(
            agent,
            transport.DOMAIN_NAME,
            (udp6.Udp6Transport() if ipv6 else udp.UdpTransport()).open_server_mode(sock=sock),
        )
        config.add_v3_user(
            agent,
            "test-reader",
            config.USM_AUTH_HMAC192_SHA256 if level != "noAuthNoPriv" else config.USM_AUTH_NONE,
            "loopback-auth-passphrase" if level != "noAuthNoPriv" else None,
            config.USM_PRIV_CFB128_AES if level == "authPriv" else config.USM_PRIV_NONE,
            "loopback-priv-passphrase" if level == "authPriv" else None,
        )
        config.add_vacm_user(agent, 3, "test-reader", level, (1, 3, 6, 1, 2, 1))
        responder = cmdrsp.NextCommandResponder(agent, context.SnmpContext(agent))
        try:
            request = SnmpWalkRequest(
                device_ip=address, port=port, oids=["1.3.6.1.2.1.1", "1.3.6.1.2.1.999"], job_timeout=5
            )
            job = store.submit(request.model_dump(mode="json"), "loopback", kind="snmp_walk")
            runner = SnmpRunner(settings, store, store.claim("wire"), "wire")
            await runner.walk()
            if bad_password:
                assert runner.result["rows"] == 0
                assert all(r["status"] == "failed" for r in runner.result["oids"]), runner.result
                return
            assert all(r["status"] == "succeeded" for r in runner.result["oids"]), runner.result
            rows = store.snmp_results(job["id"])["rows"]
            assert rows
            assert all(row["oid"].startswith("1.3.6.1.2.1.1.") for row in rows)
            assert runner.result["oids"][1]["rows"] == 0
        finally:
            responder.close(agent)
            agent.close_dispatcher()
            sock.close()

    asyncio.run(exercise())
