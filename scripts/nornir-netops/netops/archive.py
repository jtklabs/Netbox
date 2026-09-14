"""Versioned, private JSON archives for every CLI invocation."""

from __future__ import annotations

import argparse
import contextvars
import copy
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .debuglog import protect, redact

ACTIVE = contextvars.ContextVar("run_archive", default=None)
SECRET_KEYS = {"password", "authpassword", "privacypassword", "authpasswordencrypted",
               "privacypasswordencrypted", "communityname", "secret_value", "token",
               "awssecretkey", "client_secret"}


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def clean(value):
    if isinstance(value, dict):
        return {str(k): "<redacted>" if str(k).lower() in SECRET_KEYS else clean(v)
                for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, str):
        # REST command strings contain JSON-escaped secrets; clean their body
        # structurally before redacting known credential values.
        match = re.match(r"^((?:PATCH|POST|PUT|DELETE) /\S+ )({.*})$", value, re.S)
        if match:
            try:
                return redact(match[1]) + json.dumps(clean(json.loads(match[2])))
            except ValueError:
                pass
        return redact(value)
    return value


def snapshot(entries):
    return clean([{"key": e.key, "line": e.shown, "data": e.data} for e in entries])


def arguments(parser):
    group = parser.add_argument_group("JSON archive")
    group.add_argument("--report", metavar="FILE", help="archive JSON at this exact path; otherwise use a unique file in --report-dir")
    group.add_argument("--report-dir", metavar="DIR", default=os.environ.get("NETOPS_REPORT_DIR"),
                       help="automatic JSON archive directory [$NETOPS_REPORT_DIR; default: <project>/reports]")


def current():
    return ACTIVE.get()


def configure(args, desired=None):
    run = current()
    if run:
        run.args = args
        run.desired = desired
        run.document.update(feature=args.command, dry_run=not getattr(args, "apply", False))
        if desired:
            protect(desired.secrets)
            run.document.update(desired=desired.keys, variables=clean(desired.variables))


def capture(records):
    run = current()
    if run:
        with run.lock:
            for name, record in records.items():
                run.records.setdefault(name, {}).update(record)
            run.write()


def targets(inventory):
    run = current()
    if run:
        for name, host in inventory.hosts.items():
            run.hosts[name] = host
            run.records.setdefault(name, {}).update(hostname=host.hostname, platform=host.platform,
                                                     status="not_started")
        run.write()


def checkpoint(task, payload):
    """Persist a plan before the first possible device write."""
    run = current()
    if run:
        run.record(task.host, payload, changed=True, status="applying")


def failure(message):
    run = current()
    if run:
        run.document["error"] = redact(str(message))


def step(command, purpose="configure"):
    match = re.match(r"^(PATCH|POST|DELETE|PUT) (/\S+)(?: (.*))?$", command, re.S)
    if match:
        method, path, body = match.groups()
        try:
            return {"transport": "rest", "method": method, "path": path,
                    "body": json.loads(body) if body else None, "purpose": purpose}
        except ValueError:
            pass  # A banner can contain text resembling an HTTP command.
    return {"transport": "ssh", "command": command, "purpose": purpose}


def action(args, desired, host, record):
    feature = getattr(args, "command", None)
    if feature == "upgrade":
        operation = "stage_image" if getattr(args, "stage_only", False) else "upgrade"
        return {"action": operation if getattr(args, "apply", False) else "audit",
                "action_source": "profile", "action_from_netbox": False, "netbox_policy_tag": None}
    if feature in ("check-ntp", "discover", "selftest", "rollback", "collect"):
        return {"action": "rollback" if feature == "rollback" else "audit",
                "action_source": "utility", "action_from_netbox": False, "netbox_policy_tag": None}
    selected = (desired.variables if desired else {}).get("logging_policy")
    from_netbox = feature in ("waf", "syslog") and selected == "netbox"
    tag = None
    if from_netbox:
        from .features.waf import POLICY_TAGS
        tags = str(host.data.get("tags", "")).split(",") if host else []
        matches = [t.strip() for t in tags if t.strip() in POLICY_TAGS]
        tag = matches[0] if len(matches) == 1 else None
        resolved = POLICY_TAGS[tag] if tag else "audit" if not matches else None
        source = "netbox"
    elif selected:
        resolved = selected
        source = "cli" if (getattr(args, "logging_policy", None) or getattr(args, "explicit_mode", False)) else (
            "environment" if os.environ.get("NETOPS_SYSLOG_POLICY") or os.environ.get("NETOPS_F5_POLICY") else "default")
    else:
        resolved = getattr(args, "mode", "add")
        source = "cli" if getattr(args, "explicit_mode", False) else "default"
    return {"action": "manage" if resolved == "replace" else resolved,
            "action_source": source, "action_from_netbox": from_netbox, "netbox_policy_tag": tag}


def device_document(name, record, args, desired, host):
    row = dict(record)
    if host and host.data.get("poller_selection"):
        row["poller_selection"] = host.data["poller_selection"]
    row.update(action(args, desired, host, row))
    feature = getattr(args, "command", None)
    read_steps = []
    definition = getattr(args, "feature", None)
    scope = definition.name if definition else feature
    support = definition.platforms.get(row.get("platform")) if definition else None
    if support:
        read_steps = [{"transport": "ssh", "command": c, "purpose": "read_config"} for c in support.commands]
    if row.get("platform") == "f5_tmsh":
        paths = {"waf": [p["endpoint"] for p in row.get("profiles", [])],
                 "syslog": ["/mgmt/tm/sys/syslog"],
                 "snmp": ["/mgmt/tm/sys/snmp", "/mgmt/tm/sys/snmp/users", "/mgmt/tm/sys/snmp/communities"],
                 "banner": ["/mgmt/tm/sys/sshd", "/mgmt/tm/sys/global-settings"]}.get(feature, [])
        read_steps = [{"transport": "rest", "method": "GET", "path": p, "purpose": "read_config"} for p in paths]
    before = row.get("config_before")
    after = row.get("config_after")
    rollback_steps = list(row.get("rollback_steps", []))
    limitations = list(row.get("rollback_unsupported", []))
    if feature == "waf" and row.get("config_read_complete"):
        before = [{"path": p["endpoint"], "servers": p["before_servers"]} for p in row["profiles"]]
        observed = [{"path": p["endpoint"], "servers": p["after_servers"]}
                    for p in row["profiles"] if "after_servers" in p]
        after = observed if len(observed) == len(row["profiles"]) and observed else None
        rollback_steps = [{"transport": "rest", "method": "PATCH", "path": p["endpoint"],
                           "body": {"servers": p["before_servers"]}, "purpose": "restore"}
                          for p in row["profiles"] if p.get("add") or p.get("remove")]
        limitations = []
    elif feature == "syslog" and "before_servers" in row:
        before = {"remoteServers": row["before_servers"]}
        if row.get("commands"):
            rollback_steps = [{"transport": "rest", "method": "PATCH", "path": "/mgmt/tm/sys/syslog",
                               "body": before, "purpose": "restore"}]
        limitations = []
    elif feature == "banner" and row.get("banners"):
        before = [{"path": b["endpoint"], "config": b["before"]} for b in row["banners"]]
        observed = [{"path": b["endpoint"], "config": b["after"]} for b in row["banners"] if "after" in b]
        after = observed if len(observed) == len(row["banners"]) and observed else None
        rollback_steps = [{"transport": "rest", "method": "PATCH", "path": b["endpoint"],
                           "body": b["before"], "purpose": "restore"}
                          for b in row["banners"] if not b["compliant"]]
        limitations = []
    if not rollback_steps:
        rollback_steps = [step(c, "restore") for c in row.get("rollback", [])]
    if rollback_steps:
        rollback_steps.extend({**s, "purpose": "verify_restored_config"} for s in read_steps)
    if rollback_steps and getattr(args, "save", True):
        from .core import SAVE_COMMANDS
        save = 'POST /mgmt/tm/sys/config {"command": "save"}' if row.get("platform") == "f5_tmsh" else SAVE_COMMANDS.get(row.get("platform"))
        if save:
            rollback_steps.append(step(save, "persist_restored_config"))
    implementation = [step(c) for c in row.get("commands", [])]
    if implementation and getattr(args, "verify", True):
        implementation.extend({**s, "purpose": "verify_config"} for s in read_steps)
    if row.get("save_command"):
        implementation.append(step(row["save_command"], "persist_config"))
    if any(marker in json.dumps(rollback_steps) for marker in ("<redacted>", "<hidden>", "<previous-secret-required>")):
        limitations.append("Restoring previous credentials requires secrets that are not stored in this archive.")
    changed = bool(row.get("change_attempted", False) or row.get("applied", False))
    if after is not None:
        after_status = "observed"
    elif changed:
        after_status = "unknown"
    elif before is not None:
        after_status = "not_changed"
        after = before
    else:
        after_status = "unavailable"
    if before is None and feature not in ("selftest", "discover", "check-ntp", "collect"):
        limitations.append("The original feature configuration was not read; a complete reversal cannot be established.")
    row.update(
        configuration_scope=scope,
        current_config={"status": "observed" if before is not None else "unavailable", "config": before,
                        "read_steps": read_steps},
        implementation={"steps": implementation, "changes_needed": bool(row.get("commands")),
                        "will_execute": bool(implementation) and bool(getattr(args, "apply", False)) and row["action"] != "audit"},
        backout={"steps": rollback_steps, "complete": not limitations and (bool(rollback_steps) or not implementation),
                 "limitations": limitations,
                 "instructions": "Run restore steps in order, then read the feature configuration again and compare with current_config. Secret placeholders must be supplied before execution."},
        result_after={"status": after_status, "config": after, "applied": row.get("applied", False),
                      "verified": row.get("verified"), "saved": row.get("saved"),
                      "status_of_run": row.get("status"), "error": row.get("error")},
    )
    return clean(row)


class Run:
    def __init__(self, argv, project_root):
        self.argv = argv
        self.root = project_root
        self.path = None
        self.lock = threading.RLock()
        self.records, self.hosts = {}, {}
        self.args = argparse.Namespace(command=argv[0] if argv and not argv[0].startswith("-") else "help")
        self.desired = None
        self.document = {"schema_version": 2, "run_id": str(uuid.uuid4()), "started_at": now(),
                         "finished_at": None, "feature": self.args.command, "status": "running", "dry_run": True,
                         "error": None, "devices": {}}
        # Protect CLI credentials even if argument parsing subsequently fails.
        for i, arg in enumerate(argv):
            key, sep, value = arg.partition("=")
            if key in ("--password", "--secret"):
                protect([value if sep else argv[i + 1] if i + 1 < len(argv) else ""])

    def prepare(self):
        # Avoid argparse here: even malformed invocations need a final archive.
        options = {"--report": None, "--report-dir": os.environ.get("NETOPS_REPORT_DIR")}
        for i, arg in enumerate(self.argv):
            key, sep, value = arg.partition("=")
            if key in options:
                candidate = value if sep else self.argv[i + 1] if i + 1 < len(self.argv) else None
                if candidate and not candidate.startswith("--"):
                    options[key] = candidate
        slug = re.sub(r"[^a-zA-Z0-9_-]", "_", self.args.command)[:80]
        filename = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + "_" + slug + "_" + self.document["run_id"][:8] + ".json"
        self.path = Path(options["--report"]).expanduser() if options["--report"] else Path(options["--report-dir"] or self.root / "reports").expanduser() / filename
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write()

    def record(self, host, payload, failed=False, changed=False, status=None):
        with self.lock:
            self.document["schema_version"] = 2
            self.hosts[host.name] = host
            row = self.records.setdefault(host.name, {})
            row.update(copy.deepcopy(payload), hostname=host.hostname, platform=host.platform,
                       change_attempted=changed or row.get("change_attempted", False))
            row["status"] = status or ("failed" if failed else "completed")
            self.write()

    def write(self):
        if self.path is None:
            return
        with self.lock:
            self.document["schema_version"] = 2
            self.document["devices"] = {n: device_document(n, r, self.args, self.desired, self.hosts.get(n))
                                        for n, r in self.records.items()}
            actions = {r["action"] for r in self.document["devices"].values()}
            self.document["action"] = next(iter(actions)) if len(actions) == 1 else "mixed" if actions else action(self.args, self.desired, None, {})["action"]
            self.document["action_from_netbox"] = any(r["action_from_netbox"] for r in self.document["devices"].values())
            data = json.dumps(clean(self.document), indent=2, sort_keys=True, default=str) + "\n"
            fd, temp = tempfile.mkstemp(prefix=".run-", suffix=".json", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temp, self.path)
            finally:
                if os.path.exists(temp):
                    os.unlink(temp)

    def finish(self, code):
        self.document.update(finished_at=now(), exit_code=code,
                             status="completed" if code == 0 else "attention" if code == 2 and self.records else "interrupted" if code == 130 else "failed")
        if code and not self.document["error"] and not self.records:
            self.document["error"] = "Run did not reach device processing; see the CLI diagnostic for the settings or syntax error."
        if self.path is None:
            self.prepare()
        self.write()
