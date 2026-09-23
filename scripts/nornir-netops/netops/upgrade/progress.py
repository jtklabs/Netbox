"""Durable per-device progress events, suitable for an at-least-once UI feed."""

import json
import os
import threading
import time

from .. import archive, webhook


def settings_from_env():
    # Reuse the transport's URL/token validation without changing process env
    # (workers share it). The upgrade feed is independent of the NAC report.
    return webhook.settings_from_env(prefix="NETOPS_UPGRADE_WEBHOOK")


def report_in_webhook():
    """Whether the final comparison report rides along on the last webhook event."""
    value = os.environ.get("NETOPS_UPGRADE_WEBHOOK_REPORT", "true").strip().lower()
    if value not in ("true", "false"):
        raise webhook.WebhookError("NETOPS_UPGRADE_WEBHOOK_REPORT must be true or false")
    return value == "true"


class Reporter:
    def __init__(self, run, settings=None, retries=3, event_sink=None, attach_reports=None):
        self.run = run
        self.settings = settings
        self.retries = retries
        self.lock = threading.Lock()
        self.sequences = {}
        self.delivery_failed = False
        self.event_sink = event_sink
        self.attach_reports = report_in_webhook() if attach_reports is None else attach_reports

    def emit(self, host, stage, message, payload=None, changed=False, attachment=None):
        """Record and deliver one event. An attachment goes only to the webhook body."""
        with self.lock:
            sequence = self.sequences.get(host.name, 0) + 1
            self.sequences[host.name] = sequence
        event = {"schema_version": 1, "event_type": "upgrade.progress",
                 "run_id": self.run.document["run_id"], "device": host.name,
                 "device_id": host.data.get("netbox_id"), "hostname": host.hostname,
                 "site": host.data.get("site"), "role": host.data.get("role"),
                 "sequence": sequence, "timestamp": archive.now(),
                 "dry_run": not getattr(self.run.args, "apply", False),
                 "operation": ("remediate" if getattr(self.run.args, "queue_operation", None) == "remediate" else
                               "stage_image" if getattr(self.run.args, "stage_only", False) else "upgrade"),
                 "stage": stage, "message": message}
        event["event_id"] = f"{event['run_id']}:{host.name}:{sequence}"
        if self.run.document.get("schedule"):
            event["scheduled_job_id"] = self.run.document["schedule"]["job_id"]
            event["batch_id"] = self.run.document["schedule"].get("batch_id")
        if payload and "progress_summary" in payload:
            event["summary"] = payload["progress_summary"]
        # The archive is written before HTTP delivery and before device writes.
        with self.run.lock:
            previous = self.run.records.get(host.name, {}).get("upgrade_events", [])
            self.run.record(host, dict(payload or {}, upgrade_events=previous + [dict(event, delivery="pending" if self.settings else "disabled")]),
                            status=stage, changed=changed)
        print(f"{host.name}: {stage} — {message}", flush=True)
        delivered = self.settings is None
        if self.settings:
            body = json.dumps(archive.clean(dict(event, **attachment) if attachment and self.attach_reports else event))
            for attempt in range(self.retries):
                try:
                    webhook.send(self.settings, body)
                    delivered = True
                    break
                except webhook.WebhookError:
                    if attempt + 1 < self.retries:
                        time.sleep(2 ** attempt)
            with self.run.lock:
                events = self.run.records[host.name]["upgrade_events"]
                for stored in events:
                    if stored["event_id"] == event["event_id"]:
                        stored["delivery"] = "delivered" if delivered else "failed"
                self.run.write()
            if not delivered:
                self.delivery_failed = True
                print(f"{host.name}: webhook delivery failed; event retained in archive", flush=True)
        # A scheduled job must receive NetBox's start authorization as well as
        # deliver its configured UI webhook before the workflow changes boot
        # settings or installs. The --apply configuration save during prechecks
        # is the one device write that precedes this gate.
        if self.event_sink and (stage != "ready" or delivered):
            acknowledged = self.event_sink(archive.clean(event))
            if not acknowledged:
                self.delivery_failed = True
            with self.run.lock:
                self.run.records[host.name]["upgrade_events"][-1]["netbox_delivery"] = (
                    "delivered" if acknowledged else "failed")
                self.run.write()
            delivered = delivered and acknowledged
        return delivered
