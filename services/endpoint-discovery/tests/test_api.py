import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from conftest import authorization
from sqlalchemy import select

from discovery.models import Observation
from discovery.schemas import Target
from discovery.store import provision, prune

MAC = "00:11:22:33:44:55"


def report(device="switch-a", at=None, kind="fdb", entries=None, status="complete", context="default"):
    if entries is None:
        entries = [{"mac": MAC, "ifindex": 1, "interface": "Gi1", "vlan": 10}]
        if kind == "neighbors":
            entries[0].update(ip="192.0.2.25", hostname="endpoint.example")
    return {"report_id": str(uuid4()), "device_key": device, "context": context,
            "observed_at": at or int(time.time()) - 120,
            "tables": [{"kind": kind, "status": status, "entries": entries}]}


def submit(client, payload, role="poller"):
    if "target_revision" not in payload:
        assigned = client.get("/v1/targets", headers=authorization(role if role != "reader" else "poller")).json()["items"]
        payload["target_revision"] = next((t["revision"] for t in assigned if t["key"] == payload["device_key"]), "0" * 64)
    return client.post("/v1/reports", json=payload, headers=authorization(role))


def test_auth_and_roles(api):
    client, app = api
    assert client.get("/healthz", headers={"Authorization": ""}).status_code == 200
    assert client.get("/v1/observations", headers={"Authorization": ""}).status_code == 401
    assert client.get("/v1/observations?token=" + "r" * 48, headers={"Authorization": ""}).status_code == 401
    assert submit(client, report(), "reader").status_code == 403
    assert client.get("/v1/observations", headers=authorization("poller")).status_code == 403
    assert client.get("/v1/targets").status_code == 403
    assert client.get("/v1/openapi.json").json()["info"]["version"] == "1.0.0"


def test_scope_is_enforced_for_reads_reports_and_targets(api):
    client, app = api
    assert submit(client, report(), "foreign").status_code == 403
    assert submit(client, report("switch-b"), "foreign").status_code == 200
    assert client.get("/v1/observations").json()["items"] == []
    for path in ("observations", "collections", "locate?mac=" + MAC + "&"):
        separator = "" if path.endswith("&") else "?"
        assert client.get("/v1/" + path + separator + "scope=b/site").status_code == 403
    targets = client.get("/v1/targets", headers=authorization("poller")).json()["items"]
    assert {t["key"] for t in targets} == {"switch-a", "router-a"}
    assert all(t["scope"] == "a/site" for t in targets)
    pollers = client.get("/v1/pollers").json()["items"]
    assert [p["name"] for p in pollers] == ["poller-a"]
    assert pollers[0]["stale"] is False


def test_retries_do_not_duplicate_and_reused_ids_conflict(api):
    client, app = api
    payload = report()
    assert submit(client, payload).status_code == 200
    assert submit(client, payload).json()["duplicate"] is True
    payload["tables"][0]["entries"][0]["interface"] = "Gi2"
    assert submit(client, payload).status_code == 409
    assert len(client.get("/v1/observations?current=false").json()["items"]) == 1


def test_history_tracks_moves_without_repeating_unchanged_polls(api):
    client, app = api
    at = int(time.time()) - 120
    submit(client, report(at=at))
    submit(client, report(at=at + 10))
    moved = [{"mac": MAC, "ifindex": 2, "interface": "Gi2", "vlan": 10}]
    submit(client, report(at=at + 20, entries=moved))
    rows = client.get("/v1/observations?current=false").json()["items"]
    assert len(rows) == 2
    assert (rows[0]["first_seen"], rows[0]["last_seen"], rows[0]["ended_at"]) == (at, at + 10, at + 20)
    assert rows[0]["netbox_interface_id"] == 101
    assert rows[1]["interface"] == "Gi2"
    assert client.get("/v1/observations").json()["items"][0]["id"] == rows[1]["id"]


@pytest.mark.parametrize("status", ["error", "unsupported"])
def test_failed_partial_or_unsupported_poll_never_removes_data(api, status):
    client, app = api
    at = int(time.time()) - 120
    submit(client, report(at=at))
    assert submit(client, report(at=at + 20, entries=[], status=status)).status_code == 200
    row = client.get("/v1/observations").json()["items"][0]
    assert row["last_seen"] == at and row["collection_status"] == status
    assert row["ended_at"] is None
    invalid = report(at=at + 30, status=status)
    assert submit(client, invalid).status_code == 422


def test_complete_empty_snapshot_closes_previous_rows(api):
    client, app = api
    at = int(time.time()) - 120
    submit(client, report(at=at))
    submit(client, report(at=at + 20, entries=[]))
    assert client.get("/v1/observations").json()["items"] == []
    assert client.get("/v1/observations?current=false").json()["items"][0]["ended_at"] == at + 20


def test_old_reports_cannot_overwrite_current_or_health(api):
    client, app = api
    at = int(time.time()) - 120
    submit(client, report(at=at + 20))
    assert submit(client, report(at=at, entries=[])).json()["ignored_tables"] == ["fdb"]
    assert len(client.get("/v1/observations").json()["items"]) == 1
    assert submit(client, report(at=int(time.time()) - 8 * 86400)).status_code == 422
    assert submit(client, report(at=int(time.time()) + 3600)).status_code == 422


def test_locate_correlates_ip_and_hostname_with_mac_and_marks_ambiguity(api):
    client, app = api
    assert submit(client, report()).status_code == 200
    assert submit(client, report("router-a", kind="neighbors")).status_code == 200
    for query in ("ip=192.0.2.25", "hostname=endpoint.example", "mac=0011.2233.4455"):
        result = client.get("/v1/locate?" + query).json()
        assert result["locations"][0]["interface"] == "Gi1"
        assert result["neighbors"][0]["ip"] == "192.0.2.25"
        assert result["ambiguous"] is False
    # A second unknown port is evidence of ambiguity, not an automatic move.
    submit(client, report("router-a"))
    assert client.get("/v1/locate?ip=192.0.2.25").json()["ambiguous"] is True


def test_locate_never_correlates_across_scope_vrf_or_known_vlan(api):
    client, app = api
    submit(client, report("switch-b"), "foreign")
    submit(client, report("router-a", kind="neighbors"))
    global_headers = authorization("global")
    assert client.get("/v1/locate?ip=192.0.2.25", headers=global_headers).json()["locations"] == []
    target = Target(key="other-vrf", scope="a/site", poller="poller-a", address="192.0.2.50", name="other",
                    contexts=[{"key": "blue", "vrf": "blue"}])
    provision(app.state.factory, [target])
    submit(client, report("other-vrf", context="blue"))
    wrong_vlan = report()
    wrong_vlan["tables"][0]["entries"][0]["vlan"] = 20
    submit(client, wrong_vlan)
    assert client.get("/v1/locate?ip=192.0.2.25", headers=global_headers).json()["locations"] == []


def test_unknown_vlan_remains_explicit_and_transit_ports_are_not_candidates(api):
    client, app = api
    payload = report()
    payload["tables"][0]["entries"][0].update(vlan=0, port_role="transit")
    submit(client, payload)
    submit(client, report("router-a", kind="neighbors"))
    result = client.get("/v1/locate?ip=192.0.2.25").json()
    assert result["locations"][0]["vlan"] == 0
    assert result["ambiguous"] is True


def test_stale_observations_do_not_look_fresh_after_failures(api):
    client, app = api
    at = int(time.time()) - 1800
    submit(client, report(at=at))
    submit(client, report(entries=[], status="error"))
    result = client.get("/v1/locate?mac=" + MAC).json()
    assert result["locations"][0]["stale"] is True
    assert result["ambiguous"] is True


def test_stale_ip_binding_does_not_make_fresh_forwarding_evidence_definitive(api):
    client, app = api
    submit(client, report())
    submit(client, report("router-a", at=int(time.time()) - 1800, kind="neighbors"))
    result = client.get("/v1/locate?ip=192.0.2.25").json()
    assert result["locations"][0]["stale"] is False
    assert result["neighbors"][0]["stale"] is True
    assert result["ambiguous"] is True


def test_metadata_updates_preserve_history_without_duplicate_active_keys(api):
    client, app = api
    at = int(time.time()) - 120
    submit(client, report(at=at))
    targets = client.get("/v1/targets", headers=authorization("poller")).json()["items"]
    target = Target.model_validate(next(t for t in targets if t["key"] == "switch-a"))
    target.name = "renamed"
    provision(app.state.factory, [target])
    assert submit(client, report(at=at + 10)).status_code == 200
    rows = client.get("/v1/observations?current=false").json()["items"]
    assert [(r["device_name"], r["ended_at"]) for r in rows] == [("access-a", at + 10), ("renamed", None)]


def test_validation_body_limit_and_pagination(api):
    client, app = api
    assert client.get("/v1/locate?mac=bad").status_code == 422
    assert client.get("/v1/locate?ip=1.2.3.4&mac=" + MAC).status_code == 422
    assert client.get("/v1/observations?limit=1001").status_code == 422
    assert client.get("/v1/collections?after_key=bad").status_code == 422
    assert client.post("/v1/reports", content=b" " * (16 * 1024 * 1024 + 1)).status_code == 413
    payload = report()
    payload["scope"] = "b/site"
    assert submit(client, payload).status_code == 422
    submit(client, report())
    submit(client, report("router-a"))
    page = client.get("/v1/observations?limit=1").json()
    next_page = client.get("/v1/observations", params={"after_id": page["next_after_id"], "limit": 1}).json()
    assert len(next_page["items"]) == 1
    assert next_page["items"][0]["id"] != page["items"][0]["id"]


def test_assignment_revocation_and_scope_changes_are_safe(api):
    client, app = api
    target = Target(key="switch-a", scope="b/site", poller="poller-b", address="192.0.2.10", name="moved")
    with pytest.raises(ValueError, match="immutable"):
        provision(app.state.factory, [target])
    provision(app.state.factory, [], replace=True)
    assert submit(client, report()).status_code == 403
    assert client.get("/v1/targets", headers=authorization("poller")).json()["items"] == []


def test_buffered_reports_cannot_be_relabeled_by_a_changed_assignment(api):
    client, app = api
    payload = report()
    targets = client.get("/v1/targets", headers=authorization("poller")).json()["items"]
    target = Target.model_validate(next(t for t in targets if t["key"] == "switch-a"))
    payload["target_revision"] = target.revision
    target.contexts[0].vrf = "changed"
    provision(app.state.factory, [target])
    assert submit(client, payload).status_code == 409
    assert client.get("/v1/observations").json()["items"] == []


def test_conflicting_rows_rollback_whole_report(api):
    client, app = api
    payload = report()
    payload["tables"][0]["entries"].append({**payload["tables"][0]["entries"][0], "interface": "different"})
    assert submit(client, payload).status_code == 422
    assert client.get("/v1/observations").json()["items"] == []


def test_retention_preserves_recent_history_and_last_seen_evidence(api):
    client, app = api
    submit(client, report())
    with app.state.factory.begin() as session:
        row = session.scalar(select(Observation))
        row.last_seen = int(time.time()) - 91 * 86400
    from discovery.config import Settings
    result = prune(app.state.factory, Settings("", ""))
    assert result["observations_deleted"] == 1
    assert client.get("/v1/observations?current=false").json()["items"] == []


def test_postgresql_serializes_concurrent_snapshots(api):
    client, app = api
    if app.state.engine.dialect.name != "postgresql":
        pytest.skip("requires TEST_DATABASE_URL PostgreSQL")
    at = int(time.time()) - 120
    older, newer = report(at=at), report(at=at + 10, entries=[])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda p: submit(client, p), [newer, older]))
    assert all(r.status_code == 200 for r in results)
    assert client.get("/v1/observations").json()["items"] == []
