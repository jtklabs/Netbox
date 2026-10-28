from urllib.parse import parse_qs

import httpx
import pytest
from conftest import Clock
from fastapi.testclient import TestClient
from test_controls_credentials import vault_for

from netops_executor import panos
from netops_executor.api import create_app
from netops_executor.config import HTTPTarget
from netops_executor.credentials import Credential
from netops_executor.execution_models import ValidationDecision
from netops_executor.panos_engine import TRAFFIC, PanosRunner, compare
from netops_executor.panos_models import PanosBatch, PanosUpgradeRequest, PanosValidationPolicy

TARGET = "11.1.6-h3"
WRITES = (
    "<request><system><software><download>",
    "<request><system><software><install>",
    "<request><restart>",
    "<request><high-availability>",
)


class FakeFirewall:
    jobs_issued = 100

    def __init__(self, serial, version="10.2.9-h1"):
        self.serial, self.version, self.model = serial, version, "PA-3220"
        self.uptime = 10 * 86400
        self.software = {v: v == version for v in ("10.2.9-h1", "11.1.0", TARGET)}
        self.installed = None
        self.jobs = {}
        self.pending = False
        self.rebooting = 0
        self.boot_polls = 3
        self.install_result = "OK"
        self.download_polls = 0
        self.on_download = None
        self.pair = self.peer = None
        self.interfaces = {"ethernet1/1": "up", "ethernet1/2": "up", "ethernet1/3": "down"}
        self.routes = [
            ("default", "0.0.0.0/0", "192.0.2.1", "ethernet1/1"),
            ("default", "10.0.0.0/8", "", "ae1"),
        ]
        self.bgp = {"isp1": "Established"}
        self.tunnels = {"branch-1": "active"}
        self.writes = []

    def job(self, kind, result="OK", polls=0):
        FakeFirewall.jobs_issued += 1
        self.jobs[FakeFirewall.jobs_issued] = {"type": kind, "result": result, "polls": polls}
        return FakeFirewall.jobs_issued

    def job_xml(self, job_id):
        job = self.jobs[job_id]
        running = job["polls"] > 0
        job["polls"] -= 1
        return (
            f"<job><id>{job_id}</id><type>{job['type']}</type><status>{'ACT' if running else 'FIN'}</status>"
            f"<result>{'PEND' if running else job['result']}</result><progress>{50 if running else 100}</progress>"
            f"<details><line>{job['type']} {job['result']}</line></details></job>"
        )

    def ha_xml(self):
        if not self.pair:
            return "<enabled>no</enabled>"
        state, peer = self.pair.state, self.peer
        conn = "down" if peer.rebooting else "up"
        sync = "synchronized" if self.version == peer.version else "not synchronized"
        return (
            "<enabled>yes</enabled><group><mode>Active-Passive</mode>"
            f"<local-info><state>{state[self.serial]}</state><build-rel>{self.version}</build-rel>"
            "<preemptive>no</preemptive></local-info>"
            f"<peer-info><conn-status>{conn}</conn-status><state>{state[peer.serial]}</state>"
            f"<build-rel>{peer.version}</build-rel></peer-info><running-sync>{sync}</running-sync></group>"
        )

    def handle(self, cmd):
        if self.rebooting:
            self.rebooting -= 1
            if not self.rebooting:
                self.version, self.uptime = self.installed, 30
                self.job("AutoCom")
            return None
        if cmd.startswith(WRITES):
            self.writes.append(cmd)
        if cmd == panos.SYSTEM_INFO:
            days, rest = divmod(self.uptime, 86400)
            hours, rest = divmod(rest, 3600)
            uptime = f"{days} days, {hours}:{rest // 60:02}:{rest % 60:02}"
            return (
                f"<system><hostname>fw-{self.serial}</hostname><serial>{self.serial}</serial>"
                f"<model>{self.model}</model><sw-version>{self.version}</sw-version>"
                f"<app-version>8800-8000</app-version><uptime>{uptime}</uptime>"
                "<operational-mode>normal</operational-mode></system>"
            )
        if cmd == panos.CHASSIS_READY:
            return "yes"
        if cmd == panos.HA_STATE:
            return self.ha_xml()
        if cmd == panos.PENDING_CHANGES:
            return "yes" if self.pending else "no"
        if cmd == panos.JOBS:
            return "".join(self.job_xml(j) for j in self.jobs)
        if cmd.startswith("<show><jobs><id>"):
            return self.job_xml(int(cmd.split("<id>")[1].split("<")[0]))
        if cmd in (panos.SOFTWARE_CHECK, panos.SOFTWARE_INFO):
            return (
                "<sw-updates><versions>"
                + "".join(
                    f"<entry><version>{v}</version><downloaded>{'yes' if d else 'no'}</downloaded></entry>"
                    for v, d in self.software.items()
                )
                + "</versions></sw-updates>"
            )
        if cmd.startswith("<request><system><software><download>"):
            version = cmd.split("<version>")[1].split("<")[0]
            self.software[version] = True
            if self.on_download:
                self.on_download()
            return f"<job>{self.job('Downld', polls=self.download_polls)}</job>"
        if cmd.startswith("<request><system><software><install>"):
            version = cmd.split("<version>")[1].split("<")[0]
            if self.install_result == "OK":
                self.installed = version
            return f"<job>{self.job('SWInstall', self.install_result)}</job>"
        if cmd == panos.RESTART:
            self.rebooting = self.boot_polls
            return "<msg>restarting</msg>"
        if cmd == panos.SUSPEND:
            self.pair.suspend(self)
            return "Successfully changed HA state to suspended"
        if cmd == panos.FUNCTIONAL:
            self.pair.functional(self)
            return "Successfully changed HA state to functional"
        if cmd == panos.INTERFACES:
            return (
                "<hw>"
                + "".join(
                    f"<entry><name>{n}</name><state>{s}</state></entry>" for n, s in self.interfaces.items()
                )
                + "</hw>"
            )
        if cmd == panos.ROUTES:
            return "".join(
                f"<entry><virtual-router>{vr}</virtual-router><destination>{d}</destination>"
                f"<nexthop>{n}</nexthop><interface>{i}</interface></entry>"
                for vr, d, n, i in self.routes
            )
        if cmd == panos.BGP_PEERS:
            return "".join(
                f'<entry peer="{p}" vr="default"><status>{s}</status></entry>' for p, s in self.bgp.items()
            )
        if cmd == panos.TUNNELS:
            return (
                "<IPSec>"
                + "".join(
                    f"<entry><name>{n}</name><state>{s}</state></entry>" for n, s in self.tunnels.items()
                )
                + "</IPSec>"
            )
        if cmd == panos.SESSIONS:
            return "<num-active>1000</num-active>"
        if cmd == panos.ARP:
            return "<max>2000</max><total>50</total>"
        if cmd == panos.RUNNING_CONFIG:
            return '<config version="10.2.0"><devices/></config>'
        raise AssertionError("unexpected command " + cmd)


class FakePair:
    def __init__(self, a, b):
        self.state = {a.serial: "active", b.serial: "passive"}
        a.pair = b.pair = self
        a.peer, b.peer = b, a

    def suspend(self, fw):
        if self.state[fw.serial] == "active":
            self.state[fw.peer.serial] = "active"
        self.state[fw.serial] = "suspended"

    def functional(self, fw):
        self.state[fw.serial] = "passive" if self.state[fw.peer.serial] == "active" else "active"


class FakePanorama:
    def __init__(self, *firewalls):
        self.firewalls = {f.serial: f for f in firewalls}
        self.reject_probe = False

    def devices(self):
        entries = []
        for fw in self.firewalls.values():
            ha = ""
            if fw.pair:
                ha = f"<ha><state>{fw.pair.state[fw.serial]}</state><peer><serial>{fw.peer.serial}</serial></peer></ha>"
            entries.append(
                f'<entry name="{fw.serial}"><serial>{fw.serial}</serial>'
                f"<connected>{'no' if fw.rebooting else 'yes'}</connected><model>{fw.model}</model>"
                f"<sw-version>{fw.version}</sw-version>{ha}</entry>"
            )
        return "<devices>" + "".join(entries) + "</devices>"

    def __call__(self, request):
        if request.method == "GET":
            assert request.headers["x-pan-key"] == "panorama-secret"
            if self.reject_probe:
                return httpx.Response(401)
            return httpx.Response(
                200,
                text='<response status="success"><result><sw-version>11.1.4</sw-version></result></response>',
            )
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        assert form["type"] == "op"
        if "target" not in form:
            assert form["cmd"] == panos.DEVICES
            body = self.devices()
        else:
            body = self.firewalls[form["target"]].handle(form["cmd"])
            if body is None:
                return httpx.Response(
                    200,
                    text='<response status="error"><msg><line>device is not connected</line></msg></response>',
                )
        return httpx.Response(200, text=f'<response status="success"><result>{body}</result></response>')


@pytest.fixture
def panorama(settings, store, monkeypatch):
    vault = vault_for(settings, store)
    vault.put(
        "panorama-key", Credential(kind="http", scheme="header", header="X-PAN-KEY", token="panorama-secret")
    )
    settings.http_targets["panorama"] = HTTPTarget(
        id="panorama",
        base_url="https://panorama.example",
        auth_probe_path="/api/",
        auth_probe_params={"type": "version"},
    )
    fake = FakePanorama()
    client = httpx.Client
    monkeypatch.setattr(
        "netops_executor.panos.httpx.Client",
        lambda **kwargs: client(transport=httpx.MockTransport(fake), **kwargs),
    )
    return fake


def submit(store, serial, **options):
    request = PanosUpgradeRequest(
        serial=serial, target_version=TARGET, allowed_source_versions=["10.2.9-h1"], **options
    )
    return store.submit(request.model_dump(mode="json"), "panos:" + serial, kind="panos_upgrade")


def work(settings, store, worker="w"):
    job = store.claim(worker)
    clock = Clock()
    PanosRunner(settings, store, job, worker, sleep=clock.sleep, clock=clock.clock).run()
    return store.get(job["id"])


def run(settings, store, serial, **options):
    submit(store, serial, **options)
    return work(settings, store)


def standalone(panorama, serial="0079510001"):
    fw = FakeFirewall(serial)
    panorama.firewalls[serial] = fw
    return fw


def test_standalone_upgrade_stages_base_image_and_validates(settings, store, panorama):
    fw = standalone(panorama)
    job = run(settings, store, fw.serial)
    assert job["status"] == "succeeded", job["result"]
    assert [c.split("<version>")[-1].split("<")[0] if "<version>" in c else c for c in fw.writes] == [
        "11.1.0",
        TARGET,
        TARGET,
        panos.RESTART,
    ]
    assert job["result"]["software"]["required"] == ["11.1.0", TARGET]
    assert job["result"]["boot_verified"]["version"] == TARGET
    assert job["result"]["validated_version"] == TARGET
    assert not job["lock_held"]
    backup = settings.backup_root / job["result"]["backup"]["path"]
    assert backup.read_bytes().startswith(b"<config")
    assert oct(backup.stat().st_mode & 0o777) == "0o600"
    assert job["credential_summary"]["profiles_used"] == ["panorama-key"]


@pytest.mark.parametrize(
    "change, error",
    [
        (lambda fw: setattr(fw, "version", "10.1.14"), "outside allowed_source_versions"),
        (lambda fw: setattr(fw, "pending", True), "uncommitted local changes"),
        (lambda fw: setattr(fw, "model", "PA-220"), "not approved"),
    ],
)
def test_prechecks_fail_before_any_write(settings, store, panorama, change, error):
    fw = standalone(panorama)
    change(fw)
    job = run(settings, store, fw.serial, approved_models=["PA-3220"])
    assert job["status"] == "failed"
    assert error in job["result"]["error"]
    assert fw.writes == [] and not job["lock_held"]


def test_unrestored_bgp_opens_gate_and_accept_continues_without_replay(settings, store, panorama):
    fw = standalone(panorama)
    original = fw.handle

    def handle(cmd):
        if fw.installed and not fw.rebooting:
            fw.bgp = {"isp1": "Idle"}
        return original(cmd)

    fw.handle = handle
    job = run(settings, store, fw.serial)
    assert job["status"] == "needs_review" and job["lock_held"]
    gate = job["validation_gates"][-1]
    assert gate["phase"] == TRAFFIC
    assert "bgp_peers not restored: default|isp1" in gate["findings"]["critical"]
    writes = list(fw.writes)
    store.decide(
        job["id"],
        ValidationDecision(
            gate_id=gate["id"],
            validation_revision=gate["revision"],
            operator="jason",
            reason="Peer is down upstream",
        ),
    )
    job = work(settings, store)
    assert job["status"] == "completed_with_warnings", job["result"]
    assert job["result"]["manual_validations"][0]["operator"] == "jason"
    assert fw.writes == writes and not job["lock_held"]


def test_ha_pair_upgrades_both_members_with_validated_failover(settings, store, panorama):
    a, b = FakeFirewall("0079510001"), FakeFirewall("0079510002")
    FakePair(a, b)
    panorama.firewalls.update({a.serial: a, b.serial: b})
    first = run(settings, store, a.serial)
    assert first["status"] == "succeeded", first["result"]
    assert a.version == TARGET and a.pair.state == {a.serial: "passive", b.serial: "active"}
    assert [w for w in a.writes if "high-availability" in w] == [panos.SUSPEND, panos.FUNCTIONAL]
    assert first["result"]["post_validation"]["traffic_source"] == b.serial
    second = run(settings, store, b.serial)
    assert second["status"] == "succeeded", second["result"]
    assert b.version == TARGET and a.pair.state[a.serial] == "active"
    assert second["result"]["ha_preflight"]["peer_version"] == TARGET


def test_mixed_version_failover_requires_validated_peer(settings, store, panorama):
    a, b = FakeFirewall("0079510001", version=TARGET), FakeFirewall("0079510002")
    FakePair(b, a)
    panorama.firewalls.update({a.serial: a, b.serial: b})
    job = run(settings, store, b.serial)
    assert job["status"] == "failed"
    assert "validated upgrade of the peer" in job["result"]["error"]
    assert b.writes == [] and not job["lock_held"]


def test_ha_install_failure_returns_unit_to_service(settings, store, panorama):
    a, b = FakeFirewall("0079510001"), FakeFirewall("0079510002")
    FakePair(a, b)
    panorama.firewalls.update({a.serial: a, b.serial: b})
    a.install_result = "FAIL"
    job = run(settings, store, a.serial)
    assert job["status"] == "failed", job["result"]
    assert "install of 11.1.6-h3 failed" in job["result"]["error"]
    assert a.writes[-1] == panos.FUNCTIONAL and a.pair.state[a.serial] == "passive"
    assert panos.RESTART not in a.writes and not job["lock_held"]


def test_unverified_reboot_requires_recovery(settings, store, panorama):
    fw = standalone(panorama)
    fw.boot_polls = 10**6
    job = run(settings, store, fw.serial, reboot_timeout=300)
    assert job["status"] == "recovery_required"
    assert job["lock_held"]
    submit(store, fw.serial)
    assert store.claim("next") is None


def test_already_on_target_validates_without_writes(settings, store, panorama):
    fw = standalone(panorama)
    fw.version = TARGET
    job = run(settings, store, fw.serial)
    assert job["status"] == "succeeded", job["result"]
    assert job["result"]["already_on_target"] and fw.writes == []


def test_stage_only_downloads_without_install(settings, store, panorama):
    fw = standalone(panorama)
    job = run(settings, store, fw.serial, stage_only=True)
    assert job["status"] == "succeeded", job["result"]
    assert (
        all(w.startswith("<request><system><software><download>") for w in fw.writes) and len(fw.writes) == 2
    )
    assert fw.software[TARGET] and fw.installed is None


def test_cancel_during_download_stops_before_install(settings, store, panorama):
    fw = standalone(panorama)
    fw.download_polls = 5
    job = submit(store, fw.serial)
    fw.on_download = lambda: store.cancel(job["id"])
    job = work(settings, store)
    assert job["status"] == "cancelled"
    assert fw.installed is None and not job["lock_held"]


def test_compare_gaps_thresholds_and_warning_mode():
    baseline = {
        "interfaces": {"ethernet1/1": "up", "ethernet1/2": "down"},
        "routes": ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"],
        "bgp_peers": {"default|isp": "established"},
        "ipsec_tunnels": {"t1": "active"},
        "sessions": 1000,
        "gaps": ["arp"],
    }
    current = {
        "interfaces": {"ethernet1/1": "down", "ethernet1/2": "up"},
        "routes": ["a", "b", "c", "d", "e", "f", "g", "h"],
        "bgp_peers": {"default|isp": "established"},
        "ipsec_tunnels": {},
        "sessions": 100,
        "gaps": ["arp"],
    }
    critical, findings, metrics = compare(baseline, current, PanosValidationPolicy(min_session_percent=50))
    assert "interfaces not restored: ethernet1/1" in critical
    assert "ipsec_tunnels not restored: t1" in critical
    assert any("route count 8" in c for c in critical) and any("sessions count" in c for c in critical)
    assert metrics["route_percent"] == 80
    critical, findings, _ = compare(
        baseline, current, PanosValidationPolicy(restore_required=False, min_route_percent=None)
    )
    assert critical == []
    assert {f["category"] for f in findings} >= {"interfaces", "ipsec_tunnels", "routes"}
    lost = {**current, "gaps": ["bgp_peers", "arp"]}
    assert (
        "bgp_peers could not be collected after the upgrade"
        in compare(baseline, lost, PanosValidationPolicy())[0]
    )


def test_uptime_parser():
    assert panos.uptime_seconds("12 days, 3:04:05") == ((12 * 24 + 3) * 60 + 4) * 60 + 5
    assert panos.uptime_seconds("0 days, 0:00:30") == 30
    assert panos.uptime_seconds("unknown") is None


def test_batch_api_queues_panos_waves(settings, store, panorama):
    client = TestClient(create_app(settings), headers={"Authorization": "Bearer " + settings.token})
    body = {
        "name": "Change456",
        "defaults": {"target_version": TARGET, "allowed_source_versions": ["10.2.9-h1"]},
        "overrides": {"0079510003": {"validation_policy": {"on_timeout": "fail"}}},
        "waves": [
            {"wave": 1, "upgrade_at_a_time": 2, "serials": ["0079510001", "0079510002"]},
            {"wave": 2, "upgrade_at_a_time": 20, "serials": ["0079510003"]},
        ],
    }
    response = client.post("/api/v1/panos/upgrade-batches", json=body, headers={"Idempotency-Key": "c456"})
    assert response.status_code == 202, response.text
    batch = response.json()
    assert batch["kind"] == "panos_upgrade" and batch["device_count"] == 3
    assert {j["kind"] for j in batch["jobs"]} == {"panos_upgrade"}
    again = client.post("/api/v1/panos/upgrade-batches", json=body, headers={"Idempotency-Key": "c456"})
    assert again.json()["id"] == batch["id"]
    claimed = [store.claim("w1"), store.claim("w2"), store.claim("w3")]
    assert sorted(j["device"] for j in claimed if j) == ["0079510001", "0079510002"]
    assert {j["lock_key"] for j in claimed if j} == {"panos:0079510001", "panos:0079510002"}
    item = store.get(next(j["id"] for j in batch["jobs"] if j["device"] == "0079510003"))
    assert item["request"]["validation_policy"]["on_timeout"] == "fail"
    plan = client.post("/api/v1/panos/plan", json={**body["defaults"], "serial": "0079510001"})
    assert plan.json()["steps"][-1] == "validate_traffic"
    bad = client.post(
        "/api/v1/panos/jobs", json={**body["defaults"], "serial": "0079510001", "panorama": "other"}
    )
    assert bad.status_code == 422
    with pytest.raises(ValueError):
        PanosBatch.model_validate({**body, "overrides": {"0079519999": {}}})


def test_request_normalizes_download_versions():
    request = PanosUpgradeRequest(
        serial="0079510001",
        target_version=TARGET,
        allowed_source_versions=["10.2.9-h1"],
        download_versions=["10.2.0", "11.0.0", "10.2.0", TARGET],
    )
    assert request.download_versions == ["10.2.0", "11.0.0"]
    with pytest.raises(ValueError):
        PanosUpgradeRequest(serial="0079510001", target_version="11.1", allowed_source_versions=["10.2.9"])


def test_missing_target_or_rejected_key_fails_job_not_worker(settings, store, panorama):
    fw = standalone(panorama)
    job = run(settings, store, fw.serial, panorama="removed")
    assert job["status"] == "failed" and "not configured: removed" in job["result"]["error"]
    panorama.reject_probe = True
    job = run(settings, store, fw.serial)
    assert job["status"] == "failed"
    assert job["result"]["error"] == "Panorama rejected every eligible HTTP credential"
    assert fw.writes == []
