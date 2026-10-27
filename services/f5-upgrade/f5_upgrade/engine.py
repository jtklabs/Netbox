import math
import os
import time

from .config import under
from .device import BigIP, compare, matches, name
from .models import JobRequest
from .ssh import SSH, DeviceError, digest
from .store import LostOwnership


class Runner:
    def __init__(self, settings, store, job, worker, device=None, sleep=time.sleep, clock=time.monotonic):
        self.settings, self.store, self.job, self.worker = settings, store, job, worker
        self.sleep, self.clock = sleep, clock
        self.result = job["result"]
        self.changed = bool(self.result.get("inherited_lock"))
        self.changed_addresses = []
        self.boot_attempted = False
        self.ssh = (
            SSH(settings, settings.devices[job["device"]], self.guard) if device is None else device.ssh
        )
        self.device = device or BigIP(self.ssh)

    def guard(self):
        self.store.guard(self.job["id"], self.worker)

    def event(self, step, **data):
        self.store.record(self.job["id"], self.worker, step, data, self.result)

    def intent(self, step, **data):
        # Persist BEFORE issuing a mutation. An interrupted operation is never automatically replayed.
        self.changed = True
        self.event(step + "_started", **data)

    def wait(self, seconds=5):
        self.guard()
        self.sleep(seconds)
        self.guard()

    def backup(self, request):
        d = self.settings.devices[request.device]
        passphrase = None if request.ucs_unencrypted else os.environ.get(d.ucs_passphrase_env)
        if not request.ucs_unencrypted and not passphrase:
            raise DeviceError("encrypted UCS requires the configured UCS passphrase environment variable")
        if passphrase and any(c in passphrase for c in "\r\n\x00"):
            raise DeviceError("UCS passphrase cannot contain newline or NUL")
        directory = under(self.settings.backup_root, request.backup_directory or request.device)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        filename = self.job["id"] + ".ucs"
        path = directory / filename
        remote = "/var/local/ucs/" + filename
        self.intent("save_config")
        self.ssh.tmsh("save /sys config", timeout=180)
        command = "save /sys ucs " + filename
        if passphrase:
            quoted = passphrase.replace("\\", "\\\\").replace('"', '\\"')
            command += ' passphrase "' + quoted + '"'
        self.intent("create_ucs", encrypted=not request.ucs_unencrypted)
        self.ssh.tmsh(command, timeout=1800, secret=bool(passphrase))
        self.event("copy_ucs_started")
        self.ssh.get(remote, path)
        self.result["backup"] = {
            "path": str(path.relative_to(self.settings.backup_root.resolve())),
            "sha256": digest(path),
            "encrypted": not request.ucs_unencrypted,
            "bytes": path.stat().st_size,
        }
        self.event("copy_ucs_completed")

    def images(self, request):
        for image in request.images:
            remote = "/shared/images/" + image.name
            if "transfer" in request.plan():
                local = under(self.settings.image_root, image.name)
                if not local.is_file() or digest(local) != image.sha256:
                    raise DeviceError("local image missing or SHA-256 mismatch: " + image.name)
                partial = "/shared/images/." + self.job["id"] + ".partial"
                self.intent("transfer", image=image.name)
                self.ssh.put(local, partial)
                if self.ssh.remote_digest(partial) != image.sha256:
                    raise DeviceError("transferred image SHA-256 mismatch")
                self.ssh.shell("mv -- " + name(partial) + " " + name(remote))
                self.event("transfer_completed", image=image.name)
            if self.ssh.remote_digest(remote) != image.sha256:
                raise DeviceError("device image SHA-256 mismatch: " + image.name)
            self.device.verify_image(image)
            self.event("image_verified", image=image.name)

    def install(self, request):
        for image in request.images:
            volumes = self.device.volumes()
            if any(v["active"] and v["volume"] == request.target_volume for v in volumes.values()):
                raise DeviceError("refusing to install on active volume")
            if matches(volumes.get(request.target_volume), image.version, image.build):
                self.event("install_already_complete", image=image.name, volume=request.target_volume)
                continue
            self.intent("install", image=image.name, volume=request.target_volume)
            self.device.install(image, request.target_volume, request.target_volume not in volumes)
            deadline = self.clock() + request.install_timeout
            while True:
                target = self.device.volumes().get(request.target_volume)
                if target and "fail" in target["status"].lower():
                    raise DeviceError("software installation failed")
                if matches(target, image.version, image.build):
                    break
                if self.clock() >= deadline:
                    raise DeviceError("software installation did not complete with expected version/build")
                self.wait(10)
            self.event("install_completed", image=image.name)

    def drain(self, request):
        self.device.check_drain()
        states = self.device.address_states()
        baseline = self.device.connections()
        threshold = (
            math.floor(baseline * (100 - request.drain_percent) / 100)
            if request.drain_percent is not None
            else (request.drain_connections or 0)
        )
        self.result["drain"] = {
            "initial_connections": baseline,
            "threshold": threshold,
            "address_states": states,
            "changed_addresses": [],
            "timed_out": False,
        }
        self.event("drain_snapshot")
        for address, enabled in states.items():
            if enabled:
                # Include the in-flight command in recovery, even if the SSH response is lost.
                self.changed_addresses.append(address)
                self.result["drain"]["changed_addresses"] = self.changed_addresses[:]
                self.intent("disable_virtual_address", address=address)
                self.device.set_enabled(address, False)
        deadline = self.clock() + request.drain_timeout
        while True:
            current = self.device.connections()
            self.result["drain"]["final_connections"] = current
            self.event("drain_sample", connections=current, threshold=threshold)
            if current <= threshold:
                return
            if self.clock() >= deadline:
                self.result["drain"]["timed_out"] = True
                self.event("drain_timeout", forced_reboot=request.reboot_on_drain_timeout)
                if not request.reboot_on_drain_timeout:
                    raise DeviceError("drain timed out; reboot was not authorized on timeout")
                return
            self.wait(min(5, max(0, deadline - self.clock())))

    def restore_addresses(self):
        for address in self.changed_addresses[:]:
            self.intent("restore_virtual_address", address=address)
            self.device.set_enabled(address, True)
            self.changed_addresses.remove(address)
        if "drain" in self.result:
            self.result["drain"]["restored"] = True
            self.event("virtual_addresses_restored")

    def reboot(self, volume, version, build, timeout):
        if not matches(self.device.volumes().get(volume), version, build):
            raise DeviceError("reboot target is not complete with expected version/build")
        if self.device.active()["volume"] == volume:
            raise DeviceError("reboot target is already active")
        old_boot = self.device.boot_id()
        self.boot_attempted = True
        self.intent("reboot", volume=volume)
        try:
            self.device.reboot(volume)
        except DeviceError:
            # Reboot can close SSH before returning. Only a NEW boot identity verifies it happened.
            self.event("reboot_response_uncertain")
        finally:
            self.ssh.close()
        deadline = self.clock() + timeout
        while self.clock() < deadline:
            self.wait(5)
            try:
                if self.device.boot_id() != old_boot:
                    active = self.device.active()
                    if (
                        active["volume"] == volume
                        and active["version"] == version
                        and active["build"] == build
                    ):
                        self.result["boot_verified"] = active
                        self.event("reboot_completed")
                        return
            except DeviceError:
                self.ssh.close()
        raise DeviceError(
            "new boot identity and expected volume/version/build were not verified before timeout"
        )

    def postvalidate(self, baseline, timeout):
        deadline = self.clock() + timeout
        while True:
            snapshot = self.device.snapshot()
            issues = compare(baseline, snapshot)
            self.result["post_validation"] = snapshot
            self.result["validation_issues"] = issues
            if not issues or self.clock() >= deadline:
                self.event("post_validation_completed", issues=issues)
                return not issues
            self.wait(min(10, max(0, deadline - self.clock())))

    def upgrade(self):
        r = JobRequest.model_validate(self.job["request"])
        self.result["plan"] = r.plan()
        before = self.device.snapshot()
        self.result["pre_validation"] = before
        self.event("pre_validation_completed")
        active = before["active"]
        if r.install or r.reboot:
            if active["version"] not in r.allowed_source_versions:
                raise DeviceError("running version is outside allowed_source_versions")
            if active["volume"] == r.target_volume:
                raise DeviceError("target volume must not be the active volume")
            if any(
                v["status"] != "complete" or (v.get("active_requested") and not v["active"])
                for v in self.device.volumes().values()
            ):
                raise DeviceError("another volume is not complete; inspect before upgrading")
            self.result["rollback"] = {
                "volume": active["volume"],
                "version": active["version"],
                "build": active["build"],
                "from_volume": r.target_volume,
                "from_version": r.expected_version,
                "from_build": r.expected_build,
            }
            self.event("rollback_recorded")
        # Check drain compatibility before saving/installing anything.
        if r.drain:
            self.device.check_drain()
            self.device.address_states()
        if "create_ucs" in r.plan():
            self.backup(r)
        if r.transfer or r.install:
            self.images(r)
        if r.install:
            self.install(r)
        if r.drain:
            self.drain(r)
        if r.reboot:
            self.reboot(r.target_volume, r.expected_version, r.expected_build, r.reboot_timeout)
            self.restore_addresses()
        return self.postvalidate(before, r.validation_timeout if r.reboot else 0)

    def rollback(self):
        source = self.store.get(self.job["parent"])
        saved = source["result"]["rollback"]
        baseline = source["result"]["pre_validation"]
        current = self.device.snapshot()
        self.result["pre_validation"] = current
        self.result["rollback_target"] = saved
        self.event("rollback_pre_validation")
        active = current["active"]
        if (active["volume"], active["version"], active["build"]) != (
            saved["from_volume"],
            saved["from_version"],
            saved["from_build"],
        ):
            raise DeviceError("device no longer runs the source job's target; rollback refused")
        self.reboot(saved["volume"], saved["version"], saved["build"], self.job["request"]["reboot_timeout"])
        # A partial drain from the source job may have survived a manually resolved failure.
        self.changed_addresses = source["result"].get("drain", {}).get("changed_addresses", [])[:]
        self.restore_addresses()
        return self.postvalidate(baseline, self.job["request"]["validation_timeout"])

    def run(self):
        try:
            passed = self.rollback() if self.job["kind"] == "rollback" else self.upgrade()
            self.store.finish(
                self.job["id"],
                self.worker,
                "succeeded" if passed else "failed",
                retain_lock=not passed and self.changed,
            )
        except LostOwnership:
            # Do not issue cleanup mutations after revocation; recovery retains the device lock.
            pass
        except Exception as exc:
            message = (
                str(exc) if isinstance(exc, DeviceError) else "operation failed (" + type(exc).__name__ + ")"
            )
            self.result["error"] = message
            try:
                if self.changed_addresses and not self.boot_attempted:
                    try:
                        self.restore_addresses()
                    except Exception:
                        self.result["address_restore_failed"] = True
                self.event("failed", message=message)
                self.store.finish(
                    self.job["id"], self.worker, "recovery_required" if self.changed else "failed"
                )
            except LostOwnership:
                pass
        finally:
            self.ssh.close()
