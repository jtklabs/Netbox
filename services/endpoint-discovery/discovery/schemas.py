import ipaddress
import re
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


def mac_address(value: str) -> str:
    compact = re.sub(r"[:.\-]", "", value).lower()
    if not re.fullmatch(r"[0-9a-f]{12}", compact):
        raise ValueError("expected a 48-bit MAC address")
    if int(compact[:2], 16) & 1 or int(compact, 16) == 0:
        raise ValueError("expected a unicast, nonzero MAC address")
    return ":".join(compact[i:i + 2] for i in range(0, 12, 2))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Context(StrictModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[\w.-]+$")
    snmp_context: str = Field(default="", max_length=120, pattern=r"^[^\r\n]*$")
    vrf: str = Field(default="default", min_length=1, max_length=120)
    vlan: int = Field(default=0, ge=0, le=4094)
    tables: list[Literal["fdb", "neighbors"]] = Field(default_factory=lambda: ["fdb", "neighbors"], min_length=1, max_length=2)

    @field_validator("tables")
    @classmethod
    def unique_tables(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("table kinds must be unique")
        return value


class Target(StrictModel):
    key: str = Field(min_length=1, max_length=120, pattern=r"^[\w.-]+$")
    scope: str = Field(min_length=1, max_length=120)
    poller: str = Field(min_length=1, max_length=120)
    address: str
    snmp_port: int = Field(default=161, ge=1, le=65535)
    name: str = Field(min_length=1, max_length=255)
    revision: str = Field(default="", pattern=r"^(|[0-9a-f]{64})$")
    credential: str = Field(default="default", min_length=1, max_length=120)
    netbox_device_id: int | None = Field(default=None, gt=0)
    interface_ids: dict[str, int] = Field(default_factory=dict)
    interface_roles: dict[str, Literal["access", "transit", "unknown"]] = Field(default_factory=dict)
    contexts: list[Context] = Field(default_factory=lambda: [Context(key="default")], min_length=1, max_length=256)

    @field_validator("address")
    @classmethod
    def address_literal(cls, value):
        address = ipaddress.ip_address(value)
        if address.is_multicast or address.is_unspecified or "%" in value:
            raise ValueError("target must be a unicast IP literal")
        return str(address)

    @model_validator(mode="after")
    def unique_contexts(self):
        if len({c.key for c in self.contexts}) != len(self.contexts):
            raise ValueError("context keys must be unique")
        if any(v <= 0 for v in self.interface_ids.values()):
            raise ValueError("interface IDs must be positive")
        return self


class Entry(StrictModel):
    mac: str
    ip: str = ""
    hostname: str = Field(default="", max_length=255)
    vlan: int = Field(default=0, ge=0, le=4094)
    ifindex: int = Field(gt=0)
    interface: str = Field(min_length=1, max_length=255)
    port_role: Literal["access", "transit", "unknown"] = "unknown"

    @field_validator("mac")
    @classmethod
    def normalize_mac(cls, value):
        return mac_address(value)

    @field_validator("ip")
    @classmethod
    def normalize_ip(cls, value):
        if not value:
            return value
        address = ipaddress.ip_address(value)
        if address.is_multicast or address.is_unspecified or "%" in value:
            raise ValueError("expected a unicast IP")
        return str(address)


class Table(StrictModel):
    kind: Literal["fdb", "neighbors"]
    status: Literal["complete", "error", "unsupported"]
    error: str = Field(default="", max_length=255)
    entries: list[Entry] = Field(default_factory=list, max_length=50000)

    @model_validator(mode="after")
    def valid_entries(self):
        if self.status != "complete" and self.entries:
            raise ValueError("incomplete tables cannot submit observations")
        for entry in self.entries:
            if (self.kind == "neighbors") != bool(entry.ip):
                raise ValueError("only neighbor entries must have an IP")
        return self


class Report(StrictModel):
    report_id: str
    device_key: str = Field(min_length=1, max_length=120)
    target_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    context: str = Field(min_length=1, max_length=120)
    observed_at: int = Field(gt=0)
    tables: list[Table] = Field(min_length=1, max_length=2)

    @field_validator("report_id")
    @classmethod
    def uuid_id(cls, value):
        return str(UUID(value))

    @model_validator(mode="after")
    def unique_tables(self):
        if len({t.kind for t in self.tables}) != len(self.tables):
            raise ValueError("table kinds must be unique")
        return self
