"""Remembering what a device is, so we do not ask it every time.

Autodetection costs a whole extra SSH login per device -- `SSHDetect` opens its
own session, fingerprints the box, and disconnects, before the real task
connects again. On a fleet that is the slowest part of a run, and the answer
almost never changes.

So the answer is written to a small JSON file and reused for 24 hours. The
cache only ever fills in a *blank* platform: a `platform` column in the CSV is
an explicit statement and always wins.

The staleness window is the trade. A device whose platform genuinely changes --
re-purposed hardware, an IOS box swapped for EOS -- is wrong until the entry
expires. `--no-platform-cache` skips it for one run and `discover --refresh`
rebuilds it; and a wrong platform fails loudly rather than quietly, because the
device rejects the show command and the run stops on that device.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional

DEFAULT_FILENAME = ".platform-cache.json"
DEFAULT_TTL_HOURS = 24.0

#: Entries older than this are dropped when the file is written, so a cache
#: does not accumulate every device that has ever been in a CSV.
MAX_AGE = timedelta(days=30)

_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(moment: datetime) -> str:
    return moment.strftime(_FORMAT)


def _parse(stamp: Any) -> Optional[datetime]:
    try:
        return datetime.strptime(str(stamp), _FORMAT).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def cache_path(explicit: Optional[str], project_root: Path) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get("NETOPS_PLATFORM_CACHE") or project_root / DEFAULT_FILENAME)


class PlatformCache:
    """A device address to what it turned out to be, with a timestamp."""

    def __init__(self, path: Optional[Path], ttl_hours: float = DEFAULT_TTL_HOURS) -> None:
        self.path = path
        self.ttl = timedelta(hours=ttl_hours)
        self.entries: Dict[str, Dict[str, str]] = {}
        self.hits = 0
        self.writes = 0
        self._loaded = False

    # -- identity ----------------------------------------------------------

    @staticmethod
    def key(host) -> str:
        """What is being cached is the answer for an *address*, not a name.

        Renaming a device in the CSV does not make it a different box, and two
        rows pointing at the same address are the same box.
        """
        return f"{host.hostname}:{host.port or 22}"

    # -- reading -----------------------------------------------------------

    def load(self) -> "PlatformCache":
        self._loaded = True
        if self.path is None or not self.path.is_file():
            return self
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A corrupt or unreadable cache is not worth failing a run over.
            # It is rebuilt by detecting, which is what would have happened
            # without it.
            return self
        if isinstance(document, dict) and isinstance(document.get("devices"), dict):
            self.entries = {
                str(key): value
                for key, value in document["devices"].items()
                if isinstance(value, dict) and value.get("platform")
            }
        return self

    def get(self, host) -> Optional[str]:
        """The remembered platform, if it is still fresh."""
        if self.path is None:
            return None  # disabled: inert, so the caller needs no special case
        entry = self.entries.get(self.key(host))
        if not entry:
            return None
        detected = _parse(entry.get("detected_at"))
        if detected is None or _now() - detected > self.ttl:
            return None
        self.hits += 1
        return str(entry["platform"])

    # -- writing -----------------------------------------------------------

    def put(self, host, platform: str) -> None:
        if self.path is None:
            return
        self.entries[self.key(host)] = {
            "platform": platform,
            "name": host.name,
            "detected_at": _stamp(_now()),
        }
        self.writes += 1

    def save(self) -> None:
        """Write atomically, so two runs finishing together cannot leave half a
        file behind."""
        if self.path is None or not self.writes:
            return
        cutoff = _now() - MAX_AGE
        keep = {
            key: value
            for key, value in self.entries.items()
            if (_parse(value.get("detected_at")) or cutoff) >= cutoff
        }
        document = {"updated_at": _stamp(_now()), "devices": keep}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(self.path.parent), delete=False
        )
        try:
            with handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(handle.name, self.path)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise


def load(explicit: Optional[str], project_root: Path, ttl_hours: float, enabled: bool = True):
    """The cache described by the flags. Disabled gives one that remembers
    nothing, so the caller needs no special case."""
    if not enabled:
        return PlatformCache(None, ttl_hours)
    return PlatformCache(cache_path(explicit, project_root), ttl_hours).load()
