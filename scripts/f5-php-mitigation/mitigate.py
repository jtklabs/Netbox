#!/usr/bin/env python3
"""Apply F5 K000163821 over SSH and report each device to a JSON webhook."""
from __future__ import annotations

import argparse
import base64
import csv
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shlex
import socket
import sys
import threading
import time
from urllib.parse import urlsplit
import uuid

import paramiko
from dotenv import dotenv_values
import requests

HERE = Path(__file__).resolve().parent
ADVISORY = "https://my.f5.com/manage/s/article/K000163821"
SUCCESS = {"applied", "compliant"}
REMOTE_STATUSES = SUCCESS | {
    "needs_change", "failed", "failed_rolled_back", "failed_rollback_incomplete"
}


@dataclass(frozen=True)
class Device:
    host: str
    name: str = ""
    port: int = 22
    httpd_sam: bool = False  # True requires it; False selects automatically.


@dataclass(frozen=True)
class Config:
    username: str
    password: str | None
    key: str | None
    key_passphrase: str | None
    known_hosts: str
    connect_timeout: int
    command_timeout: int
    webhook_url: str
    webhook_token: str | None
    webhook_ca: str | bool
    webhook_timeout: int
    webhook_attempts: int
    workers: int = 1


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def positive(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise ValueError("Must be positive")
    return parsed


def worker_count(value: str) -> int:
    count = int(value)
    if not 1 <= count <= 32:
        raise ValueError("Workers must be between 1 and 32")
    return count


def boolean(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"true", "yes", "1"}:
        return True
    if normalized in {"false", "no", "0", "auto"}:
        return False
    raise ValueError("httpd_sam must be auto, true, or false")


def make_device(host: str, name: str, port: str, sam: bool) -> Device:
    # IP addresses only; reject schemes, shell metacharacters and CIDRs.
    address = str(ipaddress.ip_address(host.strip()))
    if "%" in address:
        raise ValueError("Scoped IPv6 addresses are not supported")
    number = int(port)
    if not 1 <= number <= 65535:
        raise ValueError("SSH port must be between 1 and 65535")
    if len(name) > 128 or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise ValueError("Device name must be at most 128 printable characters")
    return Device(address, name, number, sam)


def load_devices(args: argparse.Namespace) -> list[Device]:
    if args.host:
        return [make_device(args.host, "", str(args.port), args.httpd_sam)]
    devices = []
    with args.csv.open(newline="", encoding="utf-8-sig") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or "host" not in reader.fieldnames:
            raise ValueError("CSV requires a host column")
        if len(reader.fieldnames) != len(set(reader.fieldnames)):
            raise ValueError("CSV has duplicate column names")
        if set(reader.fieldnames) - {"host", "name", "port", "httpd_sam"}:
            raise ValueError("CSV columns allowed: host,name,port,httpd_sam")
        for line, row in enumerate(reader, 2):
            try:
                if None in row or any(value is None for value in row.values()):
                    raise ValueError("Column count mismatch")
                sam = row.get("httpd_sam", "").strip()
                devices.append(make_device(
                    row["host"], row.get("name", "").strip(),
                    row.get("port", "").strip() or str(args.port),
                    boolean(sam) if sam else args.httpd_sam,
                ))
            except ValueError as exc:
                # Do not echo row contents: an accidentally misplaced secret
                # should not be copied into terminal logs.
                raise ValueError(f"Invalid inventory at CSV line {line}") from exc
    if not devices:
        raise ValueError("CSV contains no devices")
    identities = [(device.host, device.port) for device in devices]
    if len(identities) != len(set(identities)):
        raise ValueError("CSV contains duplicate host/port pairs")
    return devices


def load_config(path: Path) -> Config:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError("The .env file does not exist; copy .env.example and fill it in")
    # Disable interpolation so dollar signs in passwords remain literal.
    values = dotenv_values(path, interpolate=False)

    def get(key: str, default: str = "") -> str:
        return values.get(key) or default

    def local_file(key: str, default: str = "") -> str | None:
        raw = get(key, default)
        if not raw:
            return None
        item = Path(raw).expanduser()
        if not item.is_absolute():
            item = path.parent / item
        if not item.is_file():
            raise ValueError(f"{key} must point to an existing file")
        return str(item)

    username = get("F5_USERNAME")
    password = get("F5_PASSWORD") or None
    key = local_file("F5_SSH_KEY")
    if not username or not (password or key):
        raise ValueError("Set F5_USERNAME and F5_PASSWORD or F5_SSH_KEY in .env")
    known_hosts = local_file("F5_KNOWN_HOSTS", "~/.ssh/known_hosts")
    url = get("WEBHOOK_URL")
    parsed = urlsplit(url)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.fragment):
        raise ValueError("WEBHOOK_URL must be HTTPS without embedded credentials or a fragment")
    if parsed.hostname == "example.com":
        raise ValueError("Replace the example WEBHOOK_URL with your receiver")
    return Config(
        username, password, key, get("F5_SSH_KEY_PASSPHRASE") or None,
        known_hosts or "", positive(get("F5_CONNECT_TIMEOUT", "15")),
        positive(get("F5_COMMAND_TIMEOUT", "300")), url,
        get("WEBHOOK_BEARER_TOKEN") or None,
        local_file("WEBHOOK_CA_BUNDLE") or True,
        positive(get("WEBHOOK_TIMEOUT", "15")),
        min(positive(get("WEBHOOK_ATTEMPTS", "3")), 5),
        worker_count(get("F5_WORKERS", "1")),
    )


def load_assets() -> tuple[str, bytes]:
    template = (HERE / "remote_mitigation.sh").read_text(encoding="utf-8")
    content = (HERE / "K000163821.ini").read_bytes()
    # Fail closed if someone changes the supplied mitigation values by mistake.
    settings = {}
    for line in content.decode("ascii").splitlines():
        line = line.strip()
        if line and not line.startswith(";"):
            key, value = line.split("=", 1)
            if key.strip() in settings:
                raise ValueError("Duplicate mitigation setting")
            settings[key.strip()] = value.strip()
    if settings != {
        "memory_limit": "-1", "post_max_size": "8M",
        "upload_max_filesize": "7M", "session.upload_progress.enabled": "0",
    }:
        raise ValueError("K000163821.ini does not match the supported mitigation")
    return template, content


def remote_script(template: str, content: bytes, mode: str, sam: bool) -> str:
    replacements = {
        "MODE": mode, "SAM": str(sam).lower(),
        "SHA256": hashlib.sha256(content).hexdigest(),
        "CONTENT": base64.b64encode(content).decode("ascii"),
    }
    for key, value in replacements.items():
        template = template.replace(f"@@{key}@@", shlex.quote(value))
    if "@@" in template:
        raise ValueError("Unresolved remote script placeholder")
    return template


def read_channel(channel, timeout: int) -> tuple[int, str]:
    """Drain both streams to avoid SSH window deadlocks; bound time and output."""
    deadline = time.monotonic() + timeout
    output = bytearray()
    received = 0
    while True:
        if time.monotonic() >= deadline:
            raise TimeoutError("Remote command deadline exceeded")
        if channel.recv_ready():
            chunk = channel.recv(32768)
            output.extend(chunk)
            received += len(chunk)
        if channel.recv_stderr_ready():
            received += len(channel.recv_stderr(32768))
        if received > 1024 * 1024:
            raise RuntimeError("Remote output limit exceeded")
        if channel.exit_status_ready() and not channel.recv_ready() and not channel.recv_stderr_ready():
            return channel.recv_exit_status(), output.decode("utf-8", errors="replace")
        if channel.closed:
            raise ConnectionError("SSH channel closed without exit status")
        time.sleep(0.02)


def parse_result(code: int, output: str, mode: str) -> dict:
    parsed = {}
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) == 3 and parts[0] == "F5JOB":
            key, value = parts[1:]
            # Only copy narrowly validated protocol fields into reports.
            if key in {"stage", "failed_stage", "error", "status"} and re.fullmatch(r"[a-z_]{1,80}", value):
                parsed[key] = value
            elif key == "script_exit_code" and re.fullmatch(r"[0-9]{1,3}", value) and int(value) <= 255:
                parsed[key] = int(value)
            elif key == "changed" and value in {"true", "false"}:
                parsed[key] = value == "true"
            elif key in {"sha256", "previous_sha256"} and (re.fullmatch(r"[a-f0-9]{64}", value) or value == "absent"):
                parsed[key] = value
            elif key == "backup" and re.fullmatch(r"/var/tmp/K000163821\.[A-Za-z0-9]{8}", value):
                parsed[key] = value
            elif key == "services" and value in {"httpd", "httpd httpd_sam"}:
                parsed[key] = value.split()
            elif key == "httpd_sam_state" and value in {"unknown", "running", "stopped", "absent"}:
                parsed[key] = value
            elif key == "httpd_sam_decision" and value in {"restart", "check", "skip", "blocked"}:
                parsed[key] = value
    status = parsed.get("status")
    valid = status in REMOTE_STATUSES
    # TMSH can return zero even when its child Bash command fails. Require the
    # script's terminal exit marker and still reject an SSH failure on success.
    script_code = parsed.get("script_exit_code")
    valid &= script_code is not None
    valid &= (script_code == 0) == (status in SUCCESS | {"needs_change"})
    valid &= code == 0 or (script_code is not None and script_code != 0)
    valid &= bool(output.splitlines()) and output.splitlines()[-1] == f"F5JOB\tscript_exit_code\t{script_code}"
    valid &= not (mode == "apply" and status in {"compliant", "needs_change"})
    valid &= not (mode == "check" and status == "applied")
    valid &= not (status == "applied" and parsed.get("stage") != "complete")
    if not valid:
        parsed["status"] = "unknown" if mode == "apply" else "failed"
        parsed["error"] = "incomplete_or_invalid_remote_result"
    parsed["remote_exit_code"] = code
    return parsed


class ShellAccessError(Exception):
    """A sanitized preflight failure; no mitigation was dispatched."""


def detect_shell(transport, timeout: int) -> tuple[str, str]:
    """Probe shell access without writes, then select exactly one script launcher."""
    marker = "F5SHELL_" + uuid.uuid4().hex + ":"
    probe = shlex.quote(f"printf {marker}; id -u")
    candidates = (
        ("bash", "/bin/bash -c " + probe, "/bin/bash -s"),
        ("tmsh", "run /util bash -c " + probe, "run /util bash -c '/bin/bash -s'"),
    )
    for shell, command, launcher in candidates:
        channel = transport.open_session(timeout=timeout)
        try:
            channel.settimeout(timeout)
            channel.exec_command(command)
            channel.shutdown_write()
            code, output = read_channel(channel, timeout)
        finally:
            channel.close()
        match = re.search(r"^" + re.escape(marker) + r"([0-9]+)\r?$", output, re.MULTILINE)
        if code == 0 and match:
            if match.group(1) != "0":
                raise ShellAccessError("root_privileges_required")
            return shell, launcher
    raise ShellAccessError("bash_access_unavailable")


def execute_device(device: Device, config: Config, mode: str, script: str) -> dict:
    result = {"status": "failed", "changed": None, "stage": "connect"}
    client = paramiko.SSHClient()
    started = False
    channel = None
    try:
        client.load_host_keys(config.known_hosts)
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(
            hostname=device.host, port=device.port, username=config.username,
            password=config.password, key_filename=config.key,
            passphrase=config.key_passphrase, look_for_keys=False,
            allow_agent=False, timeout=config.connect_timeout,
            banner_timeout=config.connect_timeout, auth_timeout=config.connect_timeout,
        )
        result["stage"] = "shell_detect"
        transport = client.get_transport()
        if transport is None:
            raise ConnectionError("Missing SSH transport")
        transport.set_keepalive(15)
        shell, launcher = detect_shell(transport, config.connect_timeout)
        result["login_shell"] = shell
        result["stage"] = "execute"
        channel = transport.open_session(timeout=config.connect_timeout)
        channel.settimeout(config.connect_timeout)
        # Set started before dispatch: a lost reply may hide successful dispatch.
        started = True
        channel.exec_command(launcher)
        channel.sendall(script.encode("utf-8"))
        channel.shutdown_write()
        code, output = read_channel(channel, config.command_timeout)
        result.update(parse_result(code, output, mode))
    except paramiko.AuthenticationException:
        result["error"] = "ssh_authentication_failed"
    except paramiko.BadHostKeyException:
        result["error"] = "ssh_host_key_mismatch"
    except ShellAccessError as exc:
        result["error"] = str(exc)
    except (TimeoutError, socket.timeout):
        result["error"] = "ssh_timeout"
        result["status"] = "unknown" if started and mode == "apply" else "failed"
    except Exception:
        # Library exception messages may contain credentials, URLs or raw output.
        result["error"] = "ssh_or_remote_execution_failed"
        result["status"] = "unknown" if started and mode == "apply" else "failed"
    finally:
        if channel is not None:
            channel.close()
        client.close()
    return result


def notify(config: Config, payload: dict) -> dict:
    headers = {"Content-Type": "application/json", "Idempotency-Key": payload["event_id"]}
    if config.webhook_token:
        headers["Authorization"] = "Bearer " + config.webhook_token
    # No .netrc credential injection, ambient proxy settings, or redirects.
    session = requests.Session()
    session.trust_env = False
    result = {"status": "failed", "attempts": 0}
    try:
        for attempt in range(1, config.webhook_attempts + 1):
            result["attempts"] = attempt
            try:
                with session.post(
                    config.webhook_url, json=payload, headers=headers,
                    timeout=(config.webhook_timeout, config.webhook_timeout),
                    verify=config.webhook_ca, allow_redirects=False, stream=True,
                ) as response:
                    result["http_status"] = response.status_code
                    if 200 <= response.status_code < 300:
                        result["status"] = "delivered"
                        return result
                    result["error"] = "webhook_http_error"
                    if response.status_code != 429 and response.status_code < 500:
                        return result
            except requests.RequestException:
                result["error"] = "webhook_connection_or_tls_error"
            if attempt < config.webhook_attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
        return result
    finally:
        session.close()


def print_report(results: list[dict], report_path: Path, mode: str) -> None:
    print(f"\nK000163821 — {mode.upper()} REPORT", flush=True)
    print(f"{'HOST':<40} {'RESULT':<28} {'WEBHOOK':<12} {'SECONDS':>8}")
    for result in results:
        print(f"{result['device']['host']:<40} {result['status']:<28} "
              f"{result['webhook']['status']:<12} {result['duration_seconds']:>8.1f}")
        if result.get("error"):
            print(f"  Error: {result['error']} (stage: {result.get('failed_stage', result.get('stage'))})")
        if result.get("backup"):
            print(f"  Device backup: {result['backup']}")
        if result.get("login_shell"):
            print(f"  SSH shell: {result['login_shell']}")
        if result.get("httpd_sam_decision"):
            print(f"  httpd_sam decision: {result['httpd_sam_decision']} "
                  f"(detected state: {result.get('httpd_sam_state', 'unknown')})")
        if result["status"] == "unknown":
            print("  Remote state is uncertain. Inspect the device before another apply attempt.")
    okay = sum(item["status"] in SUCCESS for item in results)
    delivery_failed = sum(item["webhook"]["status"] != "delivered" for item in results)
    print(f"Devices: {len(results)} | Successful/compliant: {okay} | "
          f"Other: {len(results) - okay} | Webhook failures: {delivery_failed}")
    print(f"JSON report: {report_path}", flush=True)


def save_report(path: Path, report: dict) -> None:
    temporary = path.with_suffix(".tmp")
    # O_EXCL avoids silently following a pre-existing temporary-file symlink.
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            json.dump(report, destination, indent=2)
            destination.write("\n")
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


class BatchRunner:
    """Independent SSH/webhook workers; serialized report writes and console lines."""

    def __init__(self, config: Config, mode: str, report: dict, path: Path):
        self.config = config
        self.mode = mode
        self.report = report
        self.path = path
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.interrupted = False
        self.reporting_failed = False

    def persist_locked(self) -> None:
        # Caller owns self.lock. Never write the shared report concurrently.
        try:
            save_report(self.path, self.report)
        except OSError:
            self.reporting_failed = True
            self.stop.set()

    def process(self, device: Device, script: str) -> dict:
        started = time.monotonic()
        event = {
            "event": "f5.mitigation.device.completed", "schema_version": 1,
            "event_id": str(uuid.uuid4()), "run_id": self.report["run_id"],
            "advisory": ADVISORY, "mode": self.mode, "device": asdict(device),
            "started_at": utc_now(),
        }
        with self.lock:
            print(f"[{device.host}] {self.mode} started", flush=True)
        try:
            event.update(execute_device(device, self.config, self.mode, script))
        except KeyboardInterrupt:
            with self.lock:
                self.interrupted = True
                self.stop.set()
            event.update(status="unknown" if self.mode == "apply" else "failed",
                         changed=None, error="interrupted", stage="interrupted")
        except Exception:
            event.update(status="unknown" if self.mode == "apply" else "failed",
                         changed=None, error="unexpected_execution_error", stage="execute")
        event["finished_at"] = utc_now()
        event["duration_seconds"] = round(time.monotonic() - started, 2)
        event["webhook"] = {"status": "pending", "attempts": 0}
        unsafe = event["status"] in {"unknown", "failed_rollback_incomplete"}
        unsafe |= (self.mode == "apply" and event["status"] == "failed"
                   and event.get("failed_stage") in {"restart", "verify"})
        if unsafe:
            # Stop scheduling immediately, before potentially slow delivery.
            self.stop.set()
        with self.lock:
            self.report["results"].append(event)
            self.persist_locked()
        # Each worker owns its HTTP session; notification never holds report lock.
        payload = {key: value for key, value in event.items() if key != "webhook"}
        try:
            delivery = notify(self.config, payload)
        except KeyboardInterrupt:
            with self.lock:
                self.interrupted = True
                self.stop.set()
            delivery = {"status": "unknown", "error": "interrupted"}
        except Exception:
            delivery = {"status": "failed", "error": "unexpected_delivery_error"}
        with self.lock:
            event["webhook"] = delivery
            self.persist_locked()
            sam_summary = ""
            if event.get("httpd_sam_decision"):
                sam_summary = (f" | httpd_sam: {event['httpd_sam_decision']}"
                               f" ({event.get('httpd_sam_state', 'unknown')})")
            print(f"[{device.host}] {event['status']}{sam_summary} | webhook: {delivery['status']}", flush=True)
        return event

    def run(self, devices: list[Device], scripts: list[str], workers: int) -> None:
        pending = iter(zip(devices, scripts))
        # Submit at most `workers` futures; do not queue the entire inventory.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="f5-device") as pool:
            active = set()
            exhausted = False
            while active or (not exhausted and not self.stop.is_set()):
                try:
                    while len(active) < workers and not exhausted and not self.stop.is_set():
                        try:
                            device, script = next(pending)
                        except StopIteration:
                            exhausted = True
                            break
                        active.add(pool.submit(self.process, device, script))
                    if active:
                        finished, _ = wait(active, timeout=0.25, return_when=FIRST_COMPLETED)
                        for future in finished:
                            future.result()
                            active.remove(future)
                except KeyboardInterrupt:
                    self.interrupted = True
                    self.stop.set()
                    with self.lock:
                        print("Stopping new devices; waiting for in-flight devices and their webhooks.", flush=True)
            # Leaving the pool only after all started jobs have reported avoids
            # interrupting an installation or returning an incomplete report.


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", help="One management/blade IPv4 or IPv6 address")
    target.add_argument("--csv", type=Path, help="Inventory with a host column")
    mode = result.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Install settings and restart services")
    mode.add_argument("--check", action="store_true", help="Read-only check (default)")
    result.add_argument("--require-httpd-sam", "--httpd-sam", dest="httpd_sam", action="store_true",
                        help="Expect APM/SSLO: fail if httpd_sam is not running (default: auto-select running service)")
    result.add_argument("--port", type=int, default=22, help="Default SSH port")
    result.add_argument("--workers", type=worker_count,
                        help="Concurrent device jobs, 1-32 (overrides F5_WORKERS in .env; default 1)")
    result.add_argument("--env-file", type=Path, default=Path(".env"))
    result.add_argument("--report-dir", type=Path, default=Path("reports"))
    return result


def main(argv: list[str] | None = None) -> int:
    cli = parser()
    args = cli.parse_args(argv)
    try:
        devices = load_devices(args)
        config = load_config(args.env_file)
        workers = min(args.workers if args.workers is not None else config.workers, len(devices))
        template, content = load_assets()
        mode = "apply" if args.apply else "check"
        # Render every script before contacting the first device.
        scripts = [remote_script(template, content, mode, d.httpd_sam) for d in devices]
        args.report_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        run_id = str(uuid.uuid4())
        report_path = args.report_dir.resolve() / f"K000163821-{run_id}.json"
        report = {
            "run_id": run_id, "advisory": ADVISORY, "mode": mode,
            "started_at": utc_now(), "finished_at": None,
            "target_count": len(devices), "mitigation_sha256": hashlib.sha256(content).hexdigest(),
            "workers": workers,
            "results": [],
        }
        save_report(report_path, report)
    except (ValueError, OSError, UnicodeError):
        print("Setup failed. Check inventory, .env values, credential/known_hosts paths, "
              "mitigation assets, and report directory permissions. No devices contacted.", file=sys.stderr)
        return 2

    print(f"Mode: {mode} | Targets: {len(devices)} | Concurrent device limit: {workers}", flush=True)
    runner = BatchRunner(config, mode, report, report_path)
    try:
        runner.run(devices, scripts, workers)
    finally:
        report["finished_at"] = utc_now()
        report["interrupted"] = runner.interrupted
        report["unprocessed_count"] = len(devices) - len(report["results"])
        try:
            save_report(report_path, report)
        except OSError:
            runner.reporting_failed = True
        print_report(report["results"], report_path, mode)
        if report["unprocessed_count"]:
            print(f"Unprocessed devices: {report['unprocessed_count']}")
        if runner.reporting_failed:
            print("Report persistence failed; the saved JSON may be incomplete.", file=sys.stderr)
    if runner.interrupted:
        return 130
    if runner.reporting_failed or any(item["status"] not in SUCCESS or item["webhook"]["status"] != "delivered"
                               for item in report["results"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
