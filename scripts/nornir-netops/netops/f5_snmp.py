"""BIG-IP SNMP agent access and v3 users, using the shared SNMP standard."""

from __future__ import annotations

import ipaddress
import json
import re
from urllib.parse import quote, urlsplit

from . import debuglog, f5_waf
from .core import MODE_REPLACE, REDACTED, scrub

SNMP = "/mgmt/tm/sys/snmp"
USERS = SNMP + "/users"
COMMUNITIES = SNMP + "/communities"
PASSWORDS = ("authPassword", "privacyPassword", "authPasswordEncrypted",
             "privacyPasswordEncrypted", "communityName")
LEVELS = {"noauth": "no-auth-no-privacy", "auth": "auth-no-privacy", "priv": "auth-privacy"}


def network(value):
    # BIG-IP's factory entry '127.' is a hosts.allow IPv4 prefix.
    text = str(value)
    if re.fullmatch(r"(?:\d{1,3}\.){1,3}", text):
        parts = text.rstrip(".").split(".")
        text = ".".join(parts + ["0"] * (4 - len(parts))) + f"/{8 * len(parts)}"
    return str(ipaddress.ip_network(text, strict=False))


def oid(value):
    text = str(value)
    if text == "iso":
        text = "1"
    text = text.lstrip(".")
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)*", text):
        raise ValueError("F5 SNMP views require a numeric OID or iso")
    return "." + text


def desired_state(variables):
    entries = variables["entries"]
    raw_allow = variables.get("f5_allow")
    if raw_allow is None:
        raise ValueError("F5 SNMP requires snmp.allow in the standards file")
    allow = list(dict.fromkeys(network(value) for value in raw_allow))
    if not variables.get("f5_no_localhost") and "127.0.0.0/8" not in allow:
        allow.insert(0, "127.0.0.0/8")
    root = {"allowedAddresses": allow}
    if variables.get("forbid_communities"):
        root.update(snmpv1="disabled", snmpv2c="disabled")
    users = {}
    for entry in entries.values():
        kind = entry["kind"]
        if kind in ("location", "contact"):
            root["sysLocation" if kind == "location" else "sysContact"] = entry["value"]
        if kind != "user":
            continue
        name = entry["name"]
        group = entries.get("group:" + entry["group"])
        if not group or not group.get("read"):
            raise ValueError(f"F5 SNMP user {name}: define its group and read view")
        read = entries.get("view:" + group["read"])
        write = entries.get("view:" + group["write"]) if group.get("write") else None
        if not read or read["action"] != "included":
            raise ValueError(f"F5 SNMP user {name}: requires one included read view")
        if group.get("write") and (not write or write["action"] != "included"
                                   or oid(write["oid"]) != oid(read["oid"])):
            raise ValueError(f"F5 SNMP user {name}: read and write OID subsets must match")
        auth = entry.get("auth") or "none"
        privacy = entry.get("priv") or "none"
        privacy = {"aes128": "aes", "aes192": "aes-192", "aes256": "aes-256"}.get(privacy, privacy)
        if auth not in ("none", "md5", "sha", "sha256", "sha512"):
            raise ValueError(f"F5 SNMP user {name}: unsupported authentication protocol {auth}")
        if privacy not in ("none", "des", "aes", "aes-192", "aes-256"):
            raise ValueError(f"F5 SNMP user {name}: unsupported privacy protocol {privacy}")
        level = group["security"]
        if (auth != "none") != (level != "noauth") or (privacy != "none") != (level == "priv"):
            raise ValueError(f"F5 SNMP user {name}: protocols must match group security {level}")
        user = {"username": name, "access": "rw" if write else "ro",
                "oidSubset": oid(read["oid"]), "securityLevel": LEVELS[level],
                "authProtocol": auth, "privacyProtocol": privacy}
        for key, api in (("auth", "authPassword"), ("priv", "privacyPassword")):
            if entry.get("passphrases", {}).get(key):
                user[api] = entry["passphrases"][key]
        users[name] = user
    return root, users


def path_at(link, collection):
    parsed = urlsplit(link)
    if parsed.path != collection and not parsed.path.startswith(collection + "/"):
        raise ValueError(f"unexpected F5 SNMP collection path: {parsed.path}")
    return parsed.path + ("?" + parsed.query if parsed.query else "")


def resource(item, collection):
    if item.get("selfLink"):
        path = path_at(item["selfLink"], collection).split("?", 1)[0]
        if path == collection:
            raise ValueError("F5 SNMP resource link points to a collection")
        return path
    name = item.get("fullPath") or item.get("name")
    if not name:
        raise ValueError("F5 SNMP resource has no name")
    if not str(name).startswith("/") and item.get("partition"):
        name = "/" + item["partition"] + "/" + str(name)
    return collection + "/" + quote(str(name).replace("/", "~"), safe="~")


def collection(client, path, secrets):
    base, seen, rows = path, set(), []
    while path:
        path = path_at(path, base)
        if path in seen:
            raise ValueError("repeated F5 SNMP collection page")
        seen.add(path)
        page = client.get_json(path)
        items = page.get("items", [])
        if not isinstance(items, list) or any(not isinstance(item, dict) for item in items):
            raise ValueError("invalid F5 SNMP collection response")
        for item in items:
            secrets.extend(str(item[key]) for key in PASSWORDS if item.get(key))
            resource(item, base)  # Validate all identities before any writes.
        debuglog.protect(secrets)
        rows.extend(items)
        path = page.get("nextLink")
    return rows


def metadata(user):
    result = {key: user.get(key) for key in
              ("username", "access", "oidSubset", "securityLevel", "authProtocol", "privacyProtocol")}
    result["access"] = {"read-only": "ro", "read-write": "rw"}.get(result["access"], result["access"])
    if result["oidSubset"]:
        result["oidSubset"] = oid(result["oidSubset"])
    return result


def plan(client, root, users, clean, forbid, rewrite, secrets):
    current = client.get_json(SNMP)
    if not isinstance(current, dict) or "items" in current:
        raise ValueError("invalid F5 SNMP agent response")
    addresses = current.get("allowedAddresses")
    if not isinstance(addresses, list) or any(not isinstance(v, str) for v in addresses):
        raise ValueError("F5 SNMP agent response lacks an allowedAddresses list")
    existing = collection(client, USERS, secrets)
    communities = collection(client, COMMUNITIES, secrets) if forbid else []
    by_user = {}
    for item in existing:
        name = item.get("username")
        if not name or name in by_user:
            raise ValueError("F5 SNMP users have missing or duplicate usernames")
        by_user[name] = item
    operations, add, remove, extra = [], [], [], []
    patch = {}
    wanted_allow = root["allowedAddresses"]
    matched, kept = set(), []
    for address in addresses:
        try:
            key = network(address)
        except ValueError:
            key = None  # Existing DNS/wildcard entries remain extras, never implicit matches.
        if key in wanted_allow and key not in matched:
            kept.append(address)
            matched.add(key)
        else:
            extra.append("allow:" + address)
    missing = [value for value in wanted_allow if value not in matched]
    add.extend("allow:" + value for value in missing)
    if missing or (clean and extra):
        patch["allowedAddresses"] = (kept if clean else addresses) + missing
    if clean:
        remove.extend(extra)
    for key, value in root.items():
        if key != "allowedAddresses" and current.get(key) != value:
            patch[key] = value
            add.append(f"{key}={value}")
    # Disable community protocols / restrict access before adding users.
    if patch:
        operations.append(("PATCH", SNMP, patch))
    for name, wanted in users.items():
        before = by_user.get(name)
        if before is None:
            operations.append(("POST", USERS, {"name": name, **wanted}))
            add.append("user:" + name)
        elif rewrite or metadata(before) != metadata(wanted):
            operations.append(("PATCH", resource(before, USERS), wanted))
            add.append("user:" + name)
    for name, before in by_user.items():
        if name not in users:
            extra.append("user:" + name)
            if clean:
                operations.append(("DELETE", resource(before, USERS), None))
                remove.append("user:" + name)
    for item in communities:
        operations.append(("DELETE", resource(item, COMMUNITIES), None))
        remove.append("community:" + str(item["name"]))
    state = {key: current.get(key) for key in root}
    state["users"] = [metadata(item) for item in existing]
    return operations, add, remove, extra, state


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result

    payload = {"platform": "f5_tmsh", "mode": mode, "current": [], "desired": [],
               "add": [], "remove": [], "commands": [], "save_command": None,
               "compliant": False, "advisories": [], "notes": [], "rollback": [],
               "rollback_unsupported": [], "applied": False, "saved": None,
               "skipped": False, "skip_reason": None, "verified": None,
               "missing_after": [], "output": None, "save_output": None}
    secrets = [value for entry in variables["entries"].values()
               for value in entry.get("passphrases", {}).values()]
    attempted = False
    try:
        root, users = desired_state(variables)
        payload["desired"] = [f"{key}={value}" for key, value in root.items()] + ["user:" + name for name in users]
        payload["notes"].append("F5 uses snmp.allow as the global client allow list; Cisco group/user ACL names do not bind on F5")
        payload["notes"].append("Password values cannot be compared; --rewrite-users pushes current passphrases. Verification checks readable configuration, not an SNMP login")
        if any(entry["kind"] in ("host", "chassis-id") for entry in variables["entries"].values()):
            payload["notes"].append("F5 SNMP manages polling access and users; trap destinations and chassis-id are unchanged")
        if not variables.get("f5_no_localhost"):
            payload["notes"].append("127.0.0.0/8 is included for local monitoring; --f5-no-localhost omits it")
        if not task.host.username or not task.host.password:
            raise ValueError("F5 REST requires a username and password")
        clean = mode == MODE_REPLACE
        forbid = variables.get("forbid_communities", False)
        with f5_waf.Client(task.host, **variables["f5"]) as client:
            operations, add, remove, extra, state = plan(client, root, users, clean, forbid,
                                                       variables.get("rewrite_users", False), secrets)
            payload.update(current=[json.dumps(state, sort_keys=True)], add=add, remove=remove,
                           extra=extra, compliant=not operations)
            payload["commands"] = [method + " " + path + (" " + json.dumps({
                key: REDACTED if key in PASSWORDS else value for key, value in body.items()}) if body else "")
                for method, path, body in operations]
            if operations:
                payload["rollback_unsupported"] = ["F5 SNMP REST rollback is manual; deleted communities and previous passphrases are not recorded"]
                payload["notes"].extend(payload["rollback_unsupported"])
                if save:
                    payload["save_command"] = 'POST /mgmt/tm/sys/config {"command": "save"}'
            if operations and not dry_run:
                for method, path, body in operations:
                    attempted = True
                    client.request(method, path, body)
                payload["applied"] = True
                if verify:
                    remaining, _, _, _, after = plan(client, root, users, clean, forbid, False, secrets)
                    payload["after"] = [json.dumps(after, sort_keys=True)]
                    payload["verified"] = not remaining
                    payload["missing_after"] = [method + " " + path for method, path, _ in remaining]
                if save:
                    payload["saved"] = False
                    if payload["verified"] is not False:
                        client.save_config()
                        payload["saved"] = True
        payload = json.loads(scrub(json.dumps(payload), secrets))
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        payload = json.loads(scrub(json.dumps(payload), secrets))
        return Result(host=task.host, result=payload, changed=attempted, failed=True,
                      exception=RuntimeError(scrub(str(exc), secrets)))
