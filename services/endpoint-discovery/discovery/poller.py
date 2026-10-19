"""Outbound-only poller with a durable SQLite outbox and cached assignments."""
import argparse
import json
import logging
import os
import ssl
import time
from pathlib import Path

import httpx

from .collector import collect
from .poller_url import secure_url
from .runtime import Runtime
from .scheduler import Scheduler
from .schemas import Target

log = logging.getLogger("endpoint-poller")



def cycle(client, outbox, credentials, workers=32):
    """One parallel collection pass for callers that already own an HTTP client."""
    delivered = outbox.flush(client)
    try:
        response = client.get("v1/targets")
        response.raise_for_status()
        targets = [Target.model_validate(t) for t in response.json()["items"]]
        outbox.save_targets(targets)
    except (httpx.HTTPError, ValueError, KeyError):
        # An explicit authorization failure revokes cached assignments.
        if "response" in locals() and response.status_code in (401, 403):
            raise RuntimeError("poller authorization rejected; cached assignments will not be used") from None
        targets = outbox.cached_targets()
        delivered = False
        log.warning("using cached target assignments")
    scheduler = Scheduler(credentials, workers=workers, stagger=False, repeat=False, collector=collect)
    scheduler.update(targets)
    try:
        while True:
            scheduler.step(outbox)
            if scheduler.blocked:
                raise RuntimeError("outbox is full")
            if delivered:
                delivered = outbox.flush(client)
            if (all(s.attempts for s in scheduler.states.values()) and not scheduler.running
                    and scheduler.results.empty() and scheduler.pending is None):
                break
            time.sleep(0.01)
        return delivered and scheduler.errors == 0 and outbox.stats()["quarantined_reports"] == 0
    finally:
        scheduler.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    os.umask(0o077)
    url = secure_url(os.environ["DISCOVERY_URL"])
    token = Path(os.environ["DISCOVERY_TOKEN_FILE"]).read_text().strip()
    if len(token) < 32:
        raise ValueError("a poller token of at least 32 characters is required")
    credentials = json.loads(Path(os.environ["DISCOVERY_CREDENTIALS_FILE"]).read_text())
    # Reject multiline config directives before anything reaches snmp.conf.
    allowed = {"security_name", "auth_protocol", "auth_passphrase", "priv_protocol", "priv_passphrase"}
    for profile in credentials.values():
        if set(profile) - allowed or any(not isinstance(v, str) or "\n" in v or "\r" in v for v in profile.values()):
            raise ValueError("invalid SNMP credential profile")
        if not all(profile.get(k) for k in ("security_name", "auth_passphrase", "priv_passphrase")):
            raise ValueError("SNMPv3 authPriv credentials are required")
    trust = ssl.create_default_context(cafile=os.getenv("DISCOVERY_CA_FILE"))

    def client_factory():
        return httpx.Client(base_url=url + "/", headers={"Authorization": "Bearer " + token},
                            verify=trust, follow_redirects=False, timeout=60)

    runtime = Runtime(
        os.getenv("DISCOVERY_SPOOL_DIR", "/var/lib/endpoint-discovery"), credentials, client_factory,
        workers=int(os.getenv("DISCOVERY_WORKERS", "32")),
        interval=int(os.getenv("DISCOVERY_POLL_SECONDS", "60")),
        upload_workers=int(os.getenv("DISCOVERY_UPLOAD_WORKERS", "2")),
        refresh_seconds=int(os.getenv("DISCOVERY_TARGET_REFRESH_SECONDS", "60")),
        device_timeout=int(os.getenv("DISCOVERY_DEVICE_TIMEOUT_SECONDS", "120")), once=args.once,
    )
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
