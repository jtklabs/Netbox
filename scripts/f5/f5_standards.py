#!/usr/bin/env python3
"""Apply our BIG-IP standards to one or more units.

The standards themselves live in `scripts/standards.yaml` — one platform-neutral
file holding the same SNMP pollers and syslog collectors we use everywhere. This
tool maps them onto BIG-IP config:

  snmp.allow           ->  sys snmp allowed-addresses   (SNMP Client Allow List)
  syslog.destinations  ->  sys syslog remote-servers    (Remote Logging)

Nothing is written without `--commit`. A plain run connects, compares each unit
against the file, and prints what it would add, what it would remove, and what is
already compliant.

  ./f5_standards.py --host 10.0.10.11                    # show the plan
  ./f5_standards.py --host 10.0.10.11 --commit           # add what is missing
  ./f5_standards.py --csv devices.csv --clean            # plan an exact match
  ./f5_standards.py --csv devices.csv --clean --commit   # enforce an exact match
  ./f5_standards.py --host 10.0.10.11 --only syslog      # one standard at a time

`--clean` makes each list *exactly* what the file says: anything else on the unit
is removed. Without it, extra entries are reported and left alone. 127.0.0.0/8 is
part of the SNMP standard whether or not the file lists it — the unit polls its
own SNMP over localhost — so it is added when missing and never removed by
`--clean`; `--no-localhost` is the deliberate way to drop it.

Endpoints used (all iControl REST over HTTPS on the management interface):
  GET   /mgmt/tm/sys/snmp              read/compare allowed-addresses
  PATCH /mgmt/tm/sys/snmp              write the list back (whole-list replace)
  GET   /mgmt/tm/sys/snmp/communities  check for per-community source limits
  GET   /mgmt/tm/sys/syslog            read/compare remote-servers
  PATCH /mgmt/tm/sys/syslog            write the list back (whole-list replace)
  POST  /mgmt/tm/sys/config            save sys config, so changes persist

Exit codes: 0 compliant or committed, 1 a unit failed, 2 drift found but not
committed — so a cron compliance check can tell "all good" from "needs work".
"""

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

try:
    import requests
    import urllib3
except ImportError:
    sys.exit("This tool needs the 'requests' package: pip install -r requirements.txt")

from f5common import (Destination, Device, F5Client, covers, error_text, load_devices,
                      load_settings, load_standards, log, normalize_network,
                      parse_address_spec)

SNMP = "/mgmt/tm/sys/snmp"
COMMUNITIES = "/mgmt/tm/sys/snmp/communities"
SYSLOG = "/mgmt/tm/sys/syslog"

# BIG-IP ships with exactly this entry, and the unit polls its own SNMP over
# localhost, so it belongs to the standard: it is added when missing and never
# removed by --clean. --no-localhost is the deliberate way out.
LOCALHOST = "127.0.0.0/8"


@dataclass
class Plan:
    """One standard on one unit, measured against the file.

    Both standards reconcile the same way — a keyed list where each item is
    already right, missing, or not ours — so they share this shape, the renderer
    and the commit path. A standard that is not a list will need its own plan.
    """

    label: str
    endpoint: str
    payload: dict                                # what to PATCH when committing
    current: list = field(default_factory=list)  # what the unit has now
    keep: list = field(default_factory=list)     # entries the file asks for
    add: list = field(default_factory=list)      # file entries the unit is missing
    extra: list = field(default_factory=list)    # entries the file does not name
    notes: list = field(default_factory=list)

    def drift(self, clean):
        return bool(self.add) or bool(clean and self.extra)


# --------------------------------------------------------------------------- #
# Standard: SNMP access (sys snmp allowed-addresses)
# --------------------------------------------------------------------------- #

def plan_snmp(client, standards, clean):
    """Compare a unit's SNMP allow list against snmp.allow.

    Compliance is an exact match by meaning, not by text: a specified
    10.1.1.0/24 matches an existing 10.1.1.0/255.255.255.0. An entry that merely
    *covers* one (10.0.0.0/8 for 10.1.1.0/24) is not a match — the standard names
    the network, so the entry is added — but that is noted, not silent.
    """
    wanted = standards.snmp_allow
    if not wanted:
        return None
    current = list(client.get_json(SNMP).get("allowedAddresses") or [])
    plan = Plan(label="SNMP allow list", endpoint=SNMP, payload={}, current=current)

    matched = {}
    for entry in current:
        net = parse_address_spec(entry)
        hit = None
        if net is not None:
            # `not in matched` sends a second spelling of the same network
            # (10.1.1.0/24 alongside 10.1.1.0/255.255.255.0) to extra.
            hit = next((i for i, (_, want) in enumerate(wanted)
                        if want == net and i not in matched), None)
        if hit is None:
            plan.extra.append(entry)
        else:
            matched[hit] = entry
    plan.keep = [matched[i] for i in sorted(matched)]

    for i, (text, want) in enumerate(wanted):
        if i in matched:
            continue
        plan.add.append(text)
        covering = [entry for entry in plan.extra
                    if parse_address_spec(entry) is not None
                    and covers(parse_address_spec(entry), want)]
        if covering:
            plan.notes.append(f"{text} is already reachable via the broader entry "
                              f"{covering[0]}, but the standard names the network, "
                              f"so it is added explicitly")
    if clean:
        for entry in plan.extra:
            net = parse_address_spec(entry)
            if net is None or not net.is_loopback:
                continue
            if any(covers(want, net) for _, want in wanted):
                continue      # a specified network still covers localhost
            plan.notes.append(f"removing {entry} closes SNMP over localhost, which the "
                              f"unit's own internal monitoring uses — {LOCALHOST} is in "
                              f"the standard by default, and --no-localhost dropped it")

    # Compliant entries keep the spelling the unit already uses, so enforcing a
    # standard never rewrites an entry that already says the right thing.
    plan.payload = {"allowedAddresses": (plan.keep if clean else current) + plan.add}
    return plan


def community_check(client, wanted):
    """Being in the allow list is necessary but not always sufficient.

    Each v2c community can carry its own `source` restriction, applied on top of
    the allow list. Returns a note worth printing, or None. Read-only and
    best-effort: a failure here never fails the run.
    """
    try:
        items = client.get_json(COMMUNITIES).get("items", [])
    except (requests.RequestException, RuntimeError):
        return None
    if not items:
        return ("no SNMP v2c community is configured on this unit, so only SNMPv3 "
                "users can poll it (System >> SNMP : Agent : Configuration)")
    restricted = []
    for item in items:
        source = (item.get("source") or "").strip()
        if not source or source.lower() in ("all", "default"):
            return None       # one unrestricted community accepts any source
        entry = parse_address_spec(source)
        if entry is not None and all(covers(entry, net) for _, net in wanted):
            return None
        restricted.append(f"{item.get('name', '?')} (source {source})")
    return ("every SNMP community here is source-restricted and none covers these "
            "networks, so polling will still be refused — widen or clear the "
            "community source: " + ", ".join(restricted))


# --------------------------------------------------------------------------- #
# Standard: syslog destinations (sys syslog remote-servers)
# --------------------------------------------------------------------------- #

def _server_key(server):
    """Match remote-servers on where they send, not on what they are called."""
    try:
        port = int(server.get("remotePort") or 514)
    except (TypeError, ValueError):
        port = 514
    return Destination(host=str(server.get("host") or "").strip(), port=port).key


def _server_label(server):
    host = server.get("host") or "?"
    port = server.get("remotePort") or 514
    # The unit's own object name is worth showing: it is what an operator sees in
    # the GUI list, and with --clean it is what disappears.
    name = str(server.get("name") or "").split("/")[-1]
    return f"{host}:{port}" + (f" ({name})" if name else "")


def _server_name(dest):
    """A stable, readable object name for a destination we add. BIG-IP requires
    one; matching never uses it, so it only has to be unique and legible."""
    safe = "".join(ch if (ch.isalnum() or ch in ".-_") else "-" for ch in dest.host)
    return f"standards-{safe}-{dest.port}"


def plan_syslog(client, standards, clean):
    """Compare a unit's syslog remote-servers against syslog.destinations."""
    wanted = standards.syslog
    if not wanted:
        return None
    servers = list(client.get_json(SYSLOG).get("remoteServers") or [])
    plan = Plan(label="syslog destinations", endpoint=SYSLOG, payload={},
                current=[_server_label(s) for s in servers])

    matched, keep_objects, extra_objects = {}, [], []
    for server in servers:
        key = _server_key(server)
        hit = next((i for i, dest in enumerate(wanted)
                    if dest.key == key and i not in matched), None)
        if hit is None:
            extra_objects.append(server)
            plan.extra.append(_server_label(server))
        else:
            matched[hit] = server
            keep_objects.append(server)
            plan.keep.append(_server_label(server))

    added_objects = []
    for i, dest in enumerate(wanted):
        if i in matched:
            continue
        plan.add.append(dest.label)
        added_objects.append({"name": _server_name(dest), "host": dest.host,
                              "remotePort": dest.port})
    if extra_objects and not clean:
        plan.notes.append("BIG-IP sends every log message to every remote server, so "
                          "the extra destinations above keep receiving a copy")

    # Kept servers go back exactly as the unit returned them, so a matching entry
    # never loses its name or a localIp someone set deliberately.
    plan.payload = {"remoteServers": (keep_objects if clean
                                      else keep_objects + extra_objects) + added_objects}
    return plan


SECTIONS = {"snmp": plan_snmp, "syslog": plan_syslog}


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def render_plan(name, plan, clean):
    log(name, f"--- {plan.label} ---")
    log(name, f"  on unit:    {', '.join(plan.current) if plan.current else '(none)'}")
    log(name, f"  compliant:  {', '.join(plan.keep) if plan.keep else 'none'}")
    if plan.add:
        log(name, f"  to add:     {', '.join(plan.add)}")
    if plan.extra and clean:
        log(name, f"  to remove:  {', '.join(plan.extra)}")
    elif plan.extra:
        log(name, f"  extra:      {', '.join(plan.extra)} "
                  f"(left in place — --clean removes these)")
    for note in plan.notes:
        log(name, f"  note: {note}")


def run_section(client, section, standards, clean, commit):
    """Plan one standard, and apply it when committing. Returns a result row.

    The planner doubles as the verifier: after a write it runs again against the
    unit, so a silent rejection or a rewrite by BIG-IP cannot pass for success.
    """
    name = client.device.name
    plan = SECTIONS[section](client, standards, clean)
    if plan is None:
        log(name, f"--- {section} --- nothing defined in {standards.path}, skipping")
        return {"section": section, "status": "skipped", "add": [], "remove": []}
    render_plan(name, plan, clean)
    removing = plan.extra if clean else []

    if section == "snmp":
        note = community_check(client, standards.snmp_allow)
        if note:
            log(name, f"  note: {note}")

    if not plan.drift(clean):
        log(name, "  already compliant — nothing to do")
        return {"section": section, "status": "compliant", "add": [], "remove": []}
    if not commit:
        log(name, "  not committed (no --commit) — nothing was written")
        return {"section": section, "status": "drift", "add": plan.add, "remove": removing}

    client.patch_json(plan.endpoint, plan.payload)
    after = SECTIONS[section](client, standards, clean)
    if after.drift(clean):
        problems = []
        if after.add:
            problems.append(f"still missing {', '.join(after.add)}")
        if clean and after.extra:
            problems.append(f"still present {', '.join(after.extra)}")
        raise RuntimeError(f"{plan.label}: {'; '.join(problems)} after the write — unit "
                           f"reports: {', '.join(after.current) or '(none)'}")
    log(name, f"  now: {', '.join(after.current)}")
    return {"section": section, "status": "applied", "add": plan.add, "remove": removing}


SEVERITY = {"failed": 3, "drift": 2, "applied": 1, "compliant": 0, "skipped": 0}


def configure_device(device, settings, standards, sections, clean, commit, save):
    """Run the selected standards against one unit.

    A unit that cannot be reached fails on its own line rather than stopping the
    fleet, and one standard failing does not stop the others on that unit.
    """
    client = F5Client(device, settings)
    results = []
    try:
        with client:          # logs in on entry, always drops the token on exit
            for section in sections:
                try:
                    results.append(run_section(client, section, standards, clean, commit))
                except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
                    reason = error_text(exc, client.base)
                    log(device.name, f"FAILED ({section}): {reason}")
                    results.append({"section": section, "status": "failed", "error": reason})
            if save and any(r["status"] == "applied" for r in results):
                # One save covers every section: REST writes only reach the
                # running config, so without this they are lost on reboot.
                client.save_config()
                log(device.name, "saved sys config — changes survive a reboot")
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        reason = error_text(exc, client.base)
        log(device.name, f"FAILED: {reason}")
        return {"device": device, "status": "failed", "error": reason, "sections": results}
    status = max(results, key=lambda r: SEVERITY[r["status"]])["status"] if results else "skipped"
    return {"device": device, "status": status, "sections": results}


def main():
    argp = argparse.ArgumentParser(
        description="Apply our BIG-IP standards from scripts/standards.yaml. Reports what "
                    "it would change and writes nothing without --commit.")
    target = argp.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", action="append", metavar="IP",
                        help="unit to check (management IP or DNS name); repeatable")
    target.add_argument("--csv", help="device inventory CSV (host,name) instead of --host")
    argp.add_argument("--standards", metavar="FILE",
                      help="standards file (default: ../standards.yaml)")
    argp.add_argument("--only", nargs="+", choices=sorted(SECTIONS), metavar="STANDARD",
                      help=f"run only these standards ({', '.join(sorted(SECTIONS))})")
    argp.add_argument("--clean", action="store_true",
                      help="make each list exactly what the file says — remove every other "
                           "entry. Without --commit it only reports the removals.")
    mode = argp.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true",
                      help="actually write the changes (default is report-only)")
    mode.add_argument("--dry-run", action="store_true",
                      help="report only, writing nothing — the default, statable explicitly")
    argp.add_argument("--env-file", help="credentials file (default: .env next to this script)")
    argp.add_argument("--workers", type=int, help="units to work on at once (default from .env)")
    argp.add_argument("--no-localhost", action="store_true",
                      help=f"leave {LOCALHOST} out of the SNMP standard, so --clean removes it")
    argp.add_argument("--no-save", action="store_true",
                      help="with --commit, skip 'save sys config': live now, lost on reboot")
    args = argp.parse_args()

    standards = load_standards(args.standards)
    sections = args.only or sorted(SECTIONS)
    if not standards.snmp_allow and not standards.syslog:
        sys.exit(f"{standards.path} defines no standards — nothing to apply "
                 f"(expected snmp.allow and/or syslog.destinations)")
    if not args.no_localhost and "snmp" in sections and standards.snmp_allow:
        # Localhost leads the list, the way BIG-IP's own default writes it.
        localhost = normalize_network(LOCALHOST)
        if localhost[1] not in [net for _, net in standards.snmp_allow]:
            standards.snmp_allow.insert(0, localhost)

    settings = load_settings(args.env_file)
    devices = ([Device(host=host, name=host) for host in args.host] if args.host
               else load_devices(args.csv))
    if not settings.verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    workers = max(args.workers or settings.workers, 1)
    print(f"standards: {standards.path}")
    if "snmp" in sections:
        print(f"  snmp    allow {', '.join(text for text, _ in standards.snmp_allow) or '(none)'}")
    if "syslog" in sections:
        print(f"  syslog  send to {', '.join(d.label for d in standards.syslog) or '(none)'}")
    print(f"devices:   {len(devices)} ({', '.join(d.host for d in devices)}), "
          f"as {settings.username}, {min(workers, len(devices))} at a time")
    print("mode:      " + ("COMMIT — changes will be written" if args.commit
                           else "report only, nothing will be written")
          + (", exact match (--clean)" if args.clean else "") + "\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda dev: configure_device(dev, settings, standards, sections, args.clean,
                                         args.commit, not args.no_save),
            devices,
        ))

    print("\nsummary:")
    for res in results:
        dev = res["device"]
        print(f"  {dev.name} ({dev.host})")
        if res.get("error"):
            print(f"    FAILED      {res['error']}")
        for sec in res.get("sections", []):
            if sec["status"] == "failed":
                print(f"    {sec['section']:<7} FAILED      {sec['error']}")
                continue
            change = " ".join([f"+{item}" for item in sec["add"]]
                              + [f"-{item}" for item in sec["remove"]])
            print(f"    {sec['section']:<7} {sec['status']:<11}{change}".rstrip())

    failed = [r for r in results if r["status"] == "failed"]
    drifted = [r for r in results if r["status"] == "drift"]
    if any(r["status"] == "applied" for r in results):
        print("\nSNMP and syslog settings are per-unit: BIG-IP does not ConfigSync them, "
              "so both members of an HA pair need this run.")
    if drifted:
        print(f"\n{len(drifted)} of {len(results)} device(s) need changes — re-run with "
              f"--commit to apply them.")
    if failed:
        print(f"\n{len(failed)} of {len(results)} device(s) failed")
        return 1
    return 2 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
