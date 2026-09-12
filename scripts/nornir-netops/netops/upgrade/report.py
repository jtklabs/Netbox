"""Readable before/after comparison for one upgraded device, written beside its archive.

Tables are compared as unordered sets of their stable fields, so a reordered
MAC or ARP table is not a difference. Ages, uptimes, timers and timestamps are
excluded from parsed tables and masked in raw diagnostics; counter and log
output is listed as not compared rather than diffed line by line.
"""

import difflib
import html
import json
import os
import re
import tempfile
from collections import Counter, namedtuple
from pathlib import Path

from .. import archive
from . import checks

NOT_COMPARED = {
    "show logging": "log", "show clock detail": "clock", "show ntp associations": "see Health",
    "show processes cpu sorted": "counters, see Health", "show processes memory sorted": "counters, see Health",
    "show interfaces counters errors": "counters, see Health", "show aaa servers": "counters",
    "show radius statistics": "counters", "show access-session details": "session identifiers",
    "show ipv6 neighbors": "ages",
}
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
UNIT = r"(?:years?|weeks?|days?|hours?|minutes?|mins?|seconds?|secs?)"
MASKS = (
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(?:\.\d{1,3})?\b"), "<time>"),
    (re.compile(r"\b(?:(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?" + MONTHS + r"\s+\d{1,2}(?:\s+\d{4})?\b"), "<date>"),
    (re.compile(r"\b\d+\s*" + UNIT + r"\b(?:,?\s*\d+\s*" + UNIT + r"\b)*", re.I), "<uptime>"),
    (re.compile(r"\b\d+[wdhms](?:\d+[wdhms])+\b"), "<uptime>"),
    (re.compile(r"(?<![\w/])-?\d+(?:\.\d+)?\s*(?:C|F|RPM|%|W|V|mA|dBm|Celsius)\b"), "<reading>"),
)
LIMIT = 50


def masked(line):
    for pattern, token in MASKS:
        line = pattern.sub(token, line)
    return line


def line_delta(before, after):
    old = Counter(masked(line.rstrip()) for line in before.splitlines() if line.strip())
    new = Counter(masked(line.rstrip()) for line in after.splitlines() if line.strip())
    return sorted((old - new).elements()), sorted((new - old).elements())


def table_delta(old_rows, new_rows):
    old = Counter(checks.canonical(row) for row in old_rows)
    new = Counter(checks.canonical(row) for row in new_rows)
    return {"unchanged": sum((old & new).values()),
            "removed": [json.loads(row) for row in (old - new).elements()],
            "added": [json.loads(row) for row in (new - old).elements()]}


def render_row(row):
    return ", ".join(f"{key}={'/'.join(map(str, value)) if isinstance(value, list) else value}"
                     for key, value in row.items()).replace("|", "\\|")


def bullets(title, rows):
    lines = [f"{title} ({len(rows)}):"] + [f"- {render_row(row)}" for row in rows[:LIMIT]]
    if len(rows) > LIMIT:
        lines.append(f"- ... {len(rows) - LIMIT} more in the archive")
    return lines


def delta_section(title, before, after):
    lines = [f"## {title}", "", "| Item | Before | After | Unchanged | Removed | Added |", "| --- | --- | --- | --- | --- | --- |"]
    details = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old is None or new is None:
            lines.append(f"| {key} | {'-' if old is None else len(old)} | {'-' if new is None else len(new)} | | | not collected on one side |")
            continue
        delta = table_delta(old, new)
        lines.append(f"| {key} | {len(old)} | {len(new)} | {delta['unchanged']} | {len(delta['removed'])} | {len(delta['added'])} |")
        if delta["removed"] or delta["added"]:
            details += [f"### {key}", ""]
            if delta["removed"]:
                details += bullets("Removed", delta["removed"]) + [""]
            if delta["added"]:
                details += bullets("Added", delta["added"]) + [""]
    return lines + [""] + details


def software_section(before, after):
    lines = ["## Software and stack", "", "| Member | Model | Version before | Version after | Mode before | Mode after | Stack before | Stack after |",
             "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    old, new = before.get("software", {}), after.get("software", {})
    stack_old, stack_new = before.get("stack", {}), after.get("stack", {})
    for member in sorted(set(old) | set(new), key=int):
        a, b = old.get(member, {}), new.get(member, {})
        lines.append(f"| {member} | {b.get('model') or a.get('model', '?')} | {a.get('version', '-')} | {b.get('version', '-')} "
                     f"| {a.get('mode', '-')} | {b.get('mode', '-')} | {stack_old.get(member, {}).get('state', '-')} | {stack_new.get(member, {}).get('state', '-')} |")
    return lines + [""]


def config_section(before, after):
    lines = ["## Running configuration", ""]
    old = checks.normalized_config(before.get("config", ""), boot=True)
    new = checks.normalized_config(after.get("config", ""), boot=True)
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), fromfile="before", tofile="after", lineterm="", n=2))
    if not before.get("config") or not after.get("config"):
        lines.append("Not compared: the running configuration was not read on one side.")
    elif diff:
        lines += ["Boot settings excluded; they are listed below.", "", "```diff", *diff[:400], *(["... truncated; the archive holds both configurations"] if len(diff) > 400 else []), "```"]
    else:
        lines.append("No differences (boot settings excluded).")
    boot = lambda text: ", ".join(re.findall(r"(?m)^boot system (.+)$", text)) or "none"
    lines += ["", f"Boot settings: before `{boot(before.get('config', ''))}`; after `{boot(after.get('config', ''))}`."]
    if after.get("config") and after.get("startup_config"):
        lines.append("Startup-config after upgrade: " + ("identical to running-config." if after["config"] == after["startup_config"] else "differs from running-config."))
    return lines + [""]


def health_section(before, after):
    old, new = before.get("health", {}), after.get("health", {})
    lines = ["## Health", ""]
    cpu = lambda h: h.get("cpu_percent", {}).get("one_minute", "-")
    lines.append(f"- CPU one-minute: {cpu(old)}% before, {cpu(new)}% after")
    lines.append(f"- Processor memory free: {old.get('memory_free_percent', '-')}% before, {new.get('memory_free_percent', '-')}% after")
    if old.get("ntp") or new.get("ntp"):
        lines += ["- NTP peers (server, selected, reachable, stratum):"]
        for label, rows in (("before", old.get("ntp", [])), ("after", new.get("ntp", []))):
            lines.append(f"  - {label}: " + ("; ".join(f"{r['server']} {'selected' if r['selected'] else 'not selected'}, "
                                                        f"{'reachable' if r['reachable'] else 'unreachable'}, stratum {r['stratum']}" for r in rows) or "none"))
    alarms = sorted(set(new.get("alarms", [])) - set(old.get("alarms", [])))
    lines.append("- New environment alarms: " + ("; ".join(alarms) if alarms else "none"))
    grown = []
    for interface, counters in new.get("interface_errors", {}).items():
        previous = old.get("interface_errors", {}).get(interface, {})
        active = {k: v - previous.get(k, 0) for k, v in counters.items() if v > previous.get(k, 0)}
        if active:
            grown.append(f"{interface} " + ", ".join(f"{k} +{v}" for k, v in active.items()))
    lines.append("- Interface error counters that increased: " + ("; ".join(grown[:LIMIT]) if grown else "none"))
    return lines + [""]


def diagnostics_section(before, after):
    lines = ["## Diagnostics (line-level, order ignored; times, dates, uptimes and readings masked)", ""]
    skipped = []
    for command in checks.DIAGNOSTICS:
        if command in NOT_COMPARED:
            skipped.append(f"{command} ({NOT_COMPARED[command]})")
            continue
        old, new = before.get("raw", {}).get(command), after.get("raw", {}).get(command)
        if old is None and new is None:
            continue
        if old is None or new is None or not isinstance(old, str) or not isinstance(new, str):
            lines.append(f"- `{command}`: not collected on one side")
            continue
        only_before, only_after = line_delta(old, new)
        if not only_before and not only_after:
            lines.append(f"- `{command}`: no differences")
            continue
        lines.append(f"- `{command}`: {len(only_before)} line(s) only before, {len(only_after)} only after")
        for label, rows in (("only before", only_before), ("only after", only_after)):
            for row in rows[:40]:
                lines.append(f"    - {label}: `{row.strip()}`")
            if len(rows) > 40:
                lines.append(f"    - ... {len(rows) - 40} more")
    lines += ["", "Not compared: " + "; ".join(skipped) + "."]
    return lines + [""]


def build(host, before, after, findings, plan, status):
    errors = [f for f in findings if f.get("severity") == "error"]
    warnings = [f for f in findings if f.get("severity") != "error"]
    versions = lambda snap: ", ".join(sorted({row["version"] for row in snap.get("software", {}).values()})) or "?"
    lines = [f"# Upgrade comparison: {host.name} ({host.hostname})", "",
             f"- Outcome: **{status}**",
             f"- Profile: {plan.get('profile', '?')}; target {plan.get('target_version', '?')}",
             f"- Software: {versions(before)} before, {versions(after)} after",
             "- Method: tables are compared as unordered sets of stable fields; ages, uptimes, timers and timestamps are excluded or masked.",
             "", "## Findings", "",
             f"Errors ({len(errors)}):"] + [f"- {checks.describe_finding(f)}" for f in errors] + ["", f"Warnings ({len(warnings)}):"] + [
             f"- {f['check']}: {f.get('message', f.get('interface', ''))}" for f in warnings] + [""]
    lines += software_section(before, after)
    lines += config_section(before, after)
    lines += delta_section("Tables", before.get("tables", {}), after.get("tables", {}))
    lines += delta_section("Routing neighbors", before.get("routing", {}), after.get("routing", {}))
    lines += health_section(before, after)
    lines += diagnostics_section(before, after)
    return "\n".join(lines).rstrip() + "\n"


def write(target, text):
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".diff-", suffix=".md", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


STYLE = """body{font:14px/1.45 -apple-system,'Segoe UI',Helvetica,Arial,sans-serif;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#1b1f23;background:#fff}
h1{font-size:1.5rem}h2{margin-top:2rem;border-bottom:1px solid #d0d7de;padding-bottom:.25rem}h3{margin-top:1.25rem}
table{border-collapse:collapse;margin:.5rem 0}th,td{border:1px solid #d0d7de;padding:.25rem .6rem;text-align:left;font-variant-numeric:tabular-nums}th{background:#f6f8fa}
code,pre{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}code{background:#f6f8fa;padding:.1rem .3rem;border-radius:3px}
pre{background:#f6f8fa;padding:.75rem;overflow-x:auto;border-radius:6px}pre code{background:none;padding:0}
.add{color:#116329;background:#dafbe1;display:block}.del{color:#82071e;background:#ffebe9;display:block}.hunk{color:#57606a;display:block}
ul{padding-left:1.4rem}li{margin:.15rem 0}"""


def inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", text)


def cells(row):
    return [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]


def to_html(markdown, title):
    """Render the report's own Markdown subset as a self-contained page."""
    out, lines, index, depth = [], markdown.splitlines(), 0, 0

    def close(to=0):
        nonlocal depth
        while depth > to:
            out.append("</ul>")
            depth -= 1

    while index < len(lines):
        line = lines[index]
        if line.startswith("```"):
            close()
            language, block, index = line[3:].strip(), [], index + 1
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            rendered = []
            for row in block:
                kind = ("hunk" if row.startswith(("@@", "+++", "---")) else "add" if row.startswith("+")
                        else "del" if row.startswith("-") else "") if language == "diff" else ""
                rendered.append(f'<span class="{kind}">{html.escape(row, quote=False)}</span>' if kind else html.escape(row, quote=False))
            out.append("<pre><code>" + "\n".join(rendered) + "</code></pre>")
            continue
        if line.startswith("|"):
            close()
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(lines[index])
                index += 1
            header, body = cells(rows[0]), rows[1:]
            if body and set(body[0].replace("|", "").strip()) <= set("- "):
                body = body[1:]
            out.append("<table><thead><tr>" + "".join(f"<th>{inline(c)}</th>" for c in header) + "</tr></thead><tbody>"
                       + "".join("<tr>" + "".join(f"<td>{inline(c)}</td>" for c in cells(r)) + "</tr>" for r in body) + "</tbody></table>")
            continue
        heading = re.match(r"^(#{1,3})\s+(.*)$", line)
        if heading:
            close()
            level = len(heading[1])
            out.append(f"<h{level}>{inline(heading[2])}</h{level}>")
        elif (bullet := re.match(r"^(\s*)- (.*)$", line)):
            wanted = 1 + len(bullet[1]) // 4
            close(wanted)
            while depth < wanted:
                out.append("<ul>")
                depth += 1
            out.append(f"<li>{inline(bullet[2])}</li>")
        else:
            close()
            if line.strip():
                out.append(f"<p>{inline(line)}</p>")
        index += 1
    close()
    return ("<!DOCTYPE html>\n<html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
            f"<title>{html.escape(title, quote=False)}</title><style>{STYLE}</style></head><body>\n" + "\n".join(out) + "\n</body></html>\n")


Built = namedtuple("Built", "markdown html path")


def generate(reporter, host, before, after, findings, plan, status):
    """Build the Markdown and HTML comparison; write both beside the archive when there is one."""
    markdown = archive.clean(build(host, before, after, findings, plan, status))
    page = to_html(markdown, f"Upgrade comparison: {host.name}")
    path = getattr(getattr(reporter, "run", None), "path", None)
    if not isinstance(path, Path):
        return Built(markdown, page, None)
    target = path.with_name(f"{path.stem}_{re.sub(r'[^A-Za-z0-9_.-]+', '_', host.name)[:80]}.diff.md")
    write(target, markdown)
    write(target.with_suffix(".html"), page)
    return Built(markdown, page, target)
