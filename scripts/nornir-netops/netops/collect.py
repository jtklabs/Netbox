"""`configure.py collect`: gather each platform's show commands and file them in NetBox.

Discovery identifies the platform first (NetBox, the CSV, or SSH autodetect);
this picks the command set for it, runs the commands read-only, writes one
text file per command beside the archive, and posts each file to the
discovery plugin so it appears on the device page in NetBox.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import archive
from .core import canonical_platform
from .debuglog import redact

PACKAGE_CATALOG = Path(__file__).with_name("commands.yaml")
ENDPOINT = "plugins/discovery/command-outputs/"
DEFAULT_TIMEOUT = 120
MAX_UPLOAD_BYTES = 16 * 1024 * 1024
# Device replies that mean the command itself was not understood.
REJECTED = re.compile(r"(?im)^\s*(?:%\s*(?:Invalid|Incomplete|Ambiguous|Unknown|Error)|error:|syntax error|unknown command|"
                      r"Unrecognized command|Invalid input|command not found|Unknown action|Not Found:|Invalid syntax|Bad command)")


def rejected(text):
    """True when the reply is the device refusing the command rather than its output."""
    lines = text.strip().splitlines()
    if not lines:
        return False
    if REJECTED.search(text):
        return True
    # A short reply that opens with '%' is an IOS-style refusal ("% BGP not active").
    return lines[0].lstrip().startswith("%") and len(lines) <= 3


class CatalogError(ValueError):
    pass


def slug(command):
    """`show configuration | display json` -> `show_configuration_display_json`."""
    return re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9]+", "_", command)).strip("_")


def load_catalog(path=None, project_root=None):
    """The command sets: an explicit file, else <project>/commands.yaml, else the packaged default."""
    candidates = [Path(path)] if path else []
    if project_root is not None:
        candidates.append(Path(project_root) / "commands.yaml")
    candidates.append(PACKAGE_CATALOG)
    source = next((c for c in candidates if c.is_file()), None)
    if source is None:
        raise CatalogError(f"command catalog not found: {candidates[0]}")
    data = yaml.safe_load(source.read_text()) or {}
    if not isinstance(data, dict) or not data:
        raise CatalogError(f"{source}: the catalog must map platforms to command sets")
    catalog = {}
    for platform, spec in data.items():
        if not isinstance(spec, dict) or not isinstance(spec.get("commands"), list) or not spec["commands"]:
            raise CatalogError(f"{source}: {platform}: expected a mapping with a nonempty 'commands' list")
        commands = []
        for entry in spec["commands"]:
            if isinstance(entry, str):
                entry = {"command": entry}
            if not isinstance(entry, dict) or not str(entry.get("command", "")).strip():
                raise CatalogError(f"{source}: {platform}: each command is a string or {{command, timeout}}")
            timeout = entry.get("timeout", DEFAULT_TIMEOUT)
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                raise CatalogError(f"{source}: {platform}: timeout for {entry['command']!r} must be a positive number")
            commands.append({"command": str(entry["command"]).strip(), "timeout": float(timeout)})
        names = [c["command"] for c in commands]
        if len(set(slug(n) for n in names)) != len(names):
            raise CatalogError(f"{source}: {platform}: two commands produce the same filename")
        catalog[canonical_platform(platform)] = {
            "driver": str(spec.get("driver") or canonical_platform(platform)),
            "enable": bool(spec.get("enable", False)),
            "commands": commands,
        }
    catalog["__source__"] = str(source)
    return catalog


def write_output(directory, filename, text):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    target = directory / filename
    fd, temporary = tempfile.mkstemp(prefix=".collect-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return target


def collect_commands(task, catalog, output_dir, emit=None):
    """Nornir task: run the platform's command set on one device and file the outputs."""
    from nornir.core.task import Result

    host = task.host
    if not host.platform:
        from .runner import detect_platform
        detect_platform(task)
    platform = canonical_platform(host.platform)
    spec = catalog.get(platform)
    records = {"platform": platform, "outputs": [], "skipped": None}
    if spec is None:
        records["skipped"] = f"no command set for platform {platform!r}"
        return Result(host=host, result=records, changed=False)
    original = host.platform
    host.platform = spec["driver"]
    try:
        connection = host.get_connection("netmiko", task.nornir.config)
        if spec["enable"]:
            try:
                connection.enable()
            except Exception as exc:  # noqa: BLE001 - recorded per device, never fatal
                records["enable_error"] = redact(str(exc))
        collected_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        for entry in spec["commands"]:
            command = entry["command"]
            filename = f"{host.name}_{slug(command)}.txt"
            record = {"command": command, "filename": filename, "ok": True, "error": "", "collected_at": collected_at}
            try:
                text = connection.send_command(command, read_timeout=entry["timeout"])
                if not isinstance(text, str):
                    text = str(text)
            except Exception as exc:  # noqa: BLE001 - one bad command must not lose the rest
                text, record["ok"], record["error"] = "", False, redact(f"{type(exc).__name__}: {exc}")[:500]
            else:
                if rejected(text):
                    record["ok"], record["error"] = False, text.strip().splitlines()[0][:200]
            text = redact(text)
            if emit:
                emit(host, command, record["ok"])
            path = write_output(Path(output_dir) / host.name, filename, text)
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            record.update(path=str(path), size=len(text.encode("utf-8")), sha256=digest)
            records["outputs"].append(record)
    finally:
        host.platform = original
        try:
            host.close_connection("netmiko")
        except Exception:
            pass
    return Result(host=host, result=records, changed=False)


def upload(client, host, records, poller, max_bytes=MAX_UPLOAD_BYTES):
    """Post each output to the discovery plugin; returns (sent, skipped) counts."""
    device_id = host.data.get("netbox_id")
    if device_id is None:
        return 0, len(records["outputs"])
    sent = skipped = 0
    for record in records["outputs"]:
        text = Path(record["path"]).read_text(encoding="utf-8")
        payload = {"device": device_id, "platform": records["platform"], "command": record["command"],
                   "filename": record["filename"], "ok": record["ok"], "error": record["error"],
                   "collected_at": record["collected_at"], "content": text}
        if poller:
            payload["poller"] = poller
        if record["size"] > max_bytes:
            payload["content"] = ""
            payload["ok"], payload["error"] = False, f"output of {record['size']} bytes exceeds the upload limit; kept on the poller at {record['path']}"
            skipped += 1
        client.request_object("POST", ENDPOINT, payload)
        record["uploaded"] = payload["ok"] == record["ok"]
        sent += 1
    return sent, skipped


def netbox_client(args):
    from .credentials import fetch_json_secret
    from .netbox import Client, settings_from
    settings = settings_from(args.standards, args)
    if getattr(args, "netbox_secret", None):
        document = fetch_json_secret(args.netbox_secret, args.aws_region)
        settings["token"] = settings["token"] or document.get("token")
        settings["url"] = settings["url"] or document.get("url")
    return Client(settings["url"], settings["token"], settings["verify_tls"])


def run(args, style, log):
    from .cli import EXIT_FAILED, EXIT_OK, EXIT_USAGE, PROJECT_ROOT, _connect, _exception_of
    from .errors import summarize
    from .netbox import NetBoxError
    from .standards import Standards, StandardsError, load as load_standards

    try:
        args.standards = Standards() if getattr(args, "no_standards", False) else load_standards(args.standards, PROJECT_ROOT)
        catalog = load_catalog(args.commands, PROJECT_ROOT)
    except (StandardsError, CatalogError) as exc:
        print(style.bad(f"error: {exc}"), file=sys.stderr)
        return EXIT_USAGE
    targets, credentials, code = _connect(args, style)
    if targets is None:
        return code
    output_dir = Path(args.output_dir or os.environ.get("NETOPS_COLLECT_DIR") or PROJECT_ROOT / "collected")
    print(f"collecting from {len(targets.inventory.hosts)} device(s), {min(args.workers, len(targets.inventory.hosts))} at a time; "
          f"catalog {catalog['__source__']}; files under {output_dir}")
    print(f"credentials: {credentials.describe()}")
    results = targets.run(task=collect_commands, catalog=catalog, output_dir=output_dir)

    client = None
    upload_enabled = bool(args.netbox and not args.no_upload)
    if upload_enabled:
        try:
            client = netbox_client(args)
        except NetBoxError as exc:
            print(style.warn(f"outputs kept on the poller only; NetBox upload unavailable: {exc}"), file=sys.stderr)
            client = None
    poller = getattr(args, "poller", None) or os.environ.get("NETOPS_POLLER") or ""
    poller = poller.lower().removeprefix("poller-") if poller else ""

    failed = 0
    records = {}
    for name in sorted(results):
        result, host = results[name], targets.inventory.hosts[name]
        if result.failed:
            exception = _exception_of(result)
            message = redact(summarize(exception) if exception else "unknown error")
            failed += 1
            log.failure(name, message, exception)
            records[name] = {"status": "failed", "error": message}
            print(f"  {style.bold(name)} {style.bad('FAILED')} -- {message}")
            continue
        data = result.result
        if data.get("skipped"):
            records[name] = {"status": "skipped", "skip_reason": data["skipped"], "platform": data["platform"]}
            print(f"  {style.bold(name)} [{data['platform']}] {style.dim('skipped')} -- {data['skipped']}")
            continue
        outputs = data["outputs"]
        bad = [o for o in outputs if not o["ok"]]
        line = f"  {style.bold(name)} [{data['platform']}] {len(outputs)} command(s), {len(outputs) - len(bad)} ok"
        if bad:
            line += ", " + style.warn(f"{len(bad)} rejected: " + ", ".join(o["command"] for o in bad[:4]) + (" ..." if len(bad) > 4 else ""))
        if data.get("enable_error"):
            line += style.warn(f"; enable failed: {data['enable_error']}")
        if client is not None:
            try:
                sent, oversized = upload(client, host, data, poller)
                line += style.dim(f"; {sent} filed in NetBox" + (f", {oversized} too large to upload" if oversized else ""))
            except NetBoxError as exc:
                line += style.bad(f"; NetBox upload failed: {redact(str(exc))}")
                failed += 1
        print(line)
        records[name] = {"status": "completed", "platform": data["platform"],
                         "outputs": [{k: v for k, v in o.items()} for o in outputs]}
    archive.capture(records)
    total = len(results)
    summary = [f"{total} device(s)", f"{sum(1 for r in records.values() if r['status'] == 'completed')} collected"]
    skipped = sum(1 for r in records.values() if r["status"] == "skipped")
    if skipped:
        summary.append(style.dim(f"{skipped} without a command set"))
    if failed:
        summary.append(style.bad(f"{failed} failed"))
    print()
    print(style.bold("summary: ") + ", ".join(summary))
    return EXIT_FAILED if failed else EXIT_OK
