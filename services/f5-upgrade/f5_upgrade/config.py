import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field

from .models import Model, Name
from .snmp_models import SnmpProfile


class Device(Model):
    id: Name
    host: str = Field(pattern=r"^[A-Za-z0-9:._-]+$", max_length=253)
    port: int = Field(default=22, ge=1, le=65535)
    username: str = Field(default="root", pattern=r"^[a-zA-Z0-9_.-]+$")
    key_file: str | None = None
    password_env: str | None = None
    key_passphrase_env: str | None = None
    ucs_passphrase_env: str = "F5_UCS_PASSPHRASE"
    lock_group: Name | None = None


@dataclass
class Settings:
    token: str
    data_dir: Path
    image_root: Path
    backup_root: Path
    known_hosts: Path
    devices: dict[str, Device]
    stale_seconds: int = 120
    snmp_profiles: dict[str, SnmpProfile] = field(default_factory=dict)

    @classmethod
    def load(cls):
        load_dotenv(os.getenv("F5_ENV_FILE", ".env"))
        token = os.environ.get("F5_API_TOKEN", "")
        if len(token) < 32:
            raise ValueError("F5_API_TOKEN must contain at least 32 characters")
        inventory = json.loads(Path(os.getenv("F5_DEVICES_FILE", "devices.json")).read_text())
        devices = [Device.model_validate(d) for d in inventory]
        if len({d.id for d in devices}) != len(devices):
            raise ValueError("duplicate device IDs")
        endpoints = {(d.host.lower().rstrip("."), d.port) for d in devices}
        if len(endpoints) != len(devices):
            raise ValueError("use one inventory ID per device endpoint")
        result = cls(
            token,
            Path(os.getenv("F5_DATA_DIR", "data")),
            Path(os.getenv("F5_IMAGE_ROOT", "images")),
            Path(os.getenv("F5_BACKUP_ROOT", "backups")),
            Path(os.getenv("F5_KNOWN_HOSTS", "known_hosts")),
            {d.id: d for d in devices},
        )
        profile_file = os.getenv("F5_SNMP_PROFILES_FILE")
        if profile_file:
            profiles = [SnmpProfile.model_validate(p) for p in json.loads(Path(profile_file).read_text())]
            if len({p.id for p in profiles}) != len(profiles):
                raise ValueError("duplicate SNMP profile IDs")
            result.snmp_profiles = {p.id: p for p in profiles}
        result.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        result.backup_root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return result


def under(root: Path, relative: str) -> Path:
    root = root.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError("path must remain under its configured root")
    return path
