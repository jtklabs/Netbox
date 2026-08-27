"""Where the login comes from.

Three sources, checked per field in this order:

1. an explicit CLI flag (``--username`` / ``--password`` / ``--secret``)
2. AWS Secrets Manager, when a secret name is configured -- resolved with the
   default boto3 chain, so an IAM role on the instance needs no keys anywhere
3. environment variables, which include anything loaded from a ``.env`` file

A password prompt is the last resort, and only on a terminal.

Per-device ``username``/``password`` columns in the CSV still win over all of
this for that device; this module supplies the fleet-wide default.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

#: Environment variables consulted for each field.
ENV_USERNAME = "NET_USER"
ENV_PASSWORD = "NET_PASS"
ENV_ENABLE = "NET_ENABLE"


class CredentialError(Exception):
    """Credentials are missing, unreadable, or shaped unexpectedly."""


# --------------------------------------------------------------------------- #
# .env
# --------------------------------------------------------------------------- #


def find_env_file(explicit: Optional[str], project_root: Path) -> Optional[Path]:
    """Locate the .env: the one asked for, else ./.env, else <project>/.env."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise CredentialError(f"env file not found: {path}")
        return path
    for candidate in (Path.cwd() / ".env", project_root / ".env"):
        if candidate.is_file():
            return candidate
    return None


def parse_env_file(path: Path) -> Dict[str, str]:
    """Read a KEY=VALUE file.

    Deliberately small: comments, blank lines, optional ``export``, and single
    or double quoted values. No interpolation -- a password containing ``$`` is
    far more likely than someone wanting variable expansion in a secrets file.
    """
    values: Dict[str, str] = {}
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, separator, value = line.partition("=")
        if not separator:
            raise CredentialError(f"{path}:{number}: expected KEY=VALUE, got {raw!r}")
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(path: Path) -> int:
    """Merge a .env into os.environ without clobbering what is already set.

    The real environment wins, so a one-off ``NET_PASS=... ./configure.py ...``
    overrides the file rather than being silently ignored.
    """
    loaded = 0
    for key, value in parse_env_file(path).items():
        if key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


# --------------------------------------------------------------------------- #
# AWS Secrets Manager
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class AwsSecretSpec:
    """Which secret to read, and which fields inside it to use."""

    name: str
    region: Optional[str] = None
    username_key: str = "username"
    password_key: str = "password"
    enable_key: str = "enable_secret"


def fetch_json_secret(name: str, region: Optional[str] = None) -> Dict[str, str]:
    """Read a JSON object from Secrets Manager with the ambient IAM identity
    (instance/task role, SSO session, profile -- whatever boto3 finds)."""
    try:
        import boto3
        from botocore.exceptions import BotoCoreError, ClientError
    except ImportError as exc:  # pragma: no cover - depends on install extras
        raise CredentialError(
            "AWS Secrets Manager requested but boto3 is not installed "
            "(pip install boto3)"
        ) from exc

    try:
        client = boto3.client("secretsmanager", region_name=region)
        response = client.get_secret_value(SecretId=name)
    except (BotoCoreError, ClientError) as exc:
        raise CredentialError(f"could not read secret {name!r}: {exc}") from exc

    payload = response.get("SecretString")
    if payload is None:
        raise CredentialError(f"secret {name!r} holds binary data; expected a JSON string")
    try:
        document = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CredentialError(
            f"secret {name!r} is not JSON; expected a JSON object"
        ) from exc
    if not isinstance(document, dict):
        raise CredentialError(f"secret {name!r} is not a JSON object")
    return document


def fetch_aws_secret(spec: AwsSecretSpec) -> Dict[str, str]:
    """Read the device login out of a JSON secret, using the configured keys."""
    document = fetch_json_secret(spec.name, spec.region)

    values: Dict[str, str] = {}
    for field, key in (
        ("username", spec.username_key),
        ("password", spec.password_key),
        ("secret", spec.enable_key),
    ):
        if key in document and document[key] is not None:
            values[field] = str(document[key])

    missing = [
        key
        for field, key in (("username", spec.username_key), ("password", spec.password_key))
        if field not in values
    ]
    if missing:
        raise CredentialError(
            f"secret {spec.name!r} has no {', '.join(repr(m) for m in missing)} "
            f"key (found: {', '.join(sorted(document)) or 'nothing'})"
        )
    return values


# --------------------------------------------------------------------------- #
# resolution
# --------------------------------------------------------------------------- #


@dataclass
class Credentials:
    username: Optional[str] = None
    password: Optional[str] = None
    secret: Optional[str] = None
    #: Human-readable provenance, printed so an operator can see which identity
    #: a run used without ever printing the password.
    source: str = "none"

    def describe(self) -> str:
        who = self.username or "(per-device)"
        return f"{who} via {self.source}"


def resolve(
    *,
    username: Optional[str],
    password: Optional[str],
    secret: Optional[str],
    aws: Optional[AwsSecretSpec],
    prompt: bool,
    key_file: Optional[str] = None,
) -> Credentials:
    """Combine the sources into one fleet-wide credential."""
    sources = []
    resolved = Credentials(username=username, password=password, secret=secret)
    if any((username, password, secret)):
        sources.append("command line")

    if aws is not None:
        values = fetch_aws_secret(aws)
        for field in ("username", "password", "secret"):
            if getattr(resolved, field) is None and field in values:
                setattr(resolved, field, values[field])
        sources.append(f"aws secret {aws.name}")

    for field, variable in (
        ("username", ENV_USERNAME),
        ("password", ENV_PASSWORD),
        ("secret", ENV_ENABLE),
    ):
        if getattr(resolved, field) is None and os.environ.get(variable):
            setattr(resolved, field, os.environ[variable])
            if "environment" not in sources:
                sources.append("environment")

    if resolved.password is None and not key_file and resolved.username and prompt:
        import getpass

        resolved.password = getpass.getpass(f"Password for {resolved.username}: ") or None
        if resolved.password:
            sources.append("prompt")

    resolved.source = " + ".join(sources) if sources else "none"
    return resolved
