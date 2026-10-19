import json

import httpx
import pytest

from discovery.poller import Outbox, cycle
from discovery.poller_url import secure_url
from discovery.schemas import Report, Target


def payload():
    return Report.model_validate({"report_id": "d9ad6209-c0da-4976-82bd-0746fe828c24", "device_key": "a",
                                  "target_revision": "0" * 64,
                                  "context": "default", "observed_at": 1700000000,
                                  "tables": [{"kind": "fdb", "status": "complete", "entries": []}]})


def test_outbox_survives_restart_retries_and_acknowledges_only_success(tmp_path):
    box = Outbox(tmp_path)
    box.put(payload())
    box.connection.close()
    box = Outbox(tmp_path)
    calls = []

    def unavailable(request):
        calls.append(json.loads(request.content))
        return httpx.Response(503)

    with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(unavailable)) as client:
        assert box.flush(client) is False
    assert box.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1
    with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={"accepted": True, "duplicate": True}))) as client:
        assert box.flush(client) is True
    assert box.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0
    assert calls[0]["report_id"] == payload().report_id


def test_rejected_reports_are_retained_but_do_not_block_delivery(tmp_path):
    box = Outbox(tmp_path)
    box.put(payload())
    box.put(payload())
    responses = iter([httpx.Response(403), httpx.Response(200, json={"accepted": True})])
    with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(lambda r: next(responses))) as client:
        assert box.flush(client) is True
    assert box.connection.execute("SELECT rejected FROM reports").fetchall() == [(1,)]


def test_redirects_and_malformed_acks_do_not_lose_reports(tmp_path):
    box = Outbox(tmp_path)
    box.put(payload())
    for response in (httpx.Response(302, headers={"Location": "https://other/"}),
                     httpx.Response(200, text="invalid"), httpx.Response(200, json=[])):
        with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(lambda r: response)) as client:
            assert box.flush(client) is False
    assert box.connection.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 1


def test_outbox_capacity_includes_quarantined_reports(tmp_path):
    box = Outbox(tmp_path, max_bytes=1)
    with pytest.raises(RuntimeError, match="full"):
        box.put(payload())


def test_cached_assignments_expire_and_rejected_auth_stops_collection(tmp_path):
    box = Outbox(tmp_path)
    box.save_targets([Target(key="a", scope="a", poller="p", address="192.0.2.1", name="a")])
    assert box.cached_targets()[0].key == "a"
    with pytest.raises(RuntimeError, match="recent"):
        box.cached_targets(now=9999999999)
    with httpx.Client(base_url="https://test/", transport=httpx.MockTransport(lambda r: httpx.Response(401))) as client:
        with pytest.raises(RuntimeError, match="authorization"):
            cycle(client, box, {})


@pytest.mark.parametrize("url", ["http://host", "https://user:password@host", "https://host?a=b", "https://host/#x"])
def test_only_verified_https_without_embedded_credentials_is_accepted(url):
    with pytest.raises(ValueError):
        secure_url(url)
