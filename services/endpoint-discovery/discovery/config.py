"""Service-side authorization, never based on a report's claimed scope."""
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .schemas import StrictModel


class Principal(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    role: Literal["reader", "poller"]
    token_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    scopes: list[str] = Field(min_length=1)

    @field_validator("scopes")
    @classmethod
    def explicit_scopes(cls, value):
        if any(not s.strip() or s == "*" for s in value):
            raise ValueError("explicit nonempty scopes are required")
        return value


class Access(StrictModel):
    principals: list[Principal] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_credentials(self):
        if len({p.name for p in self.principals}) != len(self.principals):
            raise ValueError("principal names must be unique")
        if len({p.token_sha256 for p in self.principals}) != len(self.principals):
            raise ValueError("credentials must be unique")
        return self


@dataclass(frozen=True)
class Settings:
    database_url: str
    access_file: str
    stale_seconds: int = 900
    retention_days: int = 90
    max_report_age_days: int = 7
    max_body_bytes: int = 16 * 1024 * 1024

    @classmethod
    def from_env(cls):
        values = cls(
            database_url=os.environ["DISCOVERY_DATABASE_URL"],
            access_file=os.environ["DISCOVERY_ACCESS_FILE"],
            stale_seconds=int(os.getenv("DISCOVERY_STALE_SECONDS", "900")),
            retention_days=int(os.getenv("DISCOVERY_RETENTION_DAYS", "90")),
        )
        if values.stale_seconds < 1 or values.retention_days < 1:
            raise ValueError("freshness and retention must be positive")
        if not values.database_url.startswith("postgresql+psycopg://"):
            raise ValueError("a dedicated postgresql+psycopg database URL is required")
        return values


def read_access(path: str) -> Access:
    return Access.model_validate(json.loads(Path(path).read_text()))


def digest_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
