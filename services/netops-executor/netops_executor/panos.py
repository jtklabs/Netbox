"""Panorama XML API client. Firewall operations are proxied with target=<serial>."""

import re
import ssl
import time
import xml.etree.ElementTree as ET

import httpx

from .credentials import AuthenticationRejected, Selector, Vault
from .execution import credential_options, probe
from .store import Conflict

DEVICES = "<show><devices><all></all></devices></show>"
SYSTEM_INFO = "<show><system><info></info></system></show>"
CHASSIS_READY = "<show><chassis-ready></chassis-ready></show>"
HA_STATE = "<show><high-availability><state></state></high-availability></show>"
PENDING_CHANGES = "<check><pending-changes></pending-changes></check>"
JOBS = "<show><jobs><all></all></jobs></show>"
SOFTWARE_CHECK = "<request><system><software><check></check></software></system></request>"
SOFTWARE_INFO = "<request><system><software><info></info></software></system></request>"
INTERFACES = "<show><interface>hardware</interface></show>"
ROUTES = "<show><routing><route></route></routing></show>"
BGP_PEERS = "<show><routing><protocol><bgp><peer></peer></bgp></protocol></routing></show>"
TUNNELS = "<show><running><tunnel><flow><all></all></flow></tunnel></running></show>"
SESSIONS = "<show><session><info></info></session></show>"
ARP = "<show><arp><entry name='all'/></arp></show>"
RUNNING_CONFIG = "<show><config><running></running></config></show>"
SUSPEND = "<request><high-availability><state><suspend></suspend></state></high-availability></request>"
FUNCTIONAL = (
    "<request><high-availability><state><functional></functional></state></high-availability></request>"
)
RESTART = "<request><restart><system></system></restart></request>"


def show_job(job_id):
    return "<show><jobs><id>" + str(int(job_id)) + "</id></jobs></show>"


def download(version):
    # Versions are validated against a strict pattern before reaching XML.
    return (
        "<request><system><software><download><version>"
        + version
        + "</version></download></software></system></request>"
    )


def install(version):
    return (
        "<request><system><software><install><version>"
        + version
        + "</version></install></software></system></request>"
    )


class PanosError(Exception):
    """Panorama or the firewall rejected an operation, or its output could not be read."""


class PanosUnavailable(PanosError):
    """No API response: the request may or may not have reached the firewall."""


def text(element, path, default=""):
    found = element.find(path) if element is not None else None
    return found.text.strip() if found is not None and found.text else default


def message(root):
    return (
        " ".join(t.strip() for t in root.find("msg").itertext() if t.strip())
        if root.find("msg") is not None
        else ""
    )


def uptime_seconds(value):
    match = re.fullmatch(r"\s*(?:(\d+)\s+days?,\s*)?(\d+):(\d{2}):(\d{2})\s*", value or "")
    if not match:
        return None
    days, hours, minutes, seconds = (int(v or 0) for v in match.groups())
    return ((days * 24 + hours) * 60 + minutes) * 60 + seconds


class Panorama:
    def __init__(self, settings, store, target_id, profiles, guard, audit):
        self.target = settings.http_targets[target_id]
        if not self.target.auth_probe_path:
            raise PanosError("Panorama target requires a configured read-only authentication probe")
        self.selector = Selector(
            Vault(store, settings.credential_key_file),
            "http",
            self.target.id,
            profiles if profiles is not None else self.target.credential_profiles,
            audit,
        )
        self.guard = guard
        self.client = None
        self.auth = None

    def connect(self):
        if self.client is None:
            verify = ssl.create_default_context(cafile=self.target.ca_file) if self.target.ca_file else True
            self.client = httpx.Client(
                base_url=self.target.base_url, verify=verify, follow_redirects=False, trust_env=False
            )
        if self.auth is None:

            def authenticate(c):
                options = credential_options(self.selector, c)
                probe(self.client, self.target, options, 30, self.guard)
                return options

            try:
                self.auth = self.selector.run(authenticate)
            except httpx.HTTPError as exc:
                raise PanosUnavailable("Panorama probe failed (" + type(exc).__name__ + ")") from None
            except AuthenticationRejected:
                raise PanosError("Panorama rejected every eligible HTTP credential") from None
            except (Conflict, ValueError) as exc:
                raise PanosError("Panorama authentication failed: " + str(exc)) from None
        return self.auth

    def op(self, cmd, target=None, timeout=60, limit=16 * 1024 * 1024):
        options = self.connect()
        form = {"type": "op", "cmd": cmd, **({"target": target} if target else {})}
        try:
            with self.client.stream("POST", "/api/", data=form, timeout=timeout, **options) as response:
                content = bytearray()
                deadline = time.monotonic() + timeout
                for chunk in response.iter_bytes():
                    self.guard()
                    content.extend(chunk)
                    if len(content) > limit or time.monotonic() > deadline:
                        raise PanosUnavailable("Panorama response exceeded size or duration limit")
                status = response.status_code
        except httpx.HTTPError as exc:
            raise PanosUnavailable("Panorama request failed (" + type(exc).__name__ + ")") from None
        if status in (401, 403):
            # The key may have rotated; select again before the next request.
            self.auth = None
            raise PanosError("Panorama rejected the API credential (HTTP " + str(status) + ")")
        if not 200 <= status < 300:
            raise PanosError("Panorama returned HTTP " + str(status))
        try:
            root = ET.fromstring(content)
        except ET.ParseError:
            raise PanosError("Panorama returned malformed XML") from None
        if root.tag != "response" or root.get("status") != "success":
            detail = message(root) or "operation rejected"
            raise PanosError(self.selector.redact(detail)[:500])
        result = root.find("result")
        return result if result is not None else ET.Element("result")

    def device(self, serial):
        for entry in self.op(DEVICES).findall("devices/entry"):
            if (text(entry, "serial") or entry.get("name")) == serial:
                return {
                    "serial": serial,
                    "connected": text(entry, "connected") == "yes",
                    "hostname": text(entry, "hostname"),
                    "model": text(entry, "model"),
                    "sw_version": text(entry, "sw-version"),
                    "ha_state": text(entry, "ha/state").lower(),
                    "ha_peer": text(entry, "ha/peer/serial") or None,
                }
        return None

    def close(self):
        if self.client is not None:
            self.client.close()
            self.client = None


class Firewall:
    def __init__(self, panorama, serial):
        self.panorama, self.serial = panorama, serial

    def op(self, cmd, **kwargs):
        return self.panorama.op(cmd, target=self.serial, **kwargs)

    def system_info(self):
        system = self.op(SYSTEM_INFO).find("system")
        if system is None:
            raise PanosError("show system info returned no system element")
        info = {
            key.replace("-", "_"): text(system, key)
            for key in (
                "hostname",
                "serial",
                "model",
                "sw-version",
                "app-version",
                "threat-version",
                "av-version",
                "uptime",
                "operational-mode",
                "multi-vsys",
            )
        }
        if not info["sw_version"]:
            raise PanosError("show system info returned no sw-version")
        info["uptime_seconds"] = uptime_seconds(info["uptime"])
        return info

    def chassis_ready(self):
        return "".join(self.op(CHASSIS_READY).itertext()).strip().lower() == "yes"

    def ha_state(self):
        result = self.op(HA_STATE)
        group = result.find("group")
        return {
            "enabled": text(result, "enabled") == "yes",
            "mode": text(group, "mode").lower(),
            "local": text(group, "local-info/state").lower(),
            "local_version": text(group, "local-info/build-rel"),
            "preemptive": text(group, "local-info/preemptive"),
            "peer_state": text(group, "peer-info/state").lower(),
            "peer_conn": text(group, "peer-info/conn-status").lower(),
            "peer_version": text(group, "peer-info/build-rel"),
            "running_sync": text(group, "running-sync").lower(),
        }

    def pending_changes(self):
        value = "".join(self.op(PENDING_CHANGES).itertext()).strip().lower()
        if value not in ("yes", "no"):
            raise PanosError("check pending-changes returned an unrecognized value")
        return value == "yes"

    @staticmethod
    def parse_job(job):
        return {
            "id": int(text(job, "id", "0") or 0),
            "type": text(job, "type"),
            "status": text(job, "status").upper(),
            "result": text(job, "result").upper(),
            "progress": text(job, "progress"),
            "details": " ".join(t.strip() for t in job.find("details").itertext() if t.strip())[:2000]
            if job.find("details") is not None
            else "",
        }

    def jobs(self):
        return [self.parse_job(job) for job in self.op(JOBS).findall("job")]

    def job(self, job_id):
        job = self.op(show_job(job_id)).find("job")
        if job is None:
            raise PanosError("job " + str(job_id) + " was not reported")
        return self.parse_job(job)

    def enqueue(self, cmd):
        """Send a job-producing write once and return the firewall job ID."""
        result = self.op(cmd, timeout=120)
        value = text(result, "job")
        if not value.isdigit():
            raise PanosError("operation was accepted without a job ID: " + "".join(result.itertext())[:300])
        return int(value)

    def command(self, cmd):
        return " ".join(t.strip() for t in self.op(cmd, timeout=120).itertext() if t.strip())[:1000]

    def software(self, refresh=False):
        versions = {}
        for entry in self.op(SOFTWARE_CHECK if refresh else SOFTWARE_INFO, timeout=300).iter("entry"):
            version = text(entry, "version")
            if version:
                versions[version] = {
                    "filename": text(entry, "filename"),
                    "downloaded": text(entry, "downloaded") == "yes",
                    "current": text(entry, "current") == "yes",
                }
        return versions

    def interfaces(self):
        hw = self.op(INTERFACES).find("hw")
        if hw is None:
            raise PanosError("show interface hardware returned no hw table")
        return {text(e, "name"): text(e, "state").lower() for e in hw.findall("entry") if text(e, "name")}

    def routes(self):
        return sorted(
            {
                "|".join(text(e, k) for k in ("virtual-router", "destination", "nexthop", "interface"))
                for e in self.op(ROUTES).findall("entry")
            }
        )

    def bgp_peers(self):
        return {
            (e.get("vr") or "") + "|" + (e.get("peer") or text(e, "peer-address")): text(e, "status").lower()
            for e in self.op(BGP_PEERS).findall("entry")
        }

    def tunnels(self):
        ipsec = self.op(TUNNELS).find("IPSec")
        if ipsec is None:
            return {}
        return {text(e, "name"): text(e, "state").lower() for e in ipsec.findall("entry") if text(e, "name")}

    def session_count(self):
        value = text(self.op(SESSIONS), "num-active")
        if not value.isdigit():
            raise PanosError("show session info returned no num-active")
        return int(value)

    def arp_count(self):
        value = text(self.op(ARP), "total")
        if not value.isdigit():
            raise PanosError("show arp returned no total")
        return int(value)

    def running_config(self):
        result = self.op(RUNNING_CONFIG, timeout=300, limit=64 * 1024 * 1024)
        if result.find("config") is None:
            raise PanosError("running configuration export returned no config element")
        return ET.tostring(result.find("config"), encoding="utf-8")
