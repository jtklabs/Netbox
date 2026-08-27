"""CSV -> Nornir inventory.

The CSV needs one column with an address; everything else is optional:

    host,name,platform,port,username,password,site,role
    10.1.1.1,core-sw1,cisco_ios,,,,atl,core
    10.1.1.2,,,,,,atl,access

* ``platform`` may be left blank -- it is autodetected over SSH at run time.
* per-row ``username``/``password`` override the global credentials, which is
  how you cover the handful of boxes that are not on TACACS yet.
* any column this module does not recognise becomes host data, so
  ``--filter site=atl`` works without touching code.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any, Dict, List, Optional

from nornir.core import Nornir
from nornir.core.inventory import (
    ConnectionOptions,
    Defaults,
    Groups,
    Host,
    Hosts,
    Inventory,
)
from nornir.core.plugins.inventory import InventoryPluginRegister

from .core import canonical_platform

#: Column names accepted for the management address, in priority order.
ADDRESS_COLUMNS = ("host", "hostname", "ip", "ip_address", "address", "mgmt_ip")
#: Columns with a defined meaning; everything else lands in host.data.
RESERVED_COLUMNS = set(ADDRESS_COLUMNS) | {
    "name",
    "platform",
    "port",
    "username",
    "password",
    "secret",
}


class InventoryError(Exception):
    """The CSV cannot be turned into a usable inventory."""


def _clean(row: Dict[str, Any]) -> Dict[str, str]:
    """Lowercase the headers and strip the values; drop the empty ones."""
    cleaned = {}
    for key, value in row.items():
        if key is None:
            continue  # extra columns beyond the header row
        if isinstance(value, list):  # csv puts overflow fields in a list
            value = ",".join(str(v) for v in value)
        text = str(value).strip() if value is not None else ""
        if text:
            cleaned[key.strip().lower().lstrip("﻿")] = text
    return cleaned


class CSVInventory:
    """Nornir inventory plugin backed by a flat CSV file."""

    def __init__(
        self,
        csv_file: str = "inventory/hosts.csv",
        username: Optional[str] = None,
        password: Optional[str] = None,
        secret: Optional[str] = None,
        key_file: Optional[str] = None,
        conn_timeout: Optional[float] = None,
        port: int = 22,
        platform: Optional[str] = None,
    ) -> None:
        self.csv_file = Path(csv_file)
        self.username = username
        self.password = password
        self.secret = secret
        self.key_file = key_file
        self.conn_timeout = conn_timeout
        self.port = port
        self.platform = canonical_platform(platform) or None

    def load(self) -> Inventory:
        if not self.csv_file.exists():
            raise InventoryError(f"inventory file not found: {self.csv_file}")

        # Built first: nornir does not push defaults down into hosts by itself,
        # so every Host below is handed this object explicitly.
        default_extras: Dict[str, Any] = {}
        if self.secret:
            default_extras["secret"] = self.secret
        if self.key_file:
            default_extras["use_keys"] = True
            default_extras["key_file"] = self.key_file
        if self.conn_timeout:
            # Caps how long a worker sits on an unreachable device before the
            # next one gets the slot.
            default_extras["conn_timeout"] = self.conn_timeout
        defaults = Defaults(
            username=self.username,
            password=self.password,
            port=self.port,
            platform=self.platform,
            connection_options={"netmiko": ConnectionOptions(extras=dict(default_extras))},
        )

        hosts = Hosts()
        seen: Dict[str, str] = {}
        with self.csv_file.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            if not reader.fieldnames:
                raise InventoryError(f"{self.csv_file} has no header row")
            headers = {h.strip().lower() for h in reader.fieldnames if h}
            if not headers & set(ADDRESS_COLUMNS):
                raise InventoryError(
                    f"{self.csv_file} needs one of these columns: "
                    f"{', '.join(ADDRESS_COLUMNS)} (found: {', '.join(sorted(headers))})"
                )

            for number, raw in enumerate(reader, start=2):  # line 1 is the header
                row = _clean(raw)
                address = next((row[c] for c in ADDRESS_COLUMNS if c in row), "")
                if not address:
                    if row:  # data but no address: a mistake worth reporting
                        raise InventoryError(f"{self.csv_file}:{number}: no address column value")
                    continue  # blank line

                name = row.get("name") or address
                if name in seen:
                    raise InventoryError(
                        f"{self.csv_file}:{number}: duplicate device {name!r} "
                        f"(already defined as {seen[name]})"
                    )
                seen[name] = address

                connection_options = {}
                if row.get("secret"):
                    # nornir replaces `extras` rather than merging it, so the
                    # fleet-wide extras have to be folded in here or a per-device
                    # secret would silently drop the key file.
                    connection_options["netmiko"] = ConnectionOptions(
                        extras={**default_extras, "secret": row["secret"]}
                    )

                hosts[name] = Host(
                    name=name,
                    hostname=address,
                    platform=canonical_platform(row.get("platform")) or None,
                    port=int(row["port"]) if row.get("port") else None,
                    username=row.get("username"),
                    password=row.get("password"),
                    data={
                        "source_line": number,
                        **{k: v for k, v in row.items() if k not in RESERVED_COLUMNS},
                    },
                    connection_options=connection_options,
                    defaults=defaults,
                )

        if not hosts:
            raise InventoryError(f"{self.csv_file} contained no devices")

        return Inventory(hosts=hosts, groups=Groups(), defaults=defaults)


InventoryPluginRegister.register("csv", CSVInventory)


def init_nornir(
    csv_file: str,
    username: Optional[str],
    password: Optional[str],
    secret: Optional[str],
    key_file: Optional[str],
    port: int,
    workers: int,
    conn_timeout: Optional[float] = None,
) -> Nornir:
    """Build a Nornir instance from the CSV. Logging to file is off -- this is
    an interactive tool and the report is the output."""
    from nornir import InitNornir

    return InitNornir(
        runner={"plugin": "threaded", "options": {"num_workers": workers}},
        inventory={
            "plugin": "csv",
            "options": {
                "csv_file": csv_file,
                "username": username,
                "password": password,
                "secret": secret,
                "key_file": key_file,
                "conn_timeout": conn_timeout,
                "port": port,
            },
        },
        logging={"enabled": False},
    )


def missing_credentials(nr: Nornir, key_file: Optional[str] = None) -> List[str]:
    """Names of hosts that could not authenticate: no username, or no password
    when key authentication is not in play."""
    return sorted(
        name
        for name, host in nr.inventory.hosts.items()
        if not host.username or (not key_file and not host.password)
    )
