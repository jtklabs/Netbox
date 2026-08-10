"""Remember how large a GETBULK each device will actually answer.

Some devices never answer a GETBULK asking for 25 varbinds: the reply exceeds
the path MTU, gets fragmented, and something drops it. Finding the largest
request a device *will* answer costs a timeout per rung stepped down, which is
fine once and wasteful every six hours forever. So it is written down.

The file is a plain JSON map of host to the setting that worked. It is a cache
in the strict sense — deleting it costs one slow scan and nothing else — so
every failure here is logged and swallowed. A poller that cannot write its
cache directory must still scan the fleet.

Entries expire. A device that was behind a broken path when it was measured
should not be scanned pessimistically forever once somebody fixes the MTU, so
after `ttl_days` the measurement is discarded and the next scan re-derives it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time

log = logging.getLogger(__name__)

__all__ = ('BulkState', 'GETNEXT_ONLY')

# Stored instead of a repetition count for a device that answers no GETBULK at
# all. Zero rather than None so the JSON stays a plain host->int map.
GETNEXT_ONLY = 0

DEFAULT_TTL_DAYS = 7


class BulkState:
    """A persistent host -> max_repetitions map, safe to share across threads."""

    def __init__(self, path: str = "", ttl_days: int = DEFAULT_TTL_DAYS):
        self.path = path
        self.ttl_seconds = ttl_days * 86400
        self._entries: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._dirty = False
        if path:
            self._load()

    def _load(self) -> None:
        try:
            with open(self.path) as handle:
                raw = json.load(handle)
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            # A corrupt cache is not worth a failed scan; start over.
            log.warning("ignoring unreadable GETBULK cache %s: %s", self.path, exc)
            return
        if not isinstance(raw, dict):
            return

        now = time.time()
        for host, entry in raw.items():
            if not isinstance(entry, dict):
                continue
            try:
                reps = int(entry["max_repetitions"])
                measured = float(entry.get("measured_at", 0))
            except (KeyError, TypeError, ValueError):
                continue
            if self.ttl_seconds and now - measured > self.ttl_seconds:
                # Re-measure rather than stay pessimistic about a path that may
                # well have been fixed since.
                log.debug("GETBULK cache entry for %s has expired", host)
                self._dirty = True
                continue
            self._entries[host] = {"max_repetitions": reps, "measured_at": measured}

    def limit_for(self, host: str, configured: int) -> int:
        """The repetition count to start `host` on.

        Never above `configured`: lowering the fleet-wide setting must take
        effect immediately rather than being overridden by a cheerier
        measurement taken last week.
        """
        with self._lock:
            entry = self._entries.get(host)
        if entry is None:
            return configured
        remembered = entry["max_repetitions"]
        if remembered == GETNEXT_ONLY:
            return GETNEXT_ONLY
        return min(remembered, configured)

    def remember(self, host: str, max_repetitions: int, configured: int) -> None:
        """Record what `host` settled on, if it is worth recording.

        A device that managed the configured maximum needs no entry — that is
        the default anyway, and leaving it out means raising the fleet-wide
        setting later actually applies to it.
        """
        with self._lock:
            if max_repetitions >= configured:
                if self._entries.pop(host, None) is not None:
                    self._dirty = True
                return
            previous = self._entries.get(host)
            if previous and previous["max_repetitions"] == max_repetitions:
                return
            self._entries[host] = {
                "max_repetitions": max_repetitions,
                "measured_at": time.time(),
            }
            self._dirty = True

    def save(self) -> None:
        """Write the cache out. Never raises: this is only ever an optimisation."""
        if not self.path:
            return
        with self._lock:
            if not self._dirty:
                return
            snapshot = dict(self._entries)
            self._dirty = False
        try:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            # Written to a sibling and renamed so a killed poller cannot leave
            # a half-written file that the next run then refuses to parse.
            fd, temp = tempfile.mkstemp(dir=directory, prefix=".bulkstate-")
            with os.fdopen(fd, "w") as handle:
                json.dump(snapshot, handle, indent=2, sort_keys=True)
            os.replace(temp, self.path)
            log.debug("wrote GETBULK cache for %d device(s) to %s",
                      len(snapshot), self.path)
        except OSError as exc:
            log.warning("could not write GETBULK cache %s: %s — scans will "
                        "re-derive the limit each run", self.path, exc)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)
