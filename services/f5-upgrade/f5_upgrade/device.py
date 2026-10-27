"""F5 commands and strict field-format parsing; no device HTTP calls."""

import re
import shlex
import uuid

from .ssh import DeviceError


def name(value):
    # Device-returned identifiers are untrusted too. Fail on unsupported names instead of interpolating them.
    if not re.fullmatch(r"[A-Za-z0-9_/.:%-]+", value) or value.startswith("-"):
        raise DeviceError("unsupported device object name")
    return value


def blocks(text, prefix):
    lexer = shlex.shlex(text, posix=True, punctuation_chars="{}")
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    # shlex groups adjacent brace punctuation.
    tokens = [part for t in tokens for part in (list(t) if t and set(t) <= {"{", "}"} else [t])]
    expected, result, i = prefix.split(), {}, 0
    while i < len(tokens):
        if tokens[i : i + len(expected)] != expected:
            raise DeviceError("unrecognized tmsh object output")
        i += len(expected)
        if i + 1 >= len(tokens) or tokens[i + 1] != "{":
            raise DeviceError("malformed tmsh object header")
        key, i, depth, body = tokens[i], i + 2, 1, []
        while i < len(tokens) and depth:
            t = tokens[i]
            depth += (t == "{") - (t == "}")
            if depth:
                body.append(t)
            i += 1
        if depth or key in result:
            raise DeviceError("incomplete or duplicate tmsh object")
        result[key] = body
    return result


def field(tokens, key, default=None):
    # Only inspect this object's own properties. Member/profile statistics can
    # occur before parent statistics and must never supply a pool's health.
    depth = 0
    for i, token in enumerate(tokens):
        if token == "{":
            depth += 1
        elif token == "}":
            depth -= 1
        elif depth == 0 and token == key:
            if i + 1 >= len(tokens) or tokens[i + 1] in ("{", "}"):
                raise DeviceError("missing tmsh field value")
            return tokens[i + 1]
    return default


def required(tokens, key):
    value = field(tokens, key)
    if value is None:
        raise DeviceError("required tmsh field missing: " + key)
    return value


def health(text, kind):
    objects = blocks(text, "ltm " + kind)
    records = {}
    for key, body in objects.items():
        availability = field(body, "status.availability-state", field(body, "availability-state"))
        enabled = field(body, "status.enabled-state", field(body, "enabled-state"))
        if availability is None or enabled is None:
            raise DeviceError("missing availability/enabled status for " + kind)
        records[key] = {
            "availability": availability,
            "enabled": enabled,
            "up": availability == "available" and enabled == "enabled",
        }
        if kind == "virtual":
            current = required(body, "clientside.cur-conns")
            if not re.fullmatch(r"\d+", current):
                raise DeviceError("connection count must be an unscaled integer")
            records[key]["connections"] = int(current)
    return {"up": sum(x["up"] for x in records.values()), "total": len(records), "objects": records}


class BigIP:
    def __init__(self, ssh):
        self.ssh = ssh

    def volumes(self):
        objects = blocks(self.ssh.tmsh("list /sys software volume all-properties"), "sys software volume")
        result = {}
        for key, body in objects.items():
            if not re.fullmatch(r"(?:HD|MD)\d+\.\d+", key):
                raise DeviceError("only single-system BIG-IP volumes are supported; chassis output rejected")
            result[key] = {
                "volume": key,
                "version": field(body, "version"),
                "build": field(body, "build"),
                "basebuild": field(body, "basebuild"),
                "status": required(body, "status"),
                "active": required(body, "active") == "yes",
                "active_requested": field(body, "active-requested", "no") == "yes",
            }
        # The status table reports the installed build, including a hotfix. Do not
        # accept a basebuild match as proof that the requested hotfix is installed.
        status = self.ssh.tmsh("show /sys software status")
        seen = set()
        for line in status.splitlines():
            columns = line.split()
            if not columns or columns[0] not in result:
                continue
            key = columns[0]
            if key in seen or len(columns) < 6 or columns[1] != "BIG-IP":
                raise DeviceError("unrecognized software status row")
            seen.add(key)
            if result[key]["status"] == "complete":
                if columns[5] != "complete" or columns[2] != result[key]["version"]:
                    raise DeviceError("software status changed while being read")
                result[key]["build"] = columns[3]
        if seen != set(result):
            raise DeviceError("software status table does not match volume inventory")
        return result

    def verify_image(self, image):
        prefix = "sys software " + image.kind
        properties = "version verified" + (" build" if image.kind == "image" else "")
        records = blocks(self.ssh.tmsh(f"list /{prefix} {name(image.name)} {properties}"), prefix)
        body = records.get(image.name)
        if (
            body is None
            or field(body, "verified") != "yes"
            or field(body, "version") != image.version
            or (image.kind == "image" and field(body, "build") != image.build)
        ):
            raise DeviceError("device image metadata/signature verification does not match manifest")

    def boot_id(self):
        value = self.ssh.shell("cat /proc/sys/kernel/random/boot_id").strip()
        try:
            return str(uuid.UUID(value))
        except ValueError:
            raise DeviceError("could not read boot identity") from None

    def active(self):
        active = [v for v in self.volumes().values() if v["active"]]
        if len(active) != 1 or active[0]["status"] != "complete":
            raise DeviceError("cannot identify a single healthy active volume")
        # Running version/build is authoritative, including hotfix builds.
        raw = self.ssh.tmsh("show /sys version")
        version = re.search(r"(?m)^\s*Version\s+(\S+)\s*$", raw)
        build = re.search(r"(?m)^\s*Build\s+(\S+)\s*$", raw)
        if not version or not build:
            raise DeviceError("cannot parse running version/build")
        return {**active[0], "version": version[1], "build": build[1]}

    def stats(self, kind):
        return health(self.ssh.tmsh(f"cd /; show /ltm {kind} recursive field-fmt raw"), kind)

    def snapshot(self):
        active = self.active()
        categories = {
            label: self.stats(kind)
            for label, kind in (
                ("virtual_servers", "virtual"),
                ("pools", "pool"),
                ("nodes", "node"),
                ("virtual_ips", "virtual-address"),
            )
        }
        provision = blocks(self.ssh.tmsh("list /sys provision asm"), "sys provision")
        if "asm" not in provision:
            raise DeviceError("ASM provisioning status missing")
        level = required(provision["asm"], "level")
        policies = (
            {}
            if level == "none"
            else blocks(self.ssh.tmsh("cd /; list /asm policy recursive one-line"), "asm policy")
        )
        return {
            "active": active,
            "boot_id": self.boot_id(),
            "waf_policies": len(policies),
            "waf_policy_names": sorted(policies),
            "asm_provision": level,
            **categories,
        }

    def connections(self):
        return sum(v["connections"] for v in self.stats("virtual")["objects"].values())

    def check_drain(self):
        groups = blocks(self.ssh.tmsh("list /cm device-group all-properties"), "cm device-group")
        if any(field(g, "auto-sync") == "enabled" for g in groups.values()):
            raise DeviceError("virtual-address drain is not permitted with automatic config sync enabled")

    def address_states(self):
        objects = blocks(
            self.ssh.tmsh("cd /; list /ltm virtual-address recursive all-properties"), "ltm virtual-address"
        )
        result = {}
        for address, body in objects.items():
            state = required(body, "enabled")
            if state not in ("yes", "no"):
                raise DeviceError("unrecognized virtual address enabled state")
            name(address)
            result[address] = state == "yes"
        return result

    def set_enabled(self, address, enabled):
        self.ssh.tmsh(f"modify /ltm virtual-address {name(address)} enabled {'yes' if enabled else 'no'}")

    def reboot(self, volume):
        self.ssh.tmsh("reboot volume " + name(volume), timeout=45)

    def install(self, image, volume, create=False):
        self.ssh.tmsh(
            f"install /sys software {image.kind} {name(image.name)} volume {name(volume)}"
            + (" create-volume" if create else ""),
            timeout=180,
        )


def matches(volume, version, build):
    return (
        volume
        and volume["status"] == "complete"
        and volume["version"] == version
        and volume["build"] == build
    )


def compare(before, after):
    issues = []
    if before["waf_policy_names"] != after["waf_policy_names"]:
        issues.append("WAF policy inventory changed")
    for category in ("virtual_servers", "pools", "nodes", "virtual_ips"):
        a, b = before[category]["objects"], after[category]["objects"]
        if set(a) != set(b):
            issues.append(category + " inventory changed")
        lost = [k for k in a.keys() & b.keys() if a[k]["up"] and not b[k]["up"]]
        if lost:
            issues.append(f"{category}: {len(lost)} previously up objects are no longer up")
    return issues
