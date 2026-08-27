"""The desired state, in one file.

Values that are the same on every device -- the NTP servers, the syslog
collectors, the SNMP users -- belong in `standards.yaml` beside this tool, not
in a shell command somebody has to retype correctly at 2am. The file states the
standard platform-neutrally; each template maps it onto one platform's syntax.

CLI flags still work and still win, so a one-off run does not need the file
edited. What the file removes is the need to remember the fleet's values.

A value may point at another part of the document with a dotted path, so the
SNMP poller networks are stated once and the ACL that enforces them refers to
them rather than repeating them:

    acls:
      - name: SNMP-POLLERS
        permit: snmp.allow
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

#: Looked for, in order, when --standards is not given.
DEFAULT_FILENAMES = ("standards.yaml", "standards.yml", "standards.json")

#: What ships with the tool. Never used for a real run -- its addresses are
#: placeholders, and converging a fleet onto them would be worse than failing.
#: `configure.py selftest` may fall back to it, because that renders templates
#: offline and touches nothing.
EXAMPLE_FILENAME = "standards.yaml.example"

#: Sections this tool understands. A typo would otherwise read as "that
#: standard is not defined", which looks compliant while enforcing nothing.
KNOWN_SECTIONS = {
    "ntp": {"servers", "vrf", "source", "prefer", "iburst"},
    "syslog": {"destinations", "severity", "source", "vrf", "facility"},
    "snmp": {
        "allow",
        "acl",
        "communities",
        "contact",
        "location",
        "chassis_id",
        "users",
        "groups",
        "views",
        "hosts",
        "packetsize",
    },
    "banner": {"motd", "login", "delimiter"},
    "acls": None,  # a list, not a mapping
    "local_accounts": {"names", "privilege", "role"},
    # Passthrough to ServiceNow change fields. change_request has hundreds of
    # valid columns, so warning about an unrecognised one would be noise.
    "change": None,
}

_DOTTED_PATH = re.compile(r"^[a-z_][a-z0-9_]*(\.[a-z_][a-z0-9_]*)+$", re.IGNORECASE)


class StandardsError(Exception):
    """The standards file is missing, unreadable, or shaped wrongly."""


def find_standards(
    explicit: Optional[str], project_root: Path, allow_example: bool = False
) -> Optional[Path]:
    """Locate the standards file.

    Checked in order: what was asked for, the working directory, then this
    tool's own directory. Deliberately not any directory above: this tool is
    self-contained, and reaching up into a shared file would couple it to
    whatever else lives in the tree.

    `allow_example` falls back to the shipped example. Only the offline
    selftest passes it: a real run against placeholder addresses would be a
    great deal worse than a run that stops and says the file is missing.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise StandardsError(f"standards file not found: {path}")
        return path
    for directory in (Path.cwd(), project_root):
        for name in DEFAULT_FILENAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    if allow_example:
        candidate = project_root / EXAMPLE_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _load_document(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            raise StandardsError(f"{path} is not valid JSON: {exc}") from exc
    else:
        try:
            import yaml
        except ImportError as exc:  # pragma: no cover - depends on install
            raise StandardsError(
                f"reading {path} needs PyYAML (pip install -r requirements.txt), "
                f"or write the file as JSON instead"
            ) from exc
        try:
            document = yaml.safe_load(text) or {}
        except yaml.YAMLError as exc:
            raise StandardsError(f"{path} is not valid YAML: {exc}") from exc

    if not isinstance(document, dict):
        raise StandardsError(f"{path} must be a mapping of standard name to settings")
    return document


@dataclass
class Standards:
    """The parsed file, plus the lookups the features need."""

    path: Optional[Path] = None
    document: Dict[str, Any] = field(default_factory=dict)
    #: Unknown keys, reported once by the CLI rather than raised -- the same
    #: choice scripts/f5 makes, so one file can carry another tool's sections.
    warnings: List[str] = field(default_factory=list)

    @property
    def loaded(self) -> bool:
        return self.path is not None

    def section(self, name: str) -> Dict[str, Any]:
        value = self.document.get(name)
        if value is None:
            return {}
        if not isinstance(value, dict):
            raise StandardsError(f"{self.path}: '{name}' must be a mapping")
        return value

    def value(self, dotted: str, default: Any = None) -> Any:
        """Look up 'snmp.allow', resolving references as it goes."""
        node: Any = self.document
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return self.resolve(node)

    def resolve(self, value: Any) -> Any:
        """Expand a dotted-path reference into what it points at.

        Only a bare string that looks like a path and actually resolves is
        treated as a reference, so a hostname with a dot in it is still a
        hostname.
        """
        if isinstance(value, str) and _DOTTED_PATH.match(value):
            node: Any = self.document
            for part in value.split("."):
                if not isinstance(node, dict) or part not in node:
                    return value
                node = node[part]
            return node
        return value

    def entries(self, dotted: str) -> List[Any]:
        """A list-valued standard, resolved. Missing reads as empty."""
        value = self.value(dotted)
        if value is None:
            return []
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise StandardsError(f"{self.path}: '{dotted}' must be a list")
        return [self.resolve(item) for item in value]

    def defined(self, dotted: str) -> bool:
        """Whether the file says anything about this standard at all.

        An empty list is a statement -- `snmp.communities: []` means 'there must
        be none' -- so it counts as defined.
        """
        node: Any = self.document
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return False
            node = node[part]
        return node is not None


def load(
    explicit: Optional[str], project_root: Path, allow_example: bool = False
) -> Standards:
    """Find and read the standards file. Absent is not an error -- the flags
    still work without it."""
    path = find_standards(explicit, project_root, allow_example)
    if path is None:
        return Standards()

    document = _load_document(path)
    warnings: List[str] = []
    for key, section in document.items():
        if key not in KNOWN_SECTIONS:
            warnings.append(
                f"{path}: ignoring unknown standard {key!r} "
                f"(known: {', '.join(sorted(KNOWN_SECTIONS))})"
            )
            continue
        known_keys = KNOWN_SECTIONS[key]
        if known_keys is None or section is None:
            continue
        if not isinstance(section, dict):
            raise StandardsError(f"{path}: '{key}' must be a mapping")
        for sub in section:
            if sub not in known_keys:
                warnings.append(
                    f"{path}: ignoring unknown key '{key}.{sub}' "
                    f"(known: {', '.join(f'{key}.{k}' for k in sorted(known_keys))})"
                )
    return Standards(path=path, document=document, warnings=warnings)


def host_and_port(item: Any, default_port: Optional[int] = None) -> Dict[str, Any]:
    """Normalize `10.1.1.50` or `{host: 10.1.1.51, port: 1514}` into one shape.

    Both spellings are already in scripts/standards.yaml, so both are accepted
    wherever a destination is expected.
    """
    if isinstance(item, Mapping):
        record = dict(item)
        if "host" not in record:
            raise StandardsError(f"destination {item!r} has no 'host' key")
    else:
        record = {"host": item}
    record["host"] = str(record["host"])
    if record.get("port") is None and default_port is not None:
        record["port"] = default_port
    return record


def of(args) -> Standards:
    """The standards attached to a parsed namespace, or an empty set.

    Features call this rather than reaching for the attribute directly, so a
    namespace built in a test without one still works.
    """
    return getattr(args, "standards", None) or Standards()
