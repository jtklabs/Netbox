#!/usr/bin/env python3
"""Filter an inventory CSV down to switches, using the Cisco Product Information API.

Reads a CSV with a `serial` column, asks Cisco what each serial is, and writes a
cleaned CSV containing every row EXCEPT the ones Cisco positively identifies as
something other than a switch.

Keep/drop rule (deliberately conservative — we only drop on confident evidence):

    switch                  -> keep
    unknown serial          -> keep   (Cisco has no record of it)
    API/auth/network error  -> keep   (we could not ask)
    unclassifiable record   -> keep   (record exists but says nothing useful)
    positively another type -> DROP   (router, AP, phone, firewall, module, ...)

This matters in practice: the Product Information API is documented to have gaps
for some Catalyst 9200/9500 serials that resolve fine on cway.cisco.com, so a
"not found" must never be read as "not a switch".

Every decision, with the raw Cisco fields behind it, is written to a report CSV
so drops can be audited and the classifier tuned to your fleet.

Usage:
    export CISCO_CLIENT_ID=...        # from apiconsole.cisco.com
    export CISCO_CLIENT_SECRET=...
    python3 scripts/clean_inventory.py inventory.csv

    python3 scripts/clean_inventory.py inventory.csv \
        --output cleaned.csv --report decisions.csv --cache .cisco-cache.json

Requires Cisco Support API entitlement (SNTC customer or PSS partner).
Stdlib only — no pip installs.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TOKEN_URL = "https://id.cisco.com/oauth2/default/v1/token"
API_BASE = "https://apix.cisco.com/product/v1/information/serial_numbers/"

# The API accepts at most 5 serial numbers per call.
BATCH_SIZE = 5

# Rate limits are not published by Cisco; be polite and back off on 403.
REQUEST_PAUSE_SECONDS = 0.3
MAX_ATTEMPTS = 4

# Decisions
KEEP_SWITCH = "switch"
KEEP_UNKNOWN = "unknown"
KEEP_ERROR = "lookup-failed"
KEEP_UNCLASSIFIED = "unclassified"
DROP_OTHER = "not-a-switch"

# Base PID prefixes that identify a switch regardless of what the API's
# taxonomy fields say. Deterministic and offline, so this is checked first.
# Careful: C9800 is a wireless controller and C9100 are access points, which is
# why the Catalyst 9000 switch families are listed individually.
SWITCH_PID_PREFIXES = (
    "C9200", "C9300", "C9400", "C9500", "C9600",       # Catalyst 9000 switches
    "C1000", "C1200", "C1300",                          # Catalyst 1000/1200/1300
    "C2960", "C3560", "C3750", "C3850", "C4500", "C6500",
    "WS-C",                                             # classic Catalyst
    "N9K-", "N7K-", "N5K-", "N3K-", "N2K-", "N1K-",     # Nexus
    "ME-",                                              # Metro Ethernet
    "IE-",                                              # Industrial Ethernet
    "CBS",                                              # Cisco Business Switches
    "SG3", "SG2", "SG5", "SF3", "SF2",                  # Small Business
    "MS",                                               # Meraki switches
)

# Words that mark a product as definitively NOT a switch when they appear in the
# product_type / product_series / product_category text.
NON_SWITCH_WORDS = (
    "router", "firewall", "access point", "wireless", "phone", "telepresence",
    "camera", "server", "adapter", "gateway", "controller", "transceiver",
    "antenna", "license", "power supply", "module", "line card", "linecard",
    "chassis fan", "storage", "load balancer",
)

SWITCH_RE = re.compile(r"\bswitch(es)?\b", re.IGNORECASE)


class CiscoAPIError(Exception):
    """Raised when a lookup cannot be completed (auth, network, rate limit)."""


# --------------------------------------------------------------------------- #
# Cisco API client
# --------------------------------------------------------------------------- #
class CiscoClient:
    def __init__(self, client_id, client_secret, verbose=False):
        self.client_id = client_id
        self.client_secret = client_secret
        self.verbose = verbose
        self._token = None
        self._token_expires_at = 0.0

    def _log(self, message):
        if self.verbose:
            print("  " + message, file=sys.stderr)

    def _fetch_token(self):
        body = urllib.parse.urlencode(
            {
                "grant_type": "client_credentials",
                "client_id": self.client_id,
                "client_secret": self.client_secret,
            }
        ).encode()
        request = urllib.request.Request(
            TOKEN_URL,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            raise CiscoAPIError(
                "token request failed (HTTP %s): %s" % (exc.code, detail)
            )
        except Exception as exc:  # noqa: BLE001 - network/DNS/TLS/JSON
            raise CiscoAPIError("token request failed: %s" % exc)

        token = payload.get("access_token")
        if not token:
            raise CiscoAPIError("token response had no access_token: %s" % payload)
        # Renew a minute early rather than racing the expiry.
        self._token = token
        self._token_expires_at = time.time() + float(payload.get("expires_in", 3600)) - 60
        self._log("obtained access token")

    def _get_token(self):
        if self._token is None or time.time() >= self._token_expires_at:
            self._fetch_token()
        return self._token

    def lookup(self, serials):
        """Return {serial_upper: record_or_None} for up to BATCH_SIZE serials.

        A value of None means Cisco returned no usable record (unknown serial).
        Raises CiscoAPIError if the batch could not be looked up at all.
        """
        url = API_BASE + urllib.parse.quote(",".join(serials), safe=",")
        payload = self._request_with_retry(url)
        return self._parse(payload, serials)

    def _request_with_retry(self, url):
        last_error = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "Authorization": "Bearer " + self._get_token(),
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    raw = response.read().decode(errors="replace")
                try:
                    return json.loads(raw)
                except ValueError:
                    # 5xx pages and some gateway errors come back as HTML/XML.
                    raise CiscoAPIError("non-JSON response: %s" % raw[:200])
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode(errors="replace")[:200]
                if exc.code == 401:
                    # Token expired or revoked mid-run; force a refresh once.
                    self._log("401 received, refreshing token")
                    self._token = None
                    last_error = CiscoAPIError("unauthorized: %s" % detail)
                elif exc.code in (403, 429) or exc.code >= 500:
                    # 403 covers Cisco's rate-limit conditions as well as auth.
                    last_error = CiscoAPIError("HTTP %s: %s" % (exc.code, detail))
                else:
                    raise CiscoAPIError("HTTP %s: %s" % (exc.code, detail))
            except CiscoAPIError as exc:
                last_error = exc
            except Exception as exc:  # noqa: BLE001 - network/TLS/timeouts
                last_error = CiscoAPIError(str(exc))

            if attempt < MAX_ATTEMPTS:
                delay = (2 ** (attempt - 1)) + random.uniform(0, 0.5)
                self._log("retry %d/%d in %.1fs (%s)" % (attempt, MAX_ATTEMPTS, delay, last_error))
                time.sleep(delay)

        raise last_error or CiscoAPIError("request failed")

    @staticmethod
    def _parse(payload, requested):
        """Map the response back onto the serials we asked for.

        Unknown serials come back as HTTP 200 with total_records 0 and a
        placeholder record carrying an ErrorResponse, so status codes alone
        cannot tell us anything here.
        """
        results = {serial.upper(): None for serial in requested}
        for record in payload.get("product_list") or []:
            if not isinstance(record, dict):
                continue
            if "ErrorResponse" in record:
                continue
            serial = (record.get("sr_no") or "").strip().upper()
            if not serial:
                continue
            # An empty base_pid means the placeholder/not-found shape.
            if not (record.get("base_pid") or "").strip():
                continue
            if serial in results:
                results[serial] = record
        return results


# --------------------------------------------------------------------------- #
# Classification
# --------------------------------------------------------------------------- #
def classify(record):
    """Return (decision, reason) for one Cisco product record.

    Layered on purpose: product_category is demonstrably unreliable as a device
    -type signal (Cisco's own sample response files a uBR10012 router under
    category "Video"), so PID prefixes and the product_series naming convention
    are trusted ahead of it.
    """
    if record is None:
        return KEEP_UNKNOWN, "no record returned by Cisco"

    base_pid = (record.get("base_pid") or "").strip()
    product_type = (record.get("product_type") or "").strip()
    series = (record.get("product_series") or "").strip()
    category = (record.get("product_category") or "").strip()
    subcategory = (record.get("product_subcategory") or "").strip()

    pid_upper = base_pid.upper()
    for prefix in SWITCH_PID_PREFIXES:
        if pid_upper.startswith(prefix):
            return KEEP_SWITCH, "base_pid %s matches switch family %s" % (base_pid, prefix)

    if SWITCH_RE.search(series):
        return KEEP_SWITCH, "product_series: %s" % series
    if product_type.upper() == "SWITCH" or SWITCH_RE.search(product_type):
        return KEEP_SWITCH, "product_type: %s" % product_type
    if SWITCH_RE.search(category) or SWITCH_RE.search(subcategory):
        return KEEP_SWITCH, "product_category/subcategory: %s / %s" % (category, subcategory)

    haystack = " ".join([product_type, series, category, subcategory]).lower()
    for word in NON_SWITCH_WORDS:
        if word in haystack:
            label = product_type or series or category or base_pid
            return DROP_OTHER, "identified as %s (%s)" % (label, word)

    if not haystack.strip():
        # A record exists but carries no taxonomy at all — cannot judge it.
        return KEEP_UNCLASSIFIED, "record has no product type/series/category"

    label = product_type or series or category
    return DROP_OTHER, "no switch indicators; identified as %s" % label


# --------------------------------------------------------------------------- #
# CSV plumbing
# --------------------------------------------------------------------------- #
def find_serial_column(fieldnames, override=None):
    if override:
        for name in fieldnames:
            if name.strip().lower() == override.strip().lower():
                return name
        raise SystemExit("error: column %r not found; columns are: %s" % (override, ", ".join(fieldnames)))
    for name in fieldnames:
        if name.strip().lower() == "serial":
            return name
    # Tolerate the common variants rather than failing on a near miss.
    for name in fieldnames:
        collapsed = re.sub(r"[^a-z]", "", name.lower())
        if collapsed in ("serialnumber", "serialno", "sn", "serials"):
            return name
    raise SystemExit(
        "error: no 'serial' column found; columns are: %s\n"
        "       pass --serial-column to choose one explicitly" % ", ".join(fieldnames)
    )


def load_cache(path):
    if not path or not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (ValueError, OSError) as exc:
        print("warning: ignoring unreadable cache %s (%s)" % (path, exc), file=sys.stderr)
        return {}


def save_cache(path, cache):
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(cache, handle, indent=2, sort_keys=True)
    except OSError as exc:
        print("warning: could not write cache %s (%s)" % (path, exc), file=sys.stderr)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Filter an inventory CSV down to switches via the Cisco Product Information API.",
    )
    parser.add_argument("input", help="input inventory CSV")
    parser.add_argument("-o", "--output", help="cleaned CSV (default: <input>-cleaned.csv)")
    parser.add_argument("-r", "--report", help="per-serial decision report CSV (default: <input>-report.csv)")
    parser.add_argument("--serial-column", help="serial column name (default: auto-detect 'serial')")
    parser.add_argument("--cache", default=".cisco-product-cache.json",
                        help="lookup cache file; speeds up re-runs (default: %(default)s)")
    parser.add_argument("--no-cache", action="store_true", help="disable the lookup cache")
    parser.add_argument("--limit", type=int, help="only look up the first N distinct serials (testing)")
    parser.add_argument("-v", "--verbose", action="store_true", help="log API activity to stderr")
    args = parser.parse_args(argv)

    stem = re.sub(r"\.csv$", "", args.input, flags=re.IGNORECASE)
    output_path = args.output or stem + "-cleaned.csv"
    report_path = args.report or stem + "-report.csv"
    cache_path = None if args.no_cache else args.cache

    try:
        with open(args.input, newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise SystemExit("error: %s is empty or has no header row" % args.input)
            serial_column = find_serial_column(reader.fieldnames, args.serial_column)
            fieldnames = list(reader.fieldnames)
            rows = list(reader)
    except OSError as exc:
        raise SystemExit("error: cannot read %s (%s)" % (args.input, exc))

    print("read %d rows from %s (serial column: %r)" % (len(rows), args.input, serial_column))

    serials = []
    seen = set()
    for row in rows:
        serial = (row.get(serial_column) or "").strip()
        if serial and serial.upper() not in seen:
            seen.add(serial.upper())
            serials.append(serial)
    if args.limit:
        serials = serials[: args.limit]
    print("found %d distinct serials to look up" % len(serials))

    cache = load_cache(cache_path)
    pending = [s for s in serials if s.upper() not in cache]
    if cache:
        print("%d cached, %d to fetch" % (len(serials) - len(pending), len(pending)))

    if pending:
        client_id = os.environ.get("CISCO_CLIENT_ID")
        client_secret = os.environ.get("CISCO_CLIENT_SECRET")
        if not client_id or not client_secret:
            raise SystemExit(
                "error: CISCO_CLIENT_ID and CISCO_CLIENT_SECRET must be set\n"
                "       (register an app at https://apiconsole.cisco.com)"
            )
        client = CiscoClient(client_id, client_secret, verbose=args.verbose)

        for start in range(0, len(pending), BATCH_SIZE):
            batch = pending[start : start + BATCH_SIZE]
            print("  looking up %d-%d of %d" % (start + 1, start + len(batch), len(pending)))
            try:
                found = client.lookup(batch)
            except CiscoAPIError as exc:
                # Keep going: unresolved serials are kept, not silently dropped.
                print("  warning: batch failed (%s) — those serials will be kept" % exc,
                      file=sys.stderr)
                for serial in batch:
                    cache[serial.upper()] = {"__error__": str(exc)}
                continue
            for serial in batch:
                cache[serial.upper()] = found.get(serial.upper())
            time.sleep(REQUEST_PAUSE_SECONDS)

        save_cache(cache_path, cache)

    decisions = {}
    for serial in serials:
        entry = cache.get(serial.upper())
        if isinstance(entry, dict) and "__error__" in entry:
            decisions[serial.upper()] = (KEEP_ERROR, entry["__error__"], None)
        else:
            decision, reason = classify(entry)
            decisions[serial.upper()] = (decision, reason, entry)

    kept_rows = []
    report_rows = []
    counts = {}
    for row in rows:
        serial = (row.get(serial_column) or "").strip()
        if not serial:
            decision, reason, record = KEEP_UNCLASSIFIED, "row has no serial", None
        elif serial.upper() in decisions:
            decision, reason, record = decisions[serial.upper()]
        else:
            decision, reason, record = KEEP_UNKNOWN, "not looked up (--limit)", None

        counts[decision] = counts.get(decision, 0) + 1
        if decision != DROP_OTHER:
            kept_rows.append(row)

        record = record or {}
        report_rows.append(
            {
                "serial": serial,
                "decision": "keep" if decision != DROP_OTHER else "remove",
                "classification": decision,
                "reason": reason,
                "base_pid": record.get("base_pid", ""),
                "product_type": record.get("product_type", ""),
                "product_series": record.get("product_series", ""),
                "product_category": record.get("product_category", ""),
                "product_subcategory": record.get("product_subcategory", ""),
                "product_name": record.get("product_name", ""),
            }
        )

    try:
        with open(output_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(kept_rows)
        with open(report_path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(report_rows[0].keys()) if report_rows else ["serial"])
            writer.writeheader()
            writer.writerows(report_rows)
    except OSError as exc:
        raise SystemExit("error: cannot write output (%s)" % exc)

    print("")
    print("kept %d of %d rows -> %s" % (len(kept_rows), len(rows), output_path))
    print("decisions -> %s" % report_path)
    for decision in (KEEP_SWITCH, KEEP_UNKNOWN, KEEP_UNCLASSIFIED, KEEP_ERROR, DROP_OTHER):
        if counts.get(decision):
            verb = "removed" if decision == DROP_OTHER else "kept"
            print("  %-16s %5d  (%s)" % (decision, counts[decision], verb))
    return 0


if __name__ == "__main__":
    sys.exit(main())
