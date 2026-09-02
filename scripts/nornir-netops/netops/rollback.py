"""Undoing a change, and being honest about when that is impossible.

A rollback cannot be worked out after the fact: once `logging trap
informational` has replaced `notifications`, the device no longer knows it was
ever `notifications`. So the reversal is computed at the moment of the change,
while the pre-change state is still in hand, and written to a journal. The
`rollback` subcommand replays it.

The reversal is the same shape for most features: negate every command that was
sent, then re-apply the lines the device had before. Re-applying a line that is
still present is a harmless no-op, and doing it wholesale is what restores a
scalar -- `no logging trap informational` followed by `logging trap
notifications`.

**What cannot be undone.** Anything whose old value the device would not tell
us. A local account's password, an SNMPv3 passphrase and an NTP key are stored
hashed or encrypted, so the previous one is unreadable and no journal can hold
it. An SNMP community string could be recorded, but it is a credential and this
does not write credentials to disk. Those are reported as unrestorable at the
time of the change -- when there is still a chance to take a backup -- rather
than discovered later.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: Where journals are kept when nothing else is said.
DEFAULT_DIRECTORY = ".rollback"

_STAMP = "%Y%m%dT%H%M%SZ"


class RollbackError(Exception):
    """A journal is missing, unreadable, or describes nothing to do."""


@dataclass
class Reversal:
    """How to put one device back, and what cannot be put back."""

    commands: List[str] = field(default_factory=list)
    unsupported: List[str] = field(default_factory=list)

    @property
    def possible(self) -> bool:
        return bool(self.commands)


def default_reversal(
    commands: Sequence[str],
    current: Sequence[Any],
    removed: Sequence[Any],
    secrets: Sequence[str] = (),
) -> Reversal:
    """Negate what was added, then re-apply what was there.

    Re-applying every pre-change line rather than only the ones that were
    removed is deliberate: it is what puts a scalar back. `logging trap` was
    never negated -- setting the new value replaced it -- so the only way to
    restore the old severity is to send the old line again.
    """
    reversal = Reversal()
    for command in commands:
        if command.startswith("no "):
            # Undoing a negation is not `no no ...`; it is putting the line
            # back, which the current-state replay below does.
            continue
        if any(secret and secret in command for secret in secrets):
            # Undoing it would mean writing the secret to the journal, and the
            # old value is unreadable anyway.
            reversal.unsupported.append(
                f"{command.split()[0]} ... (carries a secret; not recorded)"
            )
            continue
        reversal.commands.append(f"no {command}")
    for entry in current:
        if entry.data.get("restorable", True):
            reversal.commands.append(entry.line)
    for entry in removed:
        if not entry.data.get("restorable", True):
            reversal.unsupported.append(entry.shown)
    return reversal


# --------------------------------------------------------------------------- #
# the journal
# --------------------------------------------------------------------------- #


def journal_directory(explicit: Optional[str], project_root: Path) -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return Path(os.environ.get("NETOPS_ROLLBACK_DIR") or project_root / DEFAULT_DIRECTORY)


@dataclass
class Journal:
    """What was done to which devices, and how to undo it."""

    feature: str = ""
    mode: str = ""
    recorded_at: str = ""
    devices: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    path: Optional[Path] = None

    def add(self, name: str, record: Mapping[str, Any]) -> None:
        self.devices[name] = dict(record)

    @property
    def restorable(self) -> Dict[str, Dict[str, Any]]:
        return {n: r for n, r in self.devices.items() if r.get("rollback")}

    def save(self, directory: Path) -> Optional[Path]:
        """Write the journal. Nothing changed means nothing to write."""
        if not self.devices:
            return None
        self.recorded_at = datetime.now(timezone.utc).strftime(_STAMP)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{self.recorded_at}-{self.feature}.json"
        document = {
            "feature": self.feature,
            "mode": self.mode,
            "recorded_at": self.recorded_at,
            "devices": self.devices,
        }
        handle = tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=str(directory), delete=False
        )
        try:
            with handle:
                json.dump(document, handle, indent=2, sort_keys=True)
                handle.write("\n")
            os.replace(handle.name, path)
            os.chmod(path, 0o600)
        except OSError:
            Path(handle.name).unlink(missing_ok=True)
            raise
        self.path = path
        return path


def load(path: Path) -> Journal:
    if not path.is_file():
        raise RollbackError(f"no rollback journal at {path}")
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RollbackError(f"{path} is not a readable journal: {exc}") from exc
    if not isinstance(document, dict) or not isinstance(document.get("devices"), dict):
        raise RollbackError(f"{path} does not look like a rollback journal")
    return Journal(
        feature=str(document.get("feature", "")),
        mode=str(document.get("mode", "")),
        recorded_at=str(document.get("recorded_at", "")),
        devices=document["devices"],
        path=path,
    )


def latest(directory: Path) -> Path:
    """The most recent journal, by the timestamp in its name."""
    journals = sorted(directory.glob("*.json"))
    if not journals:
        raise RollbackError(
            f"no rollback journal in {directory} -- one is written by each --apply "
            f"that changes something"
        )
    return journals[-1]
