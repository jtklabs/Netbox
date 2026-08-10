"""Run net-snmp's CLI tools and parse what they print.

Why the CLI and not a Python SNMP library: pysnmp's API has been rewritten
more than once and the package name has moved around, so a poller box pinned a
year ago and one built today do not agree on how to open a session. net-snmp's
command-line tools have parsed the same arguments and printed the same output
for two decades and are already on every network-adjacent Linux box.

Credentials never appear on the command line. Anything passed as argv is world
readable through `ps` for as long as the process lives, and an SNMPv3 auth or
privacy passphrase is a real credential. Instead each credential set is written
to a private `snmp.conf` in a 0700 temp directory and net-snmp is pointed at it
with SNMPCONFPATH. The file is removed when the scan finishes.

Output format: `-On -Oe` rather than the `-Oq` that looks tidier. `-Oq` drops
the type tag, and the type tag is what distinguishes `Hex-STRING: 00 11 22`
(a MAC) from `STRING: 00 11 22` (a label that happens to look like one), and
what tells us an OID column holds a RowPointer we need to decode. Losing it
would mean guessing, which is the habit this whole tool exists to break.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

# net-snmp exit statuses and messages are not stable enough to branch on, so we
# classify by matching the text it prints. These are the messages net-snmp 5.x
# emits; each is anchored enough not to collide with a device's own strings.
_AUTH_FAILURE_PATTERNS = (
    "Authentication failure",
    "Unknown user name",
    "Unsupported security level",
    "Unknown security name",
    "Decryption error",
    "Wrong digest",
    "authorizationError",
    "Authentication failed for",
)
_TIMEOUT_PATTERNS = (
    "Timeout: No Response",
    "No Response from",
    # A closed UDP port produces the terse form: "snmpbulkwalk: Timeout".
    "snmpwalk: Timeout",
    "snmpbulkwalk: Timeout",
    "snmpget: Timeout",
)
_UNREACHABLE_PATTERNS = (
    "unknown host",
    "Unknown host",
    "No route to host",
    "Network is unreachable",
    "Connection refused",
)

# net-snmp chatters on stderr about MIBs it cannot parse. On a stock Ubuntu
# poller — the documented target — the IETF MIBs are not installed at all, so
# "Cannot find module (SNMPv2-MIB)" is printed on every single invocation.
# It says nothing about the device and must never be mistaken for a failure;
# we ask for numeric OIDs and never need a MIB loaded.
_IGNORED_STDERR_PATTERNS = (
    "Cannot find module",
    "Did not find",
    "Cannot adopt OID",
    "Unlinked OID",
    "Undefined identifier",
    "MIB search path",
    # net-snmp creates a cert_indexes directory under its persistent dir on
    # first use and announces it on stderr.
    "Created directory:",
)

# Repetition counts tried, in order, when a device will not answer a GETBULK at
# the configured size. Rungs rather than a halving search because each one costs
# a full timeout, so the ladder is deliberately short: from the default of 25
# the worst case is three extra timeouts before GETNEXT, once per device, and
# the result is then cached across runs. 10 is chosen to fit a typical table's
# reply inside a 1500-byte path; 4 to survive a badly fragmented tunnel.
BULK_LADDER = (10, 4)

# A walk that returns these for the very first OID means the agent has nothing
# in that subtree — normal, not an error.
_EMPTY_SUBTREE_PATTERNS = (
    "No Such Object available",
    "No Such Instance currently exists",
    "No more variables left in this MIB View",
    "End of MIB",
)


class SnmpError(Exception):
    """Any failure talking to a device."""


class SnmpAuthError(SnmpError):
    """The agent answered but rejected these credentials — try the next set."""


class SnmpTimeoutError(SnmpError):
    """The agent never answered. Trying other credentials would just wait again."""


class SnmpToolMissing(SnmpError):
    """net-snmp is not installed on this poller."""


class SnmpInvocationError(SnmpError):
    """net-snmp rejected our arguments.

    Always a bug here, never a device problem, so it is deliberately not
    caught by the GETBULK-to-GETNEXT fallback: falling back would paper over a
    malformed command line and turn it into a silent per-device slowdown.
    """


@dataclass(frozen=True)
class Credential:
    """One SNMPv3 credential set.

    v3 only. v2c sends a community string in clear text on the wire, and the
    whole point of scanning production gear from a poller box is that the
    credentials survive being on the network.
    """

    name: str
    security_name: str
    auth_protocol: str = "SHA"       # MD5 | SHA | SHA-224 | SHA-256 | SHA-384 | SHA-512
    auth_passphrase: str = ""
    priv_protocol: str = "AES"       # DES | AES | AES-192 | AES-256
    priv_passphrase: str = ""
    security_level: str = ""         # derived when blank
    context: str = ""

    def level(self) -> str:
        """Work out the security level from which passphrases were supplied."""
        if self.security_level:
            return self.security_level
        if self.auth_passphrase and self.priv_passphrase:
            return "authPriv"
        if self.auth_passphrase:
            return "authNoPriv"
        return "noAuthNoPriv"

    def config_text(self) -> str:
        """Render this credential set as a net-snmp snmp.conf."""
        lines = [
            "# Generated by scripts/snmp-inventory. Contains SNMPv3 passphrases.",
            "defVersion 3",
            f"defSecurityLevel {self.level()}",
            f"defSecurityName {self.security_name}",
        ]
        if self.auth_passphrase:
            lines.append(f"defAuthType {self.auth_protocol}")
            lines.append(f"defAuthPassphrase {self.auth_passphrase}")
        if self.priv_passphrase:
            lines.append(f"defPrivType {self.priv_protocol}")
            lines.append(f"defPrivPassphrase {self.priv_passphrase}")
        if self.context:
            lines.append(f"defContext {self.context}")
        return "\n".join(lines) + "\n"


@dataclass
class VarBind:
    """One OID/value pair as net-snmp printed it."""

    oid: str          # numeric, no leading dot
    type: str         # STRING, INTEGER, Hex-STRING, OID, Timeticks, IpAddress...
    value: str        # already unquoted / de-hexed into a usable form

    def as_int(self, default: int | None = None) -> int | None:
        try:
            return int(self.value)
        except (TypeError, ValueError):
            return default


class CredentialSession:
    """A temp directory holding one credential set's snmp.conf.

    Used as a context manager so the passphrases on disk have a bounded life:

        with CredentialSession(cred) as session:
            binds = session.walk("10.0.0.1", "1.3.6.1.2.1.1")
    """

    def __init__(self, credential: Credential, timeout: int = 5, retries: int = 1,
                 use_bulk: bool = True, max_repetitions: int = 25):
        self.credential = credential
        self.timeout = timeout
        self.retries = retries
        self.use_bulk = use_bulk
        self.max_repetitions = max_repetitions
        # Set once anything at all comes back from the host. It is what lets a
        # GETBULK that goes unanswered be told apart from a host that is simply
        # not there — see walk().
        self.answered = False
        self._dir: str | None = None

    def __enter__(self) -> CredentialSession:
        # 0700 by default from mkdtemp; the config inside is written 0600.
        self._dir = tempfile.mkdtemp(prefix="snmpinv-")
        path = os.path.join(self._dir, "snmp.conf")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(self.credential.config_text())
        return self

    def __exit__(self, *exc_info) -> None:
        if self._dir:
            shutil.rmtree(self._dir, ignore_errors=True)
            self._dir = None

    def _env(self) -> dict[str, str]:
        env = dict(os.environ)
        # SNMPCONFPATH replaces the whole search path, so the operator's own
        # ~/.snmp/snmp.conf cannot leak a different community or user into a
        # scan and silently change which credentials were actually used.
        env["SNMPCONFPATH"] = self._dir or ""
        # net-snmp caches each agent's engine boots/time in its persistent
        # directory. Point that at the temp dir too: on a poller running as a
        # service account /var/lib/snmp is usually not writable, and a stale
        # engine time from a previous run causes spurious auth failures.
        env["SNMP_PERSISTENT_DIR"] = self._dir or ""
        # Every OID we send is numeric, so loading the MIB tree buys nothing.
        # Turning it off removes a few hundred milliseconds per invocation and
        # silences the "Cannot find module" chatter that a poller without the
        # IETF MIBs installed would otherwise print on every call.
        env["MIBS"] = ""
        return env

    def _run(self, tool: str, host: str, oid: str, numeric_timeticks: bool = True) -> str:
        if self._dir is None:
            raise RuntimeError("CredentialSession must be used as a context manager")
        binary = shutil.which(tool)
        if binary is None:
            raise SnmpToolMissing(
                f"{tool} not found — install net-snmp "
                "(apt install snmp / yum install net-snmp-utils)"
            )
        argv = [binary, "-On", "-Oe", "-t", str(self.timeout), "-r", str(self.retries)]
        if numeric_timeticks:
            # -Ot prints timeticks as a bare integer, which is easier to parse.
            # It also strips the `Timeticks:` type tag, so it is turned off when
            # capturing a fixture — there the point is fidelity, not convenience.
            argv.insert(3, "-Ot")
        if tool == "snmpbulkwalk":
            # net-snmp's application-specific -C options take their value
            # attached, not as a separate argument: "-Cr25", never "-Cr 25".
            # The spaced form is rejected outright with a usage message, which
            # would break every bulk walk.
            argv.append(f"-Cr{self.max_repetitions}")
        argv += [host, oid]
        # Note there is no -u/-A/-X/-l here on purpose: everything about the
        # credential comes from SNMPCONFPATH.
        try:
            proc = subprocess.run(
                argv,
                env=self._env(),
                capture_output=True,
                text=True,
                # A hung agent should not hang the scan. net-snmp's own -t/-r
                # bound each request, but this bounds the whole walk.
                timeout=max(30, self.timeout * (self.retries + 1) * 12),
            )
        except subprocess.TimeoutExpired as exc:
            raise SnmpTimeoutError(f"{tool} against {host} exceeded its overall time budget") from exc

        _raise_for_message(_meaningful(proc.stdout, proc.stderr), host,
                           returncode=proc.returncode)
        return proc.stdout

    def walk(self, host: str, oid: str) -> list[VarBind]:
        """Walk a subtree. Returns [] when the agent has nothing there.

        A GETBULK that goes unanswered is stepped down rather than abandoned —
        see _step_down for why, and for why it is not simply left off.
        """
        while self.use_bulk:
            try:
                return parse_varbinds(self._run("snmpbulkwalk", host, oid))
            except (SnmpAuthError, SnmpInvocationError, SnmpToolMissing):
                raise
            except SnmpTimeoutError:
                if not self.answered:
                    # Nothing has ever come back from this host, so this is
                    # silence, not a GETBULK problem. Retrying at any size
                    # would just wait out another full timeout.
                    raise
                self._step_down(host, oid)
            except SnmpError:
                # Some older agents answer GETNEXT fine but reject GETBULK
                # outright. Falling back costs one round trip and rescues it.
                break
        return parse_varbinds(self._run("snmpwalk", host, oid))

    def _step_down(self, host: str, oid: str) -> None:
        """Ask for fewer varbinds per request, or give up on GETBULK entirely.

        The host answers GETs but not this GETBULK, which is the classic
        oversized-reply failure: one GETBULK returns max_repetitions varbinds
        in a single packet, and once that passes the path MTU it is fragmented
        — which plenty of firewalls and device CPUs drop outright. Nothing
        comes back and it looks exactly like an unreachable host.

        It is not a property of the model, which is what makes it confusing to
        chase: two identical switches differ if one has longer interface
        descriptions or more sysORTable rows.

        Stepping down beats dropping straight to GETNEXT because the amount by
        which a device overshoots varies. Something that cannot manage 25 will
        usually manage 10, which is still ten times fewer round trips than
        GETNEXT — and on a 48-port stack that difference is most of the scan.

        Whatever it settles on is latched for the rest of the session, so the
        cost is paid once per device rather than once per table.
        """
        for rung in BULK_LADDER:
            if rung < self.max_repetitions:
                log.warning(
                    "%s did not answer a GETBULK of %d at %s but does answer "
                    "GETs — its replies are too big for the path. Retrying at "
                    "%d for this device.",
                    host, self.max_repetitions, oid, rung,
                )
                self.max_repetitions = rung
                return
        log.warning(
            "%s answers no GETBULK even at %d — falling back to GETNEXT for "
            "this device (one varbind per packet, so it always fits).",
            host, self.max_repetitions,
        )
        self.use_bulk = False

    def settled_repetitions(self) -> int:
        """What this session ended up using, for caching across runs.

        Zero means GETBULK was abandoned altogether.
        """
        return self.max_repetitions if self.use_bulk else 0

    def walk_raw(self, host: str, oid: str) -> str:
        """Walk a subtree and return net-snmp's output unparsed.

        For capturing a device as a test fixture: the recorded-walk format is
        literally what these tools print, so the useful thing is the text
        before anything has interpreted it.
        """
        try:
            tool = "snmpbulkwalk" if self.use_bulk else "snmpwalk"
            return self._run(tool, host, oid, numeric_timeticks=False)
        except SnmpError:
            return self._run("snmpwalk", host, oid, numeric_timeticks=False)

    def get(self, host: str, oids: list[str]) -> dict[str, VarBind]:
        """Fetch scalars. Missing instances are simply absent from the result."""
        if not oids:
            return {}
        binary = shutil.which("snmpget")
        if binary is None:
            raise SnmpToolMissing("snmpget not found — install net-snmp")
        argv = [binary, "-On", "-Oe", "-Ot", "-t", str(self.timeout), "-r", str(self.retries), host]
        argv += oids
        try:
            proc = subprocess.run(argv, env=self._env(), capture_output=True, text=True,
                                  timeout=max(30, self.timeout * (self.retries + 1) * 4))
        except subprocess.TimeoutExpired as exc:
            raise SnmpTimeoutError(f"snmpget against {host} timed out") from exc
        # snmpget returns non-zero when *any* requested OID is absent, which is
        # routine when probing several vendors' scalars, so only genuine
        # transport/auth failures are escalated.
        _raise_for_message(_meaningful(proc.stdout, proc.stderr), host)
        # Getting here means the agent replied. Record that even if it replied
        # "no such object": what matters later is that packets come back at all.
        self.answered = True
        return {bind.oid: bind for bind in parse_varbinds(proc.stdout)}

    def probe(self, host: str) -> bool:
        """Cheapest possible check that these credentials work on this host.

        One GET of sysObjectID: a single small request and a single small
        reply, so it cannot fail for size reasons the way a GETBULK can. Doing
        this before any walk is what separates "silent" from "dislikes
        GETBULK", and it rejects a wrong credential set after one packet
        instead of a full walk.
        """
        binds = self.get(host, ["1.3.6.1.2.1.1.2.0"])
        return bool(binds)


def _meaningful(stdout: str, stderr: str) -> str:
    """Combine output, dropping net-snmp's MIB-loading chatter.

    Without this the harmless "Cannot find module" lines a MIB-less poller
    prints get matched against the failure patterns below.
    """
    lines = [
        line for line in f"{stdout}\n{stderr}".splitlines()
        if not any(noise in line for noise in _IGNORED_STDERR_PATTERNS)
    ]
    return "\n".join(lines)


def _raise_for_message(text: str, host: str, returncode: int = 0) -> None:
    """Turn net-snmp's diagnostics into the right exception type.

    The distinction that matters is auth-failure versus timeout. On an auth
    failure the next credential set is worth trying; on a timeout the host is
    not answering at all and trying five more credential sets just multiplies
    the wait by five.
    """
    if text.lstrip().startswith("USAGE:") or "\nUSAGE:" in text:
        # Our own command line is wrong. Without this the empty stdout looks
        # like a device that answered with nothing, and the scan reports a
        # credential problem on every host in the fleet.
        first_line = next((l for l in text.splitlines() if l.strip()), "")
        raise SnmpInvocationError(f"net-snmp rejected our arguments: {first_line}")
    for pattern in _AUTH_FAILURE_PATTERNS:
        if pattern in text:
            raise SnmpAuthError(f"{host}: {pattern}")
    for pattern in _TIMEOUT_PATTERNS:
        if pattern in text:
            raise SnmpTimeoutError(f"{host}: no response")
    for pattern in _UNREACHABLE_PATTERNS:
        if pattern in text:
            raise SnmpTimeoutError(f"{host}: {pattern}")
    if returncode != 0 and not text.strip():
        # net-snmp exited non-zero and said nothing. That happens when a UDP
        # port is closed and the ICMP unreachable comes back before any retry —
        # common on loopback and on well-firewalled hosts. Without this the
        # caller sees an empty walk and blames the credentials, then patiently
        # tries every remaining credential set against a host that is not there.
        raise SnmpTimeoutError(f"{host}: no response (net-snmp exited {returncode})")


# `.1.3.6.1.2.1.1.1.0 = STRING: "Linux host"` — the value may then continue on
# following lines, because plenty of devices put newlines in sysDescr.
_LINE = re.compile(r"^(\.?[\d.]+)\s+=\s+(?:([A-Za-z0-9-]+):\s?)?(.*)$")
_HEX_BYTE = re.compile(r"^[0-9A-Fa-f]{2}$")


def parse_varbinds(text: str) -> list[VarBind]:
    """Parse net-snmp `-On -Oe` output into VarBinds.

    Handles values that span several lines. Cisco's sysDescr is the usual
    culprit — it is a small paragraph with embedded newlines, and a parser that
    assumes one varbind per line silently truncates it and then fails to find a
    version in it.
    """
    binds: list[VarBind] = []
    pending_oid = pending_type = None
    pending_value: list[str] = []

    def flush() -> None:
        if pending_oid is None:
            return
        raw = "\n".join(pending_value)
        binds.append(VarBind(pending_oid, pending_type or "", _clean_value(pending_type or "", raw)))

    for line in text.splitlines():
        if not line.strip():
            continue
        match = _LINE.match(line)
        if match and not _is_empty_subtree_marker(line):
            flush()
            pending_oid = match.group(1).lstrip(".")
            pending_type = match.group(2)
            pending_value = [match.group(3)]
        elif _is_empty_subtree_marker(line):
            # "No Such Object available on this agent at this OID" and friends:
            # the subtree is simply not implemented. Drop the varbind entirely
            # so callers see an absent key rather than a bogus value.
            flush()
            pending_oid = pending_type = None
            pending_value = []
        elif pending_oid is not None:
            pending_value.append(line)
    flush()
    return binds


def _is_empty_subtree_marker(line: str) -> bool:
    return any(pattern in line for pattern in _EMPTY_SUBTREE_PATTERNS)


def _clean_value(value_type: str, raw: str) -> str:
    """Normalise a printed value into something usable.

    Hex-STRINGs become colon-separated uppercase (MAC-shaped), quoted strings
    lose their quotes, Timeticks lose the human-readable tail, and OIDs lose
    their leading dot so they compare equal to the OIDs we build ourselves.
    """
    raw = raw.strip()
    if value_type == "Hex-STRING":
        parts = [p for p in raw.replace("\n", " ").split(" ") if _HEX_BYTE.match(p)]
        return ":".join(p.upper() for p in parts)
    if value_type == "Timeticks":
        # `(123456) 0:20:34.56` -> `123456`
        match = re.match(r"\((\d+)\)", raw)
        if match:
            return match.group(1)
    if value_type == "OID":
        return raw.lstrip(".")
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        return raw[1:-1]
    return raw


def column_index(oid: str, column_oid: str) -> str | None:
    """Return the table index part of `oid` if it sits under `column_oid`.

    Table rows are identified by whatever follows the column OID, which for
    most tables is a single integer but for ipAddressTable and the Aruba AP
    table is a multi-part index that we decode separately.
    """
    prefix = column_oid.rstrip(".") + "."
    if not oid.startswith(prefix):
        return None
    return oid[len(prefix):]


def collect_column(binds: list[VarBind], column_oid: str) -> dict[str, VarBind]:
    """Pick one column out of a walked table, keyed by row index."""
    out: dict[str, VarBind] = {}
    for bind in binds:
        index = column_index(bind.oid, column_oid)
        if index is not None:
            out[index] = bind
    return out
