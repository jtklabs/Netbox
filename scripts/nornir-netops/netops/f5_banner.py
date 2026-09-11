"""BIG-IP SSH and Configuration utility security banners."""

from __future__ import annotations

import json

from . import f5_waf
from .core import render

SURFACES = (
    ("SSH login banner", "/mgmt/tm/sys/sshd", "banner", "bannerText"),
    ("Web login banner", "/mgmt/tm/sys/global-settings", "guiSecurityBanner", "guiSecurityBannerText"),
)


def banner_text(kinds, variables):
    from .features.banner import normalize_body

    bodies = [normalize_body(render("banner", "f5_tmsh", [kind], [], variables, keep_blank=True))
              for kind in kinds]
    if not bodies or any(not body.strip() for body in bodies):
        raise ValueError("F5 banner template must produce non-empty text for every selected banner")
    return "\n\n".join(bodies)


def plan(document, surface, text):
    from .features.banner import normalize_body

    label, endpoint, enabled_key, text_key = surface
    if not isinstance(document, dict) or document.get(enabled_key) not in ("enabled", "disabled"):
        raise ValueError(f"{label}: missing or invalid banner enablement in F5 response")
    before_text = document.get(text_key, "")
    if not isinstance(before_text, str):
        raise ValueError(f"{label}: invalid banner text in F5 response")
    before = {enabled_key: document[enabled_key], text_key: before_text}
    wanted = {enabled_key: "enabled", text_key: text}
    matches = (document[enabled_key] == "enabled" and
               normalize_body(before_text.splitlines()) == normalize_body(text.splitlines()))
    return {"label": label, "endpoint": endpoint, "before": before,
            "desired": wanted, "compliant": matches, "applied": False, "verified": None}


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result

    payload = {"platform": "f5_tmsh", "mode": mode, "current": [], "desired": [],
               "add": [], "remove": [], "commands": [], "save_command": None,
               "compliant": False, "advisories": [], "notes": [], "rollback": [],
               "rollback_unsupported": [], "applied": False, "saved": None,
               "skipped": False, "skip_reason": None, "verified": None,
               "missing_after": [], "output": None, "save_output": None, "banners": []}
    attempted = False
    try:
        text = banner_text(desired, variables)
        payload["desired"] = list(desired)
        payload["notes"].append("F5 uses the selected banner text for both SSH and web login; add and replace reconcile the same two settings")
        if len(desired) > 1:
            payload["notes"].append("Selected MOTD and login texts are combined with a blank line on F5")
        if not task.host.username or not task.host.password:
            raise ValueError("F5 REST requires a username and password")
        with f5_waf.Client(task.host, **variables["f5"]) as client:
            # Read both surfaces before writing either. Never copy unrelated
            # global settings (which may include secrets) into the report.
            rows = [plan(client.get_json(surface[1]), surface, text) for surface in SURFACES]
            payload["banners"] = rows
            changes = [row for row in rows if not row["compliant"]]
            payload["compliant"] = not changes
            payload["current"] = [row["label"] + ": " + json.dumps(row["before"]) for row in rows]
            payload["add"] = [row["label"] for row in changes]
            payload["commands"] = ["PATCH " + row["endpoint"] + " " + json.dumps(row["desired"])
                                   for row in changes]
            if changes:
                payload["rollback_unsupported"] = ["F5 banner rollback is manual: PATCH each banner's before fields from --report"]
                payload["notes"].extend(payload["rollback_unsupported"])
                if save:
                    payload["save_command"] = 'POST /mgmt/tm/sys/config {"command": "save"}'
            if changes and not dry_run:
                for row in changes:
                    attempted = True
                    client.patch_json(row["endpoint"], row["desired"])
                    row["applied"] = True
                payload["applied"] = True
                if verify:
                    for row, surface in zip(rows, SURFACES):
                        after = plan(client.get_json(surface[1]), surface, text)
                        row["after"] = after["before"]
                        row["verified"] = after["compliant"]
                    payload["missing_after"] = [row["label"] for row in rows if not row["verified"]]
                    payload["verified"] = not payload["missing_after"]
                if save:
                    payload["saved"] = False
                    if payload["verified"] is not False:
                        client.save_config()
                        payload["saved"] = True
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        return Result(host=task.host, result=payload, changed=attempted, failed=True, exception=exc)
