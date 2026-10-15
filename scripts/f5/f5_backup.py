#!/usr/bin/env python3
"""Back up BIG-IP configuration to this server — a UCS archive and an SCF.

Two artifacts per unit, because they are good at different things:

  UCS  the whole unit: configuration, SSL private keys, the user database and
       the license. What replacement hardware gets restored from.
  SCF  the configuration as one flat text file. Diffable between runs and
       between units, and what config is rebuilt from on a different box.

All of it over iControl REST on the management interface, with no SSH to the
boxes: the unit is told to write each file, the file is downloaded here in 1 MB
chunks, the local copy is checked against the unit's own md5sum, and the unit's
copy is removed again so backups do not pile up in /var.

Every file is named <unit>-<YYYYmmdd>-<HHMMSS>.<ext>, so runs never overwrite
each other and the directory listing is the retention history. `--keep N` prunes
the older sets, and only for units that a run backed up cleanly.

  ./f5_backup.py --host 10.0.10.11                    # UCS + SCF into backups/
  ./f5_backup.py --csv devices.csv --outdir /srv/f5-backups
  ./f5_backup.py --csv devices.csv --keep 30          # keep the 30 newest sets
  ./f5_backup.py --host 10.0.10.11 --only scf         # config text only
  ./f5_backup.py --csv devices.csv --leave-on-unit    # keep the unit's copy too

Both files hold private keys or password hashes: the output directory is created
0700 and every file 0600. Set F5_UCS_PASSPHRASE in .env to have the units
encrypt the archives themselves.

Endpoints used (all iControl REST over HTTPS on the management interface):
  POST /mgmt/tm/sys/ucs                              save a UCS on the unit
  POST /mgmt/tm/sys/config                           save an SCF on the unit
  GET  /mgmt/shared/file-transfer/ucs-downloads/...  download the UCS
  GET  /mgmt/shared/file-transfer/madm/...           download the SCF, once staged
  POST /mgmt/tm/util/bash                            size, md5sum, list, stage, clean up

Exit codes: 0 every file downloaded and verified, 1 at least one unit or
artifact failed — so a cron backup can tell a clean run from one to look at.
"""

import argparse
import os
import re
import stat
import sys
import time
from concurrent.futures import ThreadPoolExecutor

try:
    import requests
    import urllib3
except ImportError:
    sys.exit("This tool needs the 'requests' package: pip install -r requirements.txt")

from f5common import (Device, F5Client, error_text, load_devices, load_settings, log,
                      md5_of)

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUTDIR = os.path.join(HERE, "backups")

UCS_DIR = "/var/local/ucs"
SCF_DIR = "/var/local/scf"
UCS_DOWNLOADS = "/mgmt/shared/file-transfer/ucs-downloads"
# Writing either file walks the entire configuration; on a large one that is
# minutes, not seconds.
SAVE_TIMEOUT = 600

# Where a file has to sit for a download worker to serve it. Only the UCS has a
# worker pointed at its own directory, so anything else is staged into one of
# these: madm first because it exists for exactly this, /var/local/ucs second
# because every unit that can be backed up at all serves that one.
STAGING = (
    ("/var/config/rest/madm", "/mgmt/shared/file-transfer/madm"),
    (UCS_DIR, UCS_DOWNLOADS),
)


def human(size):
    """A byte count in the largest unit that keeps it readable."""
    if size is None:
        return "unknown size"
    value = float(size)
    for unit in ("B", "KB", "MB"):
        if value < 1024:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"


def safe_name(name):
    """A unit's name reduced to what is safe in a file name on both ends.

    Everything the tool sends to the unit's shell is built from this, which is
    what makes those commands safe to quote rather than escape.
    """
    return re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "device"


# --------------------------------------------------------------------------- #
# Asking the unit about its own files
# --------------------------------------------------------------------------- #

def remote_size(client, path):
    """Size of a file on the unit in bytes, or None if the unit won't say.

    Only an optimisation: without it the download asks the file-transfer worker
    for the total instead.
    """
    try:
        return int(client.run_bash(f"stat -c %s {path}").split()[0])
    except (requests.RequestException, RuntimeError, ValueError, IndexError):
        return None


def remote_md5(client, path):
    """md5sum of a file on the unit, or None if the unit won't run it."""
    try:
        out = client.run_bash(f"md5sum {path}").split()
    except (requests.RequestException, RuntimeError, ValueError):
        return None
    return out[0] if out and len(out[0]) == 32 else None


def remote_listing(client, directory, prefix):
    """The names in `directory` starting with `prefix`, as the unit reports them."""
    out = client.run_bash(f"ls -1 {directory}/{prefix}* 2>/dev/null")
    return [os.path.basename(line.strip()) for line in out.splitlines() if line.strip()]


def remove_from_unit(client, directory, filename):
    """Best-effort removal of a file this run made on the unit."""
    if directory == UCS_DIR and filename.endswith(".ucs"):
        try:
            resp = client.session.delete(f"{client.base}/mgmt/tm/sys/ucs/{filename}",
                                         verify=client.verify, timeout=client.timeout)
            if resp.ok:
                return True
        except requests.RequestException:
            pass
    try:
        client.run_bash(f"rm -f {directory}/{filename}")
        return True
    except (requests.RequestException, RuntimeError, ValueError):
        return False


# --------------------------------------------------------------------------- #
# Getting a file off the unit
# --------------------------------------------------------------------------- #

def download_from_unit(client, directory, filename, dest, size):
    """Pull one file off the unit, trying each download worker in turn.

    Only the UCS has a worker pointed at its own directory. Anything else — the
    SCF, and any companion archive the unit writes beside it — is *copied* into a
    directory a worker does serve and the copy deleted afterwards, so a failed
    transfer can never lose the file it was backing up.
    """
    if directory == UCS_DIR:
        workers = [(directory, f"{UCS_DOWNLOADS}/{filename}")]
    else:
        workers = [(staging, f"{endpoint}/{filename}") for staging, endpoint in STAGING]

    problems = []
    for staging, endpoint in workers:
        staged = staging != directory
        try:
            if staged:
                client.run_bash(f"mkdir -p {staging} && cp -f {directory}/{filename} "
                                f"{staging}/{filename}")
            return client.download(endpoint, dest, total=size)
        except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
            problems.append(f"{endpoint}: {error_text(exc)}")
        finally:
            if staged:
                remove_from_unit(client, staging, filename)
    raise RuntimeError(f"nothing would serve {directory}/{filename} — " + "; ".join(problems))


def quarantine(dest, suffix):
    """Move a bad download out of the way of the good ones, and say where it went.

    Half a file, or a whole one that does not match, is worse under the ordinary
    <unit>-<date>.<ext> name than no file at all: a listing reads it as a backup.
    Renaming keeps it for inspection while making it unmistakable, and a later
    --keep run ages it out along with the rest of that set.
    """
    if not os.path.exists(dest):
        return ""
    kept = f"{dest}.{suffix}"
    try:
        os.replace(dest, kept)
    except OSError:
        return f" (the {suffix} copy is still at {dest})"
    return f" (the {suffix} copy was kept as {os.path.basename(kept)})"


def pull(client, kind, directory, filename, outdir, cleanup):
    """Download one file the unit just wrote, verify it, and tidy up after it."""
    name = client.device.name
    remote = f"{directory}/{filename}"
    dest = os.path.join(outdir, filename)

    size = remote_size(client, remote)
    log(name, f"downloading {filename} ({human(size)})")
    try:
        written = download_from_unit(client, directory, filename, dest, size)
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        raise RuntimeError(f"{filename}: {error_text(exc)}"
                           f"{quarantine(dest, 'partial')}") from exc

    unit_md5 = remote_md5(client, remote)
    if unit_md5 is None:
        verified = "unverified, the unit would not run md5sum"
    elif unit_md5 != md5_of(dest):
        raise RuntimeError(f"{filename} arrived corrupt — it does not match md5sum of "
                           f"{remote} on the unit{quarantine(dest, 'corrupt')}. The unit's "
                           f"own copy was kept too; re-run before trusting either")
    else:
        verified = "md5 verified"

    if not cleanup:
        after = f", left on the unit at {remote}"
    elif remove_from_unit(client, directory, filename):
        after = ", removed from the unit"
    else:
        after = f", still on the unit at {remote} — delete it by hand"
    log(name, f"saved {dest} — {human(written)}, {verified}{after}")
    return {"kind": kind, "path": dest, "bytes": written, "verified": verified}


# --------------------------------------------------------------------------- #
# The two artifacts
# --------------------------------------------------------------------------- #

def backup_ucs(client, base, outdir, settings, cleanup):
    """Save a UCS on the unit and bring it here.

    A UCS is the whole unit — configuration, SSL private keys, the user database,
    the license — which is why it is the one to restore replacement hardware
    from, and why the archives are secret material once they are here.
    """
    filename = f"{base}.ucs"
    payload = {"command": "save", "name": filename}
    if settings.ucs_passphrase:
        payload["options"] = [{"passphrase": settings.ucs_passphrase}]
    log(client.device.name, f"saving UCS as {filename} (minutes, on a large config)")
    client.post_json("/mgmt/tm/sys/ucs", payload, timeout=SAVE_TIMEOUT)
    return [pull(client, "ucs", UCS_DIR, filename, outdir, cleanup)]


def backup_scf(client, base, outdir, settings, cleanup):
    """Save an SCF on the unit and bring it — plus whatever landed beside it — here.

    `save sys config file` writes the configuration as one flat text file under
    /var/local/scf, and writes a companion archive of the files the config points
    at (certificates and keys, external data groups, monitor scripts) next to it.
    Which of those a given version produces is read back from the unit rather
    than assumed, and everything carrying this run's name is pulled.

    The running configuration is not touched: the `file` option only writes the
    new file.
    """
    filename = f"{base}.scf"
    log(client.device.name, f"saving SCF as {filename}")
    client.post_json("/mgmt/tm/sys/config",
                     {"command": "save", "options": [{"file": filename}]},
                     timeout=SAVE_TIMEOUT)
    try:
        produced = remote_listing(client, SCF_DIR, base)
    except (requests.RequestException, RuntimeError, ValueError) as exc:
        raise RuntimeError(
            f"saved the SCF but could not list {SCF_DIR}: {error_text(exc)}. Unlike a UCS, "
            f"an SCF has no download endpoint of its own, so retrieving one needs the "
            f"util/bash endpoint — an Administrator account with an advanced shell") from exc
    if not produced:
        raise RuntimeError(f"the unit reported no error but wrote nothing matching "
                           f"{SCF_DIR}/{base}*")
    return [pull(client, "scf", SCF_DIR, item, outdir, cleanup) for item in produced]


# UCS first: it is the artifact a rebuild actually needs, so on a run that gets
# cut short it is the one that should already be here.
BACKUPS = {"ucs": backup_ucs, "scf": backup_scf}
KINDS = ("ucs", "scf")


# --------------------------------------------------------------------------- #
# Retention
# --------------------------------------------------------------------------- #

STAMP = r"\d{8}-\d{6}"


def prune(outdir, unit, keep, current):
    """Keep the newest `keep` dated sets for one unit here and delete the rest.

    Only files this tool named are eligible — <unit>-<date>-<time>.<ext>, for the
    unit just backed up — so nothing else in the directory is ever at risk. The
    caller runs this only after a clean backup, so a run that failed can never
    delete the history it failed to add to.
    """
    pattern = re.compile(rf"^{re.escape(unit)}-({STAMP})[.-]")
    sets = {}
    for entry in os.listdir(outdir):
        match = pattern.match(entry)
        if match:
            sets.setdefault(match.group(1), []).append(entry)

    removed = []
    for stamp in sorted(sets, reverse=True)[keep:]:
        if stamp == current:
            continue                      # never the set this run just made
        for entry in sets[stamp]:
            try:
                os.remove(os.path.join(outdir, entry))
                removed.append(entry)
            except OSError as exc:
                print(f"warning: could not delete {entry}: {exc}", file=sys.stderr)
    return removed


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #

def backup_device(device, settings, kinds, outdir, stamp, cleanup, keep):
    """Back up one unit. One artifact failing does not stop the other, and one
    unreachable unit never stops the fleet."""
    unit = safe_name(device.name)
    base = f"{unit}-{stamp}"
    client = F5Client(device, settings)
    files, errors = [], {}
    try:
        with client:              # logs in on entry, always drops the token on exit
            for kind in kinds:
                try:
                    files.extend(BACKUPS[kind](client, base, outdir, settings, cleanup))
                except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
                    reason = error_text(exc, client.base)
                    log(device.name, f"FAILED ({kind}): {reason}")
                    errors[kind] = reason
    except (requests.RequestException, RuntimeError, OSError, ValueError) as exc:
        reason = error_text(exc, client.base)
        log(device.name, f"FAILED: {reason}")
        errors.setdefault("unit", reason)

    pruned = []
    if keep and files and not errors:
        pruned = prune(outdir, unit, keep, stamp)
        if pruned:
            log(device.name, f"pruned {len(pruned)} file(s) from older sets")
    return {"device": device, "files": files, "errors": errors, "pruned": pruned}


def make_outdir(path):
    """Create the backup directory 0700, and say so if an existing one is looser.

    What lands in it is every unit's private keys and password hashes.
    """
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
    except OSError as exc:
        sys.exit(f"cannot create {path}: {exc}")
    if os.stat(path).st_mode & (stat.S_IRGRP | stat.S_IROTH):
        print(f"warning: {path} will hold the units' SSL private keys and is readable by "
              f"other users — chmod 700 {path}", file=sys.stderr)


def main():
    argp = argparse.ArgumentParser(
        description="Back up BIG-IP configuration to this server over iControl REST: a UCS "
                    "archive and an SCF per unit, named with the date they were taken.")
    target = argp.add_mutually_exclusive_group(required=True)
    target.add_argument("--host", action="append", metavar="IP",
                        help="unit to back up (management IP or DNS name); repeatable")
    target.add_argument("--csv", help="device inventory CSV (host,name) instead of --host")
    argp.add_argument("--outdir", default=DEFAULT_OUTDIR, metavar="DIR",
                      help="where the backups land (default: backups/ next to this script)")
    argp.add_argument("--only", nargs="+", choices=KINDS, metavar="KIND",
                      help=f"back up only these ({', '.join(KINDS)}); default is both")
    argp.add_argument("--keep", type=int, metavar="N",
                      help="after a clean backup, keep the N newest dated sets for that unit "
                           "and delete its older ones (default: keep every set)")
    argp.add_argument("--leave-on-unit", action="store_true",
                      help="keep the unit's own copy as well (default is to delete it once "
                           "the download here is verified, so /var does not fill up)")
    argp.add_argument("--env-file", help="credentials file (default: .env next to this script)")
    argp.add_argument("--workers", type=int, help="units to work on at once (default from .env)")
    args = argp.parse_args()

    if args.keep is not None and args.keep < 1:
        sys.exit("--keep must be at least 1 — a backup run that deletes its own result is "
                 "not a backup")

    # Always in KINDS order, however --only spelled it.
    kinds = [kind for kind in KINDS if kind in (args.only or KINDS)]
    settings = load_settings(args.env_file)
    devices = ([Device(host=host, name=host) for host in args.host] if args.host
               else load_devices(args.csv))
    if not settings.verify_ssl:
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    outdir = os.path.abspath(args.outdir)
    make_outdir(outdir)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    workers = max(args.workers or settings.workers, 1)

    print(f"backing up: {', '.join(kinds)}"
          + ("" if args.leave_on_unit else ", removing the unit's copy once verified"))
    print(f"devices:    {len(devices)} ({', '.join(d.host for d in devices)}), "
          f"as {settings.username}, {min(workers, len(devices))} at a time")
    print(f"into:       {outdir}, named <unit>-{stamp}.<ext>")
    print("retention:  " + (f"the {args.keep} newest set(s) per unit — older ones are deleted "
                            f"after a clean run" if args.keep
                            else "every set kept, nothing here is deleted"))
    if "ucs" in kinds and not settings.ucs_passphrase:
        print("note:       UCS archives will be UNENCRYPTED and hold the units' SSL private\n"
              "            keys and user database — set F5_UCS_PASSPHRASE in .env to have\n"
              "            the units encrypt them")
    print()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(
            lambda dev: backup_device(dev, settings, kinds, outdir, stamp,
                                      not args.leave_on_unit, args.keep),
            devices,
        ))

    print("\nsummary:")
    for res in results:
        dev = res["device"]
        print(f"  {dev.name} ({dev.host})")
        if "unit" in res["errors"]:
            print(f"    {'unit':<7} FAILED      {res['errors']['unit']}")
        for kind in kinds:
            for item in (f for f in res["files"] if f["kind"] == kind):
                print(f"    {kind:<7} {human(item['bytes']):>9}  "
                      f"{os.path.basename(item['path'])} ({item['verified']})")
            if kind in res["errors"]:
                print(f"    {kind:<7} FAILED      {res['errors'][kind]}")
        if res["pruned"]:
            print(f"    {'pruned':<7} {len(res['pruned'])} file(s) from older sets")

    failed = [res for res in results if res["errors"]]
    if failed:
        print(f"\n{len(failed)} of {len(results)} unit(s) did not back up cleanly")
        return 1
    print(f"\nall {len(results)} unit(s) backed up into {outdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
