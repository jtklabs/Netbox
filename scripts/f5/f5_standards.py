#!/usr/bin/env python3
"""Apply our F5 BIG-IP standards to one or more units.

Today it covers one standard — **SNMP access**: which networks may poll the unit
(`sys snmp allowed-addresses`, the GUI's Client Allow List under
System >> SNMP : Agent : Configuration). Logging, banner and the rest of the
standards land here as further sections.

Nothing is written without `--commit`. A plain run connects, compares each unit
against the networks you passed, and prints what it would add, what it would
remove, and what is already compliant.

  ./f5_standards.py 10.1.1.0/24 --host 10.0.10.11                     # show the plan
  ./f5_standards.py 10.1.1.0/24 --host 10.0.10.11 --commit            # add what is missing
  ./f5_standards.py 10.1.1.0/24 --csv devices.csv --clean             # plan an exact match
  ./f5_standards.py 10.1.1.0/24 --csv devices.csv --clean --commit    # enforce an exact match

`--clean` makes the allow list *exactly* the networks given: anything else on the
unit is removed. Without it, extra entries are reported and left alone.

Endpoints used (all iControl REST over HTTPS on the management interface):
  GET   /mgmt/tm/sys/snmp              read the current allowed-addresses list
  PATCH /mgmt/tm/sys/snmp              write the list back (whole-list replace)
  GET   /mgmt/tm/sys/snmp/communities  check for per-community source limits
  POST  /mgmt/tm/sys/config            save sys config, so the change persists

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

from f5common import (Device, F5Client, covers, error_text, load_devices, load_settings,
                      log, normalize_network, parse_address_spec)

# --------------------------------------------------------------------------- #
# Standard: SNMP access (sys snmp allowed-addresses)
# --------------------------------------------------------------------------- #

SNMP = "/mgmt/tm/sys/snmp"
COMMUNITIES = "/mgmt/tm/sys/snmp/communities"


@dataclass
class Plan:
    """One unit's allow list measured against the networks we specified."""

    current: list                       # the list as the unit has it now
    keep: list = field(default_factory=list)     # entries that match a specified network
    add: list = field(default_factory=list)      # specified networks the unit is missing
    extra: list = field(default_factory=list)    # entries we did not specify
    notes: list = field(default_factory=list)    # things to see before committing

    def desired(self, clean):
        """The list to write. Compliant entries keep the spelling the unit
        already uses, so enforcing a standard never rewrites a matching entry."""
        return (self.keep if clean else self.current) + self.add

    def drift(self, clean):
        return bool(self.add) or bool(clean and self.extra)


def build_plan(current, requested):
    """Compare a unit's allow list against the specified networks.

    Compliance is an exact match by meaning, not by text: a specified
    10.1.1.0/24 matches an existing 10.1.1.0/255.255.255.0. An entry that merely
    *covers* a specified network (10.0.0.0/8 for 10.1.1.0/24) is not a match —
    the standard names the network, so the entry is added — but that is noted
    rather than done silently.
    """
    plan = Plan(current=list(current))
    matched = {}
    for entry in current:
        net = parse_address_spec(entry)
        hit = None
        if net is not None:
            # `not in matched` sends a second spelling of the same network
            # (10.1.1.0/24 alongside 10.1.1.0/255.255.255.0) to extra.
            hit = next((i for i, (_, want) in enumerate(requested)
                        if want == net and i not in matched), None)
        if hit is None:
            plan.extra.append(entry)
        else:
            matched[hit] = entry
    plan.keep = [matched[i] for i in sorted(matched)]

    for i, (text, want) in enumerate(requested):
        if i in matched:
            continue
        plan.add.append(text)
        covering = [entry for entry in plan.extra
                    if _covers_entry(entry, want)]
        if covering:
            plan.notes.append(f"{text} is already reachable via the broader entry "
                              f"{covering[0]}, but the standard names the network, "
                              f"so it is added explicitly")
    return plan


def _covers_entry(entry, wanted):
    net = parse_address_spec(entry)
    return net is not None and covers(net, wanted)


def render_plan(name, plan, clean):
    log(name, f"allow list now: {', '.join(plan.current) if plan.current else '(empty)'}")
    log(name, f"compliant:      {', '.join(plan.keep) if plan.keep else 'none'}")
    if plan.add:
        log(name, f"to add:         {', '.join(plan.add)}")
    if plan.extra and clean:
        log(name, f"to remove:      {', '.join(plan.extra)}")
        for entry in plan.extra:
            net = parse_address_spec(entry)
            if net is not None and net.is_loopback:
                log(name, f"note: removing {entry} closes SNMP over localhost, which the "
                          f"unit's own internal monitoring uses — pass {entry} as an "
                          f"argument to keep it")
    elif plan.extra:
        log(name, f"extra:          {', '.join(plan.extra)} "
                  f"(left in place — --clean removes these)")
    for note in plan.notes:
        log(name, f"note: {note}")
    if clean and not plan.keep and not plan.add:
        # Cannot happen while at least one network is required, but an empty
        # allow list closes SNMP entirely, so never write one by accident.
        raise RuntimeError("refusing to write an empty allow list")


def community_check(client, nets):
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
        if entry is not None and all(covers(entry, net) for net in nets):
            return None
        restricted.append(f"{item.get('name', '?')} (source {source})")
    return ("every SNMP community here is source-restricted and none covers these "
            "networks, so polling will still be refused — widen or clear the "
            "community source: " + ", ".join(restricted))


def snmp_access(client, requested, clean, commit, save):
    """Bring one logged-in unit's SNMP allow list in line with `requested`.

    Raises on anything the unit refuses; the caller turns that into a result row.
    """
    name = client.device.name
    plan = build_plan(client.get_json(SNMP).get("allowedAddresses") or [], requested)
    render_plan(name, plan, clean)
    removing = plan.extra if clean else []

    note = community_check(client, [net for _, net in requested])
    if note:
        log(name, f"note: {note}")

    if not plan.drift(clean):
        log(name, "already compliant — nothing to do")
        return {"status": "compliant", "add": [], "remove": []}
    if not commit:
        log(name, "not committed (no --commit) — nothing was written")
        return {"status": "drift", "add": plan.add, "remove": removing}

    log(name, f"writing: {', '.join(plan.desired(clean))}")
    client.patch_json(SNMP, {"allowedAddresses": plan.desired(clean)})

    # Re-read rather than trusting the PATCH response: BIG-IP rewrites some
    # entries (10.1.1.5/32 -> 10.1.1.5), so confirm by meaning, not by string.
    after = build_plan(client.get_json(SNMP).get("allowedAddresses") or [], requested)
    problems = []
    if after.add:
        problems.append(f"still missing {', '.join(after.add)}")
    if clean and after.extra:
        problems.append(f"still present {', '.join(after.extra)}")
    if problems:
        raise RuntimeError(f"{'; '.join(problems)} after the write — unit reports: "
                           f"{', '.join(after.current) or '(empty)'}")
    log(name, f"allow list is now: {', '.join(after.current)}")

    if save:
        client.save_config()
        log(name, "saved sys config — change survives a reboot")
    else:
        log(name, "NOT saved (--no-save): live now, but lost on reboot until someone "
                  "runs 'save sys config'")
    return {"status": "applied", "add": plan.add, "remove": removing}


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def configure_device(device, settings, requested, clean, commit, save):
    """Connect to one unit and run the standards, turning any failure into a
    result row instead of a traceback — one dead unit must not stop a fleet."""
    client = F5Client(device, settings)
    try:
        with client:          # logs in on entry, always drops the token on exit
            result = snmp_access(client, requested, clean, commit, save)
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        reason = error_text(exc, client.base)
        log(device.name, f"FAILED: {reason}")
        return {"device": device, "status": "failed", "error": reason}
    return {"device": device, **result}


def main():
    argp = argparse.ArgumentParser(
        description="Apply our BIG-IP standards (today: the SNMP client allow list). "
                    "Reports what it would change and writes nothing without --commit.")
    argp.add_argument("networks", nargs="+", metavar="NETWORK",
                      help="network or address that may poll SNMP, e.g. 10.1.1.0/24")
    target = argp.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", action="append", metavar="IP",
                        help="unit to check (management IP or DNS name); repeatable")
    target.add_argument("--csv", help="device inventory CSV (host,name) instead of --host")
    argp.add_argument("--clean", action="store_true",
                      help="make the allow list exactly the networks given — remove every "
                           "other entry. Without --commit it only reports the removals.")
    mode = argp.add_mutually_exclusive_group()
    mode.add_argument("--commit", action="store_true",
                      help="actually write the change (default is report-only)")
    mode.add_argument("--dry-run", action="store_true",
                      help="report only, writing nothing — the default, statable explicitly")
    argp.add_argument("--env-file", help="credentials file (default: .env next to this script)")
    argp.add_argument("--workers", type=int, help="units to work on at once (default from .env)")
    argp.add_argument("--no-save", action="store_true",
                      help="with --commit, skip 'save sys config': live now, lost on reboot")
    args = argp.parse_args()

    requested = []
    for text in args.networks:
        try:
            entry = normalize_network(text)
        except ValueError as exc:
            sys.exit(f"bad network argument: {exc}")
        if entry[1] not in [net for _, net in requested]:
            requested.append(entry)

    settings = load_settings(args.env_file)
    devices = ([Device(host=host, name=host) for host in args.host] if args.host
               else load_devices(args.csv))
    if not settings.verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    workers = max(args.workers or settings.workers, 1)
    print(f"standard: SNMP access — allow {', '.join(text for text, _ in requested)}"
          + (" and nothing else (--clean)" if args.clean else ""))
    print(f"devices:  {len(devices)} ({', '.join(d.host for d in devices)}), "
          f"as {settings.username}, {min(workers, len(devices))} at a time")
    print("mode:     " + ("COMMIT — changes will be written"
                          if args.commit else "report only, nothing will be written") + "\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda dev: configure_device(dev, settings, requested, args.clean,
                                         args.commit, not args.no_save),
            devices,
        ))

    print("\nsummary:")
    failed = [r for r in results if r["status"] == "failed"]
    drifted = [r for r in results if r["status"] == "drift"]
    for res in results:
        dev = res["device"]
        if res["status"] == "failed":
            print(f"  FAILED      {dev.name} ({dev.host}) — {res['error']}")
            continue
        change = " ".join([f"+{net}" for net in res["add"]]
                          + [f"-{net}" for net in res["remove"]])
        detail = f" — {change}" if change else ""
        print(f"  {res['status']:<11} {dev.name} ({dev.host}){detail}")
    if any(r["status"] == "applied" for r in results):
        print("\nSNMP settings are per-unit: BIG-IP does not ConfigSync them, so both "
              "members of an HA pair need this run.")
    if drifted:
        print(f"\n{len(drifted)} of {len(results)} device(s) need changes — re-run with "
              f"--commit to apply them.")
    if failed:
        print(f"\n{len(failed)} of {len(results)} device(s) failed")
        return 1
    return 2 if drifted else 0


if __name__ == "__main__":
    sys.exit(main())
