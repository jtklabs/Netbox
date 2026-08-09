"""Shared test fixtures.

Two ways to drive the scanner in tests, on purpose:

  ReplaySession  feeds a recorded walk straight into the collector. No snmpd,
                 no sockets, runs anywhere — this is what the parsing and
                 modelling tests use, so they stay fast and have no external
                 dependency.

  EmulatedDevice a real snmpd serving the same recorded walk over real
                 SNMPv3. Slower and needs net-snmp installed, so it is used for
                 the tests that are specifically about the wire: credential
                 handling, fallback between credential sets, GETBULK, and the
                 parsing of what net-snmp actually prints.

Both read the identical fixture file, so a fixture only has to be right once.
"""

from __future__ import annotations

import os
import shutil
import sys

import pytest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(TESTS_DIR)
FIXTURES = os.path.join(TESTS_DIR, "fixtures")

sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(TESTS_DIR, "emulator"))

from snmpinv.collect import Collector, DeviceFacts  # noqa: E402
from snmpinv.snmp import Credential, VarBind, parse_varbinds  # noqa: E402
from walkfile import format_walk, load_walk  # noqa: E402

LAB_CREDENTIAL = Credential(
    name="lab",
    security_name="netops",
    auth_protocol="SHA-256",
    auth_passphrase="labauthpass123",
    priv_protocol="AES",
    priv_passphrase="labprivpass123",
)


def fixture_path(name: str) -> str:
    return os.path.join(FIXTURES, f"{name}.walk")


class ReplaySession:
    """Stands in for CredentialSession, answering from a recorded walk.

    Deliberately re-renders the fixture through the scanner's own parser rather
    than handing over pre-parsed objects: that keeps the parser in the path
    being tested, which is the point.
    """

    def __init__(self, walk_path: str):
        self.varbinds = load_walk(walk_path)
        self.text = format_walk(self.varbinds)
        self.parsed = parse_varbinds(self.text)
        self.walk_calls: list[str] = []
        self.get_calls: list[list[str]] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def walk(self, host: str, oid: str) -> list[VarBind]:
        self.walk_calls.append(oid)
        prefix = oid.rstrip(".") + "."
        return [b for b in self.parsed if b.oid == oid or b.oid.startswith(prefix)]

    def get(self, host: str, oids: list[str]) -> dict[str, VarBind]:
        self.get_calls.append(list(oids))
        wanted = set(oids)
        return {b.oid: b for b in self.parsed if b.oid in wanted}


class ReplayCollector(Collector):
    """A Collector that talks to a fixture instead of a device."""

    def __init__(self, walk_path: str, **kwargs):
        super().__init__([LAB_CREDENTIAL], **kwargs)
        self.walk_path = walk_path
        self.session = ReplaySession(walk_path)

    def collect(self, host: str) -> DeviceFacts:
        session = self.session
        system = session.walk(host, "1.3.6.1.2.1.1")
        return self._collect_with(session, host, self.credentials[0], system)


def collect_fixture(name: str, host: str = "192.0.2.1") -> DeviceFacts:
    """Collect a fixture into DeviceFacts without any network involved."""
    return ReplayCollector(fixture_path(name)).collect(host)


@pytest.fixture(scope="session")
def snmpd_available() -> bool:
    return shutil.which("snmpd") is not None


needs_snmpd = pytest.mark.skipif(
    shutil.which("snmpd") is None,
    reason="net-snmp's snmpd is not installed; the emulator tests need it",
)
