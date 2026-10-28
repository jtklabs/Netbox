"""Unattended PAN-OS upgrades through Panorama. Ambiguous writes are never replayed."""

import os
import time
from datetime import datetime, timezone

from .config import under
from .panos import (
    FUNCTIONAL,
    RESTART,
    SUSPEND,
    Firewall,
    Panorama,
    PanosError,
    PanosUnavailable,
    download,
    install,
)
from .panos_models import PanosUpgradeRequest, feature_release
from .ssh import digest
from .store import CancelRequested, Deferred, LostOwnership, NeedsReview

SYSTEM, TRAFFIC = "panos_system_validation", "panos_traffic_validation"
TRAFFIC_CATEGORIES = ("interfaces", "routes", "bgp_peers", "ipsec_tunnels", "sessions", "arp")
# Values that mean an object carries traffic; baseline objects in this state must recover.
RESTORED = {"interfaces": {"up"}, "bgp_peers": {"established"}, "ipsec_tunnels": {"active"}}


def compare(baseline, current, policy):
    """Return critical failures, reportable differences and metrics for two traffic snapshots."""
    critical, findings, metrics = [], [], {}
    skipped = set(baseline.get("gaps", []))
    for category in TRAFFIC_CATEGORIES:
        if category in skipped:
            continue
        if category in current.get("gaps", []):
            critical.append(category + " could not be collected after the upgrade")
            continue
        before, after = baseline[category], current[category]
        if category in RESTORED:
            up = RESTORED[category]
            lost = sorted(k for k, v in before.items() if v in up and after.get(k) not in up)
            changed = sorted(
                k for k in set(before) | set(after) if before.get(k) != after.get(k) and k not in lost
            )
            if lost:
                item = category + " not restored: " + ", ".join(lost[:20])
                if policy.restore_required:
                    critical.append(item)
                else:
                    findings.append({"category": category, "change": "not restored", "objects": lost[:100]})
            if changed:
                findings.append({"category": category, "change": "state changed", "objects": changed[:100]})
        elif category == "routes":
            removed, added = sorted(set(before) - set(after)), sorted(set(after) - set(before))
            percent = 100 * len(after) / len(before) if before else 100
            metrics["route_percent"] = round(percent, 1)
            if policy.min_route_percent is not None and percent < policy.min_route_percent:
                critical.append(
                    f"route count {len(after)} is below {policy.min_route_percent}% of {len(before)}"
                )
            if removed:
                findings.append({"category": "routes", "change": "removed", "objects": removed[:100]})
            if added:
                findings.append({"category": "routes", "change": "added", "objects": added[:100]})
        else:
            minimum = policy.min_session_percent if category == "sessions" else policy.min_arp_percent
            percent = 100 * after / before if before else 100
            metrics[category + "_percent"] = round(percent, 1)
            # Counts always move; they only fail an explicit threshold.
            if minimum is not None and percent < minimum:
                critical.append(f"{category} count {after} is below {minimum}% of {before}")
    return critical, findings, metrics


class PanosRunner:
    def __init__(self, settings, store, job, worker, panorama=None, sleep=time.sleep, clock=time.monotonic):
        self.settings, self.store, self.job, self.worker = settings, store, job, worker
        self.sleep, self.clock = sleep, clock
        self.result = job["result"]
        self.phase = "connect"
        self.request = self.firewall = self.peer = None
        self.panorama = panorama

    def setup(self):
        # Inside run(): a removed Panorama target or credential fails this job, never the worker.
        self.request = PanosUpgradeRequest.model_validate(self.job["request"])
        if self.panorama is None:
            if self.request.panorama not in self.settings.http_targets:
                raise PanosError("Panorama HTTP target is not configured: " + self.request.panorama)
            self.panorama = Panorama(
                self.settings,
                self.store,
                self.request.panorama,
                self.request.credential_profiles,
                self.guard,
                self.audit,
            )
        self.firewall = Firewall(self.panorama, self.request.serial)

    def guard(self):
        self.store.guard(self.job["id"], self.worker)

    def checkpoint(self):
        self.store.checkpoint(self.job["id"], self.worker)

    def audit(self, data):
        self.store.credential_event(self.job["id"], self.worker, {**data, "phase": self.phase})

    def event(self, step, **data):
        self.phase = step
        self.store.record(self.job["id"], self.worker, step, data, self.result)

    def intent(self, step, **data):
        # Persist BEFORE issuing a disruptive write. An interrupted operation is never replayed.
        self.result.setdefault("mutations", []).append(step)
        self.event(step + "_started", **data)

    def wait(self, seconds):
        self.guard()
        self.sleep(seconds)
        self.guard()

    @property
    def clustered(self):
        return bool(self.result.get("ha", {}).get("enabled"))

    def peer_firewall(self):
        if self.peer is None:
            self.peer = Firewall(self.panorama, self.result["ha"]["peer"])
        return self.peer

    def unsafe(self):
        # A staged image boots on the next restart; a suspended unit carries no traffic.
        return bool(
            self.result.get("ha_suspended")
            or self.result.get("reboot_requested_at")
            or self.result.get("install", {}).get("status") in ("started", "completed")
        )

    def reserve(self):
        self.phase = "discover_ha"
        serial = self.request.serial
        # A review continuation keeps its recorded pair: a suspended or rebooting unit may be
        # unreachable, and validation samples recheck HA state anyway.
        topology = self.result.get("ha")
        if topology is None:
            entry = self.panorama.device(serial)
            if entry is None:
                raise PanosError("firewall is not managed by this Panorama")
            if not entry["connected"]:
                raise PanosError("firewall is not connected to Panorama")
            ha = self.firewall.ha_state()
            peer = entry["ha_peer"] if ha["enabled"] else None
            if ha["enabled"] and not peer:
                raise PanosError("HA is enabled but Panorama does not report the peer serial")
            topology = {"enabled": ha["enabled"], "peer": peer, "mode": ha["mode"]}
        peer = topology["peer"]
        keys = [self.job["lock_key"], "panos:" + serial]
        if peer:
            keys += ["panos:" + peer, "panos-ha:" + ":".join(sorted((serial, peer)))]
        if not self.store.reserve(self.job["id"], self.worker, keys):
            if self.result.get("mutations"):
                raise PanosError("reservation changed during continuation")
            self.store.defer(self.job["id"], self.worker)
        self.result["ha"] = topology
        self.event("resources_reserved", keys=keys)

    def active_firewall(self, ha):
        if not ha["enabled"] or ha["local"] == "active":
            return self.firewall
        if ha["peer_state"] == "active":
            return self.peer_firewall()
        raise PanosError("no active HA member is available for traffic validation")

    def traffic(self, firewall):
        data, gaps = {}, []
        for category, read in (
            ("interfaces", firewall.interfaces),
            ("routes", firewall.routes),
            ("bgp_peers", firewall.bgp_peers),
            ("ipsec_tunnels", firewall.tunnels),
            ("sessions", firewall.session_count),
            ("arp", firewall.arp_count),
        ):
            try:
                data[category] = read()
            except PanosUnavailable:
                raise
            except PanosError:
                # An unsupported or unconfigured feature is an explicit gap, never an empty pass.
                gaps.append(category)
        data["gaps"] = gaps
        return data

    def ha_preflight(self, failover=True):
        r = self.request
        ha = self.firewall.ha_state()
        if not ha["enabled"]:
            return ha
        if ha["mode"] != "active-passive":
            raise PanosError("only active/passive HA pairs are supported")
        if ha["peer_conn"] != "up":
            raise PanosError("HA peer connection is not up")
        if sorted([ha["local"], ha["peer_state"]]) != ["active", "passive"]:
            raise PanosError("HA requires one active and one passive member")
        peer = self.panorama.device(self.result["ha"]["peer"])
        if not peer or not peer["connected"]:
            raise PanosError("HA peer is not connected to Panorama")
        local_version = self.firewall.system_info()["sw_version"]
        same = local_version == peer["sw_version"]
        if same and r.ha_policy.require_running_sync and ha["running_sync"] != "synchronized":
            raise PanosError("HA running configuration is not synchronized")
        if failover and ha["local"] == "active":
            if not r.ha_policy.allow_active_first:
                raise PanosError("active-first upgrade is disabled by this request")
            if (
                not same
                and r.ha_policy.mixed_version_failover == "require_validated_peer"
                and not self.store.validated_panos_peer(self.job["id"], peer["serial"], peer["sw_version"])
            ):
                raise PanosError("mixed-version failover needs a validated upgrade of the peer")
        self.result["ha_preflight"] = {
            "local": ha["local"],
            "peer": peer["serial"],
            "peer_version": peer["sw_version"],
            "running_sync": ha["running_sync"],
            "preemptive": ha["preemptive"],
        }
        self.event("ha_preflight_completed")
        return ha

    def wait_for_jobs(self):
        deadline = self.clock() + self.request.job_wait_timeout
        while True:
            running = [j for j in self.firewall.jobs() if j["status"] in ("ACT", "PEND")]
            if not running:
                return
            if self.clock() >= deadline:
                raise PanosError("firewall jobs are still running: " + ", ".join(j["type"] for j in running))
            self.event(
                "waiting_for_firewall_jobs", jobs=[{"id": j["id"], "type": j["type"]} for j in running]
            )
            self.checkpoint()
            self.wait(15)

    def prevalidate(self):
        r = self.request
        info = self.firewall.system_info()
        if info["serial"] and info["serial"] != r.serial:
            raise PanosError("firewall reported a different serial")
        if info["operational_mode"] not in ("", "normal"):
            raise PanosError("firewall operational mode is " + info["operational_mode"])
        self.result["source"] = {
            "version": info["sw_version"],
            "model": info["model"],
            "hostname": info["hostname"],
            "app_version": info["app_version"],
        }
        if r.approved_models and info["model"] not in r.approved_models:
            raise PanosError("hardware model is not approved: " + info["model"])
        upgrading = info["sw_version"] != r.target_version
        if upgrading and info["sw_version"] not in r.allowed_source_versions:
            raise PanosError("running version " + info["sw_version"] + " is outside allowed_source_versions")
        if not self.firewall.chassis_ready():
            raise PanosError("chassis is not ready")
        self.wait_for_jobs()
        if self.firewall.pending_changes():
            raise PanosError("firewall has uncommitted local changes")
        # Fail before backup or download when the later HA failover would be refused.
        ha = self.ha_preflight(failover=upgrading and not r.stage_only)
        active = self.active_firewall(ha)
        self.result["pre_validation"] = {
            "unit": info,
            "traffic": self.traffic(active),
            "traffic_source": active.serial,
        }
        self.event("pre_validation_completed", gaps=self.result["pre_validation"]["traffic"]["gaps"])
        return info

    def backup(self):
        r = self.request
        if not r.config_backup:
            return
        directory = under(self.settings.backup_root, r.backup_directory or "panos/" + r.serial)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = directory / (self.job["id"] + ".xml")
        self.event("config_backup_started")
        data = self.firewall.running_config()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        self.result["backup"] = {
            "path": str(path.relative_to(self.settings.backup_root.resolve())),
            "sha256": digest(path),
            "bytes": path.stat().st_size,
            "format": "panos-running-config-xml",
        }
        self.event("config_backup_completed")

    def wait_job(self, job_id, timeout, label, cancellable):
        deadline = self.clock() + timeout
        last = None
        while True:
            if cancellable:
                self.checkpoint()
            try:
                job = self.firewall.job(job_id)
                if job["status"] == "FIN":
                    return job
                if job["progress"] != last:
                    last = job["progress"]
                    self.event(label + "_progress", job=job_id, progress=last)
            except PanosError:
                # Panorama may briefly lose the firewall; the job keeps running on the device.
                pass
            if self.clock() >= deadline:
                raise PanosError(label + " job " + str(job_id) + " did not finish before timeout")
            self.wait(10)

    def stage(self):
        r = self.request
        self.phase = "software_check"
        self.checkpoint()
        versions = self.firewall.software(refresh=True)
        needed = list(r.download_versions)
        current = self.result["source"]["version"]
        base = feature_release(r.target_version) + ".0"
        if r.auto_base_image and feature_release(current) != feature_release(r.target_version):
            if r.target_version != base and base not in needed:
                needed.insert(0, base)
        needed.append(r.target_version)
        missing = [v for v in needed if v not in versions]
        if missing:
            raise PanosError("software not offered to this firewall: " + ", ".join(missing))
        self.result["software"] = {
            "required": needed,
            "already_downloaded": [v for v in needed if versions[v]["downloaded"]],
        }
        self.event("software_check_completed")
        for version in needed:
            if versions[version]["downloaded"]:
                continue
            self.checkpoint()
            self.event("software_download_started", version=version)
            job_id = self.firewall.enqueue(download(version))
            job = self.wait_job(job_id, r.download_timeout, "software_download", cancellable=True)
            if job["result"] != "OK":
                raise PanosError("download of " + version + " failed: " + job["details"])
            self.event("software_download_completed", version=version, job=job_id)
        versions = self.firewall.software()
        missing = [v for v in needed if not versions.get(v, {}).get("downloaded")]
        if missing:
            raise PanosError("software is not reported as downloaded: " + ", ".join(missing))
        self.event("software_staged", versions=needed)

    def wait_ha(self, done, timeout, error):
        deadline = self.clock() + timeout
        while True:
            try:
                ha = self.firewall.ha_state()
                if done(ha):
                    return ha
            except PanosError:
                pass
            if self.clock() >= deadline:
                raise PanosError(error)
            self.wait(5)

    def suspend(self):
        if not self.clustered:
            return
        ha = self.ha_preflight()
        self.checkpoint()
        self.result["ha_suspended"] = True
        self.intent("ha_suspend", previous_state=ha["local"])
        self.firewall.command(SUSPEND)
        self.wait_ha(
            lambda h: h["local"] == "suspended" and h["peer_state"] == "active",
            300,
            "HA suspend did not complete with the peer active",
        )
        self.event("ha_suspended")

    def return_to_service(self):
        if not self.result.get("ha_suspended"):
            return
        self.intent("ha_functional")
        self.firewall.command(FUNCTIONAL)
        self.wait_ha(
            lambda h: h["local"] in ("active", "passive") and h["peer_conn"] == "up",
            600,
            "firewall did not return to a functional HA state",
        )
        self.result["ha_suspended"] = False
        self.event("ha_functional")

    def install(self):
        r = self.request
        self.checkpoint()
        self.result["install"] = {"version": r.target_version, "status": "started"}
        self.intent("install", version=r.target_version)
        job_id = self.firewall.enqueue(install(r.target_version))
        self.result["install"]["job"] = job_id
        self.event("install_enqueued", job=job_id)
        # An install cannot safely be interrupted. Observe completion before honoring cancellation.
        job = self.wait_job(job_id, r.install_timeout, "install", cancellable=False)
        if job["result"] != "OK":
            self.result["install"]["status"] = "failed"
            raise PanosError("install of " + r.target_version + " failed: " + job["details"])
        self.result["install"]["status"] = "completed"
        self.event("install_completed", job=job_id)
        self.checkpoint()

    def reboot(self):
        r = self.request
        self.checkpoint()
        self.result["reboot_requested_at"] = datetime.now(timezone.utc).isoformat()
        start = self.clock()
        self.intent("reboot", version=r.target_version)
        try:
            self.firewall.command(RESTART)
        except PanosUnavailable:
            # Restart can drop the proxied session. Only a new boot on the target verifies it.
            self.event("reboot_response_uncertain")
        deadline = start + r.reboot_timeout
        while self.clock() < deadline:
            self.wait(15)
            try:
                info = self.firewall.system_info()
            except PanosError:
                continue
            uptime = info["uptime_seconds"]
            if (
                info["sw_version"] == r.target_version
                and uptime is not None
                and uptime <= self.clock() - start + 120
            ):
                self.result["boot_verified"] = {"version": info["sw_version"], "uptime_seconds": uptime}
                self.event("reboot_completed")
                return
        raise PanosError("a new boot on the target version was not verified before timeout")

    def validate(self, phase, sample):
        policy, timeout = self.request.validation_policy, self.request.validation_timeout
        deadline = self.clock() + timeout
        passes = 0
        while True:
            self.checkpoint()
            try:
                critical, findings = sample()
            except PanosError as exc:
                critical, findings = ["device unavailable: " + str(exc)], []
            review = bool(findings) and policy.noncritical_differences == "require_review"
            self.result.setdefault("validation_findings", {})[phase] = findings
            self.result["validation_differences"] = [
                f for items in self.result["validation_findings"].values() for f in items
            ]
            self.result["validation_issues"] = critical
            passes = passes + 1 if not critical and not review else 0
            self.event(
                phase + "_sample",
                critical=critical,
                difference_count=len(findings),
                consecutive_passes=passes,
            )
            if passes >= (policy.consecutive_passes if timeout else 1):
                return
            if self.clock() >= deadline:
                if policy.on_timeout == "fail":
                    raise PanosError(phase + " timed out; findings recorded")
                self.store.open_gate(
                    self.job["id"],
                    self.worker,
                    phase,
                    {"critical": critical, "differences": findings},
                    self.result,
                )
            self.wait(min(policy.sample_interval_seconds, max(0, deadline - self.clock())))

    def system_sample(self):
        r = self.request
        critical, findings = [], []
        info = self.firewall.system_info()
        if info["sw_version"] != r.target_version:
            critical.append("running version is " + info["sw_version"])
        if info["operational_mode"] not in ("", "normal"):
            critical.append("operational mode is " + info["operational_mode"])
        if not self.firewall.chassis_ready():
            critical.append("chassis is not ready")
        commits = sorted(
            (j for j in self.firewall.jobs() if j["type"].lower() == "autocom"), key=lambda j: j["id"]
        )
        if not commits:
            findings.append({"category": "auto_commit", "change": "no auto-commit job reported"})
        elif commits[-1]["status"] != "FIN":
            critical.append("auto-commit is still running")
        elif commits[-1]["result"] != "OK":
            critical.append("auto-commit failed: " + commits[-1]["details"][:300])
        entry = self.panorama.device(r.serial)
        if not entry or not entry["connected"]:
            critical.append("firewall is not connected to Panorama")
        elif entry["sw_version"] != r.target_version:
            critical.append("Panorama reports version " + entry["sw_version"])
        baseline = self.result["pre_validation"]["unit"]
        for key in ("app_version", "threat_version", "av_version"):
            if baseline.get(key) and info.get(key) and baseline[key] != info[key]:
                findings.append({"category": "content", "change": key + " changed", "objects": [info[key]]})
        self.result["post_system"] = info
        return critical, findings

    def traffic_sample(self):
        r = self.request
        critical = []
        ha = self.firewall.ha_state()
        if ha["enabled"]:
            if ha["local"] not in ("active", "passive"):
                critical.append("HA state is " + (ha["local"] or "unknown"))
            if ha["peer_conn"] != "up":
                critical.append("HA peer connection is " + (ha["peer_conn"] or "unknown"))
            if sorted([ha["local"], ha["peer_state"]]) != ["active", "passive"]:
                critical.append("HA requires one active and one passive member")
            if (
                ha["local_version"]
                and ha["local_version"] == ha["peer_version"]
                and r.ha_policy.require_running_sync
                and ha["running_sync"] != "synchronized"
            ):
                critical.append("HA running configuration is not synchronized")
            if critical:
                return critical, []
        active = self.active_firewall(ha)
        current = self.traffic(active)
        failures, findings, metrics = compare(
            self.result["pre_validation"]["traffic"], current, r.validation_policy
        )
        self.result["post_validation"] = {
            "traffic": current,
            "traffic_source": active.serial,
            "metrics": metrics,
        }
        return critical + failures, findings

    def settle(self):
        remaining = self.request.validation_policy.settling_seconds
        while remaining > 0:
            self.checkpoint()
            self.wait(min(15, remaining))
            remaining -= min(15, remaining)

    def accept(self, version):
        self.result["validated_version"] = version
        self.result["validation_accepted"] = True

    def upgrade(self):
        self.setup()
        r = self.request
        self.reserve()
        self.checkpoint()
        decision = self.store.apply_decision(self.job["id"], self.worker)
        if decision:
            accepted = decision["decision"]["action"] == "accept"
            if accepted:
                self.result.setdefault("manual_validations", []).append(decision["decision"])
                self.event("manual_validation_applied", gate_id=decision["id"])
            if decision["phase"] == SYSTEM:
                if not accepted:
                    self.validate(SYSTEM, self.system_sample)
                self.return_to_service()
                self.validate(TRAFFIC, self.traffic_sample)
            elif decision["phase"] == TRAFFIC and not accepted:
                self.validate(TRAFFIC, self.traffic_sample)
            self.accept(r.target_version)
            return
        self.result["plan"] = r.plan()
        info = self.prevalidate()
        if info["sw_version"] == r.target_version:
            self.result["already_on_target"] = True
            self.event("already_on_target")
            self.validate(TRAFFIC, self.traffic_sample)
            self.accept(r.target_version)
            return
        self.backup()
        self.stage()
        if r.stage_only:
            return
        self.suspend()
        self.install()
        self.reboot()
        self.settle()
        self.validate(SYSTEM, self.system_sample)
        self.return_to_service()
        self.validate(TRAFFIC, self.traffic_sample)
        self.accept(r.target_version)

    def restore_service(self):
        # Before reboot, an unchanged running image can return to service. After reboot, an
        # unvalidated unit stays suspended so its peer keeps carrying traffic.
        if self.result.get("ha_suspended") and not self.result.get("reboot_requested_at"):
            self.return_to_service()

    def run(self):
        try:
            self.upgrade()
            self.result["completed"] = True
            self.event("completed")
            audit = self.store.get(self.job["id"])["credential_summary"]
            warnings = bool(
                self.result.get("validation_differences")
                or self.result.get("manual_validations")
                or audit.get("multiple_profiles_used")
            )
            self.store.finish(
                self.job["id"], self.worker, "completed_with_warnings" if warnings else "succeeded"
            )
        except (LostOwnership, Deferred, NeedsReview):
            pass
        except CancelRequested:
            try:
                self.restore_service()
                self.result["cancelled_at"] = self.phase
                self.event("cancel_acknowledged")
                self.store.finish(
                    self.job["id"], self.worker, "recovery_required" if self.unsafe() else "cancelled"
                )
            except LostOwnership:
                pass
            except Exception:
                self.store.finish(self.job["id"], self.worker, "recovery_required")
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, PanosError) else "operation failed (" + type(exc).__name__ + ")"
            )
            self.result["error"] = message
            try:
                try:
                    self.restore_service()
                except LostOwnership:
                    raise
                except Exception:
                    self.result["ha_restore_failed"] = True
                self.event("failed", message=message)
                self.store.finish(
                    self.job["id"], self.worker, "recovery_required" if self.unsafe() else "failed"
                )
            except LostOwnership:
                pass
        finally:
            if self.panorama is not None:
                self.panorama.close()
