"""BIG-IP upgrades against an in-memory iControl REST double; never a real unit."""

import hashlib
import json
import re
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops.upgrade import f5, f5_workflow, report, scheduler, workflow
from netops.upgrade.profile import Profile, f5_version

ISO = "BIGIP-17.5.1.8-0.0.19.iso"
IMAGE_BYTES = b"iso-image-content" * 200
MD5 = hashlib.md5(IMAGE_BYTES).hexdigest()
F5_IMAGE = re.compile(r"^(?:Hotfix-)?BIGIP-(\d+(?:\.\d+){2,3})-")


def entries(**values):
    return {"entries": {"https://localhost/x/0": {"nestedStats": {"entries": {k: {"description": v} for k, v in values.items()}}}}}


def stats(rows, mapping):
    return {"entries": {f"https://localhost/x/{i}": {"nestedStats": {"entries": {source: {"description": row[key]}
                                                                                  for key, source in mapping.items()}}}
                        for i, row in enumerate(rows)}}


class FakeBigIP:
    """One unit whose state survives reconnects; also the Client factory."""

    def __init__(self, version="17.1.1.3", failover="standby", peers=("bigip-b",), sync="In Sync",
                 license_date="2025/03/01", model="BIG-IP i5800"):
        self.version, self.failover, self.peers, self.sync = version, failover, list(peers), sync
        self.license_date, self.model = license_date, model
        self.volumes = [{"name": "HD1.1", "active": True, "version": version, "build": "0.0.5", "status": "complete", "product": "BIG-IP"},
                        {"name": "HD1.2", "active": False, "version": "16.1.4", "build": "0.0.9", "status": "complete", "product": "BIG-IP"}]
        self.images = {}
        self.installing = None
        self.calls, self.ucs, self.logins, self.saved, self.failovers, self.rebooted_to = [], [], 0, 0, 0, None
        self.down_until = 0
        self.reboot_downtime = 0.05
        self.virtual_availability = "available"
        self.session = SimpleNamespace(request=self._raw, headers={})
        self.base, self.verify_tls, self.timeout, self.token = "https://192.0.2.10:443", False, 30, "tok"

    def __call__(self, host, **settings):
        self.settings = settings
        return self

    def __enter__(self):
        if time.monotonic() < self.down_until:
            raise RuntimeError("connection refused")
        self.logins += 1
        return self

    def __exit__(self, *args):
        pass

    def save_config(self):
        self.saved += 1
        return {}

    def get_json(self, path):
        return self.request("GET", path)

    def request(self, method, path, payload=None, timeout=None):
        self.calls.append((method, path, payload))
        if method == "GET":
            return self._get(path)
        if method == "POST":
            return self._post(path, payload or {})
        if method == "DELETE":
            self.images.pop(path.rsplit("/", 1)[-1], None)
            return {}
        raise RuntimeError(f"unexpected {method} {path}")

    def _get(self, path):
        if path == "/mgmt/tm/cm/device":
            me = {"name": "bigip-a", "hostname": "bigip-a.example", "selfDevice": "true", "marketingName": self.model,
                  "platformId": "C119", "chassisId": "f5-00000001", "version": self.version, "build": "0.0.5",
                  "failoverState": self.failover}
            return {"items": [me] + [{"name": p, "selfDevice": "false", "failoverState": "active" if self.failover == "standby" else "standby",
                                      "version": self.version} for p in self.peers]}
        if path == "/mgmt/tm/sys/software/volume":
            self._advance_install()
            return {"items": [dict(v) for v in self.volumes]}
        if path.startswith("/mgmt/tm/sys/software/volume/"):
            self._advance_install()
            name = path.rsplit("/", 1)[-1]
            return next(dict(v) for v in self.volumes if v["name"] == name)
        if path == "/mgmt/tm/sys/software/image":
            return {"items": [{"name": n, "version": F5_IMAGE.match(n)[1], "verified": i["verified"], "fileSize": f"{len(i['bytes'])} B"}
                              for n, i in self.images.items()]}
        if path == "/mgmt/tm/sys/software/hotfix":
            return {"items": []}
        if path == "/mgmt/tm/cm/sync-status":
            return entries(status=self.sync, color="green" if self.sync == "In Sync" else "yellow")
        if path == "/mgmt/tm/sys/license":
            return entries(serviceCheckDate=self.license_date) if self.license_date else {"entries": {}}
        if path == "/mgmt/tm/sys/version":
            return entries(Version=self.version, Build="0.0.5", Product="BIG-IP")
        if path == "/mgmt/tm/sys/provision":
            return {"items": [{"name": "ltm", "level": "nominal"}, {"name": "asm", "level": "none"}]}
        if path == "/mgmt/tm/net/vlan":
            return {"items": [{"name": "/Common/external", "tag": 4093}, {"name": "/Common/internal", "tag": 4094}]}
        if path == "/mgmt/tm/net/self":
            return {"items": [{"name": "/Common/ext-self", "address": "198.51.100.10/24", "vlan": "/Common/external"}]}
        if path == "/mgmt/tm/net/route":
            return {"items": [{"name": "/Common/default", "network": "default", "gw": "198.51.100.1"}]}
        if path == "/mgmt/tm/net/trunk":
            return {"items": [{"name": "trunk1", "interfaces": ["1.1", "1.2"]}]}
        if path == "/mgmt/tm/ltm/rule":
            return {"items": [{"name": "/Common/_sys_https_redirect"}]}
        if path == "/mgmt/tm/sys/file/ssl-cert":
            return {"items": [{"name": "/Common/default.crt", "expirationString": "Jan 1 00:00:00 2030 GMT"}]}
        if path == "/mgmt/tm/ltm/virtual/stats":
            rows = [{"name": "/Common/vs-web", "availability": self.virtual_availability, "enabled": "enabled"},
                    {"name": "/Common/vs-api", "availability": "available", "enabled": "enabled"}]
            return stats(rows, f5.STATS["virtual_servers"][1])
        if path == "/mgmt/tm/ltm/pool/stats":
            return stats([{"name": "/Common/pool-web", "availability": "available", "enabled": "enabled", "active_members": 2}], f5.STATS["pools"][1])
        if path == "/mgmt/tm/ltm/node/stats":
            return stats([{"name": "/Common/10.0.0.1", "availability": "available", "enabled": "enabled"}], f5.STATS["nodes"][1])
        if path == "/mgmt/tm/net/interface/stats":
            return stats([{"name": "1.1", "status": "up"}, {"name": "1.2", "status": "up"}, {"name": "mgmt", "status": "up"}], f5.STATS["interfaces"][1])
        return {"items": []}

    def _advance_install(self):
        if self.installing:
            name, polls = self.installing
            volume = next(v for v in self.volumes if v["name"] == name)
            if polls > 0:
                volume["status"] = f"installing {50 * (3 - polls)}.000 pct"
                self.installing = (name, polls - 1)
            else:
                volume.update(status="complete", version=self.pending_version)
                self.installing = None

    def _post(self, path, payload):
        if path == "/mgmt/tm/util/bash":
            command = payload.get("utilCmdArgs", "")
            if "df -Pk" in command:
                return {"commandResult": "/dev/mapper/vg--db--sda-dat.share 30000000 100000 29900000 1% /shared\n"}
            match = re.search(r"md5sum '([^']+)'", command)
            if match:
                name = match[1].rsplit("/", 1)[-1]
                if name in self.images:
                    return {"commandResult": f"{hashlib.md5(bytes(self.images[name]['bytes'])).hexdigest()}  {match[1]}\n"}
                if name in self.ucs:
                    return {"commandResult": f"{hashlib.md5(b'ucs').hexdigest()}  {match[1]}\n"}
                return {"commandResult": f"md5sum: {match[1]}: No such file or directory\n"}
            return {"commandResult": ""}
        if path == "/mgmt/tm/sys/software/image" and payload.get("command") == "install":
            name = payload["volume"]
            if not any(v["name"] == name for v in self.volumes):
                assert payload.get("options") == [{"create-volume": True}]
                self.volumes.append({"name": name, "active": False, "version": "", "build": "", "status": "complete", "product": "BIG-IP"})
            self.pending_version = F5_IMAGE.match(payload["name"])[1]
            self.installing = (name, 2)
            return {}
        if path == "/mgmt/tm/sys" and payload.get("command") == "reboot":
            self.rebooted_to = payload["options"][0]["volume"]
            for volume in self.volumes:
                volume["active"] = volume["name"] == self.rebooted_to
                if volume["active"]:
                    self.version = volume["version"]
            self.down_until = time.monotonic() + self.reboot_downtime
            raise RuntimeError("F5 POST /mgmt/tm/sys: connection failed (ConnectionError)")
        if path == "/mgmt/tm/sys/failover":
            self.failovers += 1
            self.failover = "standby"
            return {}
        if path == "/mgmt/tm/sys/ucs":
            self.ucs.append(payload["name"])
            return {}
        raise RuntimeError(f"unexpected POST {path}")

    def _raw(self, method, url, headers=None, data=None, verify=None, timeout=None, json=None):
        path = url.split(":443", 1)[-1]
        if method == "POST" and "/software-image-uploads/" in path:
            name = path.rsplit("/", 1)[-1]
            start, end, total = re.match(r"(\d+)-(\d+)/(\d+)", headers["Content-Range"]).groups()
            image = self.images.setdefault(name, {"bytes": bytearray(), "verified": "no"})
            image["bytes"] += data
            if int(end) + 1 == int(total):
                image["verified"] = "yes"
            return SimpleNamespace(status_code=200, text="", headers={}, content=b"")
        if method == "GET" and "/ucs-downloads/" in path:
            start, end, _ = re.match(r"(\d+)-(\d+)/(\d+)", headers["Content-Range"]).groups()
            content = b"ucs"
            return SimpleNamespace(status_code=200, text="", headers={"Content-Range": f"{start}-{end}/{len(content)}"},
                                   content=content[int(start):int(end) + 1])
        if method == "PATCH":
            return SimpleNamespace(status_code=200, text="", headers={}, content=b"")
        raise RuntimeError(f"unexpected raw {method} {path}")


@pytest.fixture
def iso(tmp_path):
    path = tmp_path / ISO
    path.write_bytes(IMAGE_BYTES)
    return path


@pytest.fixture
def profile(iso):
    return Profile.from_mapping({"name": "bigip-17.5", "models": ["BIG-IP i5800", "BIG-IP Virtual Edition"],
                                 "starting_versions": ["17.1.1.3"], "target_version": "17.5.1.8", "image": ISO,
                                 "md5": MD5, "minimum_free_bytes": 1500000000, "image_source": str(iso),
                                 "license_check_date": "2024-12-01"})


@pytest.fixture
def options(tmp_path):
    return SimpleNamespace(apply=False, stage_only=False, lock_dir=tmp_path / "locks", show_timeout=60,
                           install_timeout=30, reload_timeout=3, settle_seconds=0, validation_timeout=1, poll_interval=0,
                           f5={"port": 443, "verify_tls": False, "timeout": 30, "provider": "tmos"},
                           ucs_dir=None, image_cache=tmp_path / "cache", allow_config_mismatch=False)


@pytest.fixture
def unit(monkeypatch):
    fake = FakeBigIP()
    monkeypatch.setattr(f5_workflow.f5_waf, "Client", fake)
    return fake


def run_unit(profile, options):
    host = SimpleNamespace(name="bigip-a", hostname="192.0.2.10", port=None, platform="f5_tmsh",
                           username="admin", password="secret", data={})
    reporter = Mock()
    reporter.emit.return_value = True
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    return result, reporter, [call.args[1] for call in reporter.emit.call_args_list]


# --- profile -------------------------------------------------------------------

def test_iso_image_selects_the_bigip_family(profile):
    assert profile.family == "f5" and profile.release("17.5.1.8") == (17, 5, 1, 8)
    assert f5_version("17.1.1") == (17, 1, 1, 0)


@pytest.mark.parametrize("change,message", [
    ({"image": "BIGIP-17.5.1.9-0.0.19.iso"}, "must match target_version"),
    ({"models": ["C9300-48P"]}, "BIG-IP models"),
    ({"starting_versions": ["17.5.1.8"]}, "older than the target"),
    ({"volume": "sda2"}, "boot location"),
    ({"license_check_date": "12/01/2024"}, "YYYY-MM-DD"),
    ({"image_source": "images/" + ISO}, "absolute path"),
    ({"image_source": "https://user:pw@images.example/" + ISO}, "without credentials"),
])
def test_bigip_profile_rules(profile, change, message):
    from dataclasses import asdict
    data = {**asdict(profile), **change}
    data["models"], data["starting_versions"] = list(data["models"]), list(data["starting_versions"])
    with pytest.raises(ValueError, match=message):
        Profile.from_mapping(data)


def test_ios_xe_profiles_reject_bigip_only_keys():
    with pytest.raises(ValueError, match="BIG-IP profiles only"):
        Profile.from_mapping({"name": "p", "models": ["C9300-48P"], "starting_versions": ["17.9.4a"], "target_version": "17.12.4",
                              "image": "cat9k_iosxe.17.12.04.SPA.bin", "md5": "a" * 32, "minimum_free_bytes": 1500000000, "volume": "HD1.2"})


def test_scheduler_maps_iso_profiles_to_the_f5_platform(profile):
    from dataclasses import asdict
    assert scheduler.platform_for(asdict(profile)) == "f5_tmsh"
    assert scheduler.platform_for({"image": "cat9k_iosxe.17.12.04.SPA.bin"}) == "cisco_ios"
    assert scheduler.platform_for(None) == "cisco_ios"


def test_cli_accepts_bigip_flags_on_both_commands():
    from netops.cli import build_parser
    args = build_parser().parse_args(["upgrade", "--profile", "p.yaml", "--f5-port", "8443", "--ucs-dir", "/tmp/ucs"])
    assert args.f5_port == 8443 and args.ucs_dir == "/tmp/ucs" and args.image_cache is None
    assert build_parser().parse_args(["upgrade-poll", "--f5-verify-tls"]).f5_insecure is False


# --- prechecks -----------------------------------------------------------------

def test_dry_run_reads_everything_and_writes_nothing(profile, options, unit):
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and stages[-1] == "dry_run_complete"
    writes = [(m, p) for m, p, payload in unit.calls if m != "GET" and p != "/mgmt/tm/util/bash"]
    assert writes == [] and unit.saved == 0 and unit.images == {} and unit.ucs == []
    plan = reporter.emit.call_args.args[3]["upgrade_plan"]
    assert plan["volume"] == {"name": "HD1.2", "create": False, "replaces_version": "16.1.4"}
    assert plan["image_verification"] == "pending_transfer" and plan["expected_failover_state"] == "standby"
    assert plan["commands"][-1].startswith('POST /mgmt/tm/sys {"command": "reboot"')


@pytest.mark.parametrize("setup,reason", [
    (lambda u: setattr(u, "failover", "active"), "active member"),
    (lambda u: setattr(u, "sync", "Changes Pending"), "configuration sync is 'Changes Pending'"),
    (lambda u: setattr(u, "license_date", "2024/01/15"), "older than the target release"),
    (lambda u: setattr(u, "license_date", None), "license service check date could not be read"),
    (lambda u: setattr(u, "model", "BIG-IP i2600"), "not approved by this profile"),
    (lambda u: setattr(u, "version", "16.1.4"), "not an approved starting version"),
    (lambda u: u.volumes[1].update(status="installing 10.000 pct"), "already in progress"),
])
def test_precheck_gates(profile, options, unit, setup, reason):
    setup(unit)
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and stages[-1] == "blocked"
    assert reason in reporter.emit.call_args.args[2]


def test_already_current_unit_is_left_alone(profile, options, unit):
    unit.version = "17.5.1.8"
    unit.volumes[0]["version"] = "17.5.1.8"
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and stages[-1] == "already_current"


def test_existing_image_with_a_different_checksum_is_never_overwritten(profile, options, unit):
    options.apply = True
    unit.images[ISO] = {"bytes": bytearray(b"something else"), "verified": "yes"}
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and stages[-1] == "blocked" and "checksum differs" in reporter.emit.call_args.args[2]
    assert unit.rebooted_to is None and unit.installing is None


# --- staging and upgrade -------------------------------------------------------

def test_stage_only_uploads_and_verifies_without_installing(profile, options, unit):
    options.apply, options.stage_only = True, True
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and stages[-1] == "staged" and result.changed
    assert bytes(unit.images[ISO]["bytes"]) == IMAGE_BYTES and unit.images[ISO]["verified"] == "yes"
    assert unit.saved == 0 and unit.ucs == [] and unit.installing is None and unit.rebooted_to is None
    assert "ready" in stages and stages.index("ready") < stages.index("staging")
    assert reporter.emit.call_args.args[3]["upgrade_plan"]["image_verification_note"] == "md5 verified on the unit"


def test_apply_upgrades_a_standby_member_end_to_end(profile, options, unit, tmp_path):
    options.apply = True
    options.ucs_dir = tmp_path / "ucs"
    result, reporter, stages = run_unit(profile, options)
    assert not result.failed and result.changed, reporter.emit.call_args.args[2]
    assert stages[-1] in ("completed", "completed_with_warnings")
    order = [stages.index(s) for s in ("saving_config", "precheck", "ready", "staging", "backing_up", "installing", "reconnecting", "validating")]
    assert order == sorted(order)
    assert unit.saved == 1 and unit.ucs == ["bigip-a-pre-17.5.1.8.ucs"] and unit.failovers == 0
    assert unit.rebooted_to == "HD1.2" and unit.version == "17.5.1.8"
    assert (tmp_path / "ucs" / "bigip-a-pre-17.5.1.8.ucs").read_bytes() == b"ucs"
    plan = reporter.emit.call_args.args[3]["upgrade_plan"]
    assert plan["ucs_backup"]["md5_verified"] and plan["configuration_saved"]
    assert reporter.emit.call_args.kwargs["attachment"] is None or "report" in reporter.emit.call_args.kwargs["attachment"]


def test_active_member_fails_over_first_only_when_allowed(profile, options, unit):
    from dataclasses import asdict, replace
    options.apply = True
    unit.failover = "active"
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and stages[-1] == "blocked" and unit.failovers == 0
    allowed = replace(profile, allow_active=True)
    result, reporter, stages = run_unit(allowed, options)
    assert not result.failed, reporter.emit.call_args.args[2]
    assert unit.failovers == 1 and stages.index("failing_over") < stages.index("installing")
    assert "failover" in json.dumps(reporter.emit.call_args.args[3]["upgrade_plan"]["commands"])


def test_ready_gate_failure_installs_nothing(profile, options, unit):
    options.apply = True
    host = SimpleNamespace(name="bigip-a", hostname="192.0.2.10", port=None, platform="f5_tmsh", username="admin", password="secret", data={})
    reporter = Mock()
    reporter.emit.return_value = False
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, reporter)
    assert result.failed and not result.changed
    assert unit.saved == 1 and unit.images == {} and unit.installing is None and unit.rebooted_to is None


def test_lost_unit_after_reboot_needs_recovery(profile, options, unit):
    options.apply = True
    options.reload_timeout, options.poll_interval = 0.3, 0.02
    unit.reboot_downtime = 10
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and result.changed and stages[-1] == "recovery_required"
    assert "reboot deadline exceeded" in reporter.emit.call_args.args[2]


def test_post_check_difference_is_reported_with_its_reason(profile, options, unit):
    options.apply = True
    real_reboot = unit._post

    def reboot_then_degrade(path, payload):
        if path == "/mgmt/tm/sys":
            unit.virtual_availability = "offline"
        return real_reboot(path, payload)

    unit._post = reboot_then_degrade
    result, reporter, stages = run_unit(profile, options)
    assert result.failed and stages[-1] == "validation_failed"
    assert "virtual_servers: 2->2 rows (removed /Common/vs-web; added /Common/vs-web)" in reporter.emit.call_args.args[2]


def test_baseline_is_unordered_and_report_renders(profile, options, unit):
    before = f5.collect(unit, lambda path: None)
    unit.volumes.reverse()
    after = f5.collect(unit, lambda path: None)
    from netops.upgrade import checks
    assert not [f for f in checks.compare(before, after) if f["severity"] == "error"]
    plan = f5_workflow.preflight(before, profile)
    text = report.build(SimpleNamespace(name="bigip-a", hostname="192.0.2.10"), before, after, checks.compare(before, after), plan, "completed")
    assert "| 1 | BIG-IP i5800 | 17.1.1.3 | 17.1.1.3 | HD1.1 | HD1.1 | ready | ready |" in text
    assert "| virtual_servers | 2 | 2 | 2 | 0 | 0 |" in text and "Not compared: the running configuration" in text
