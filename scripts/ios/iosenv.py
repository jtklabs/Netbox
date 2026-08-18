"""Settings and credentials, loaded the same way scripts/f5 loads them.

Same shape on purpose: a `.env` beside the script, real environment variables
winning over it so a CI job or a vault wrapper never has to write a secret to
disk, and a warning if the file is readable by anyone else.

The one addition is remediation secrets. A standard that says "these local
accounts should exist" cannot store their passwords in NetBox, so the password
is a runtime variable the checker supplies at the moment of the write:

    IOS_ACCOUNT_SECRET=...        used for every account the standard adds
    IOS_SECRET_NETOPS=...         used for `netops` specifically, beating the above

If a required runtime variable is not set, the addition is refused and reported
— never rendered half-way and sent.
"""

from __future__ import annotations

import os
import stat
import sys
from dataclasses import dataclass, field

__all__ = ('Settings', 'load_settings', 'load_env_file', 'secrets_from_environment')

DEFAULT_ENV_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
TRUE_VALUES = ('1', 'true', 'yes', 'on')

# Environment prefix for a per-account secret: IOS_SECRET_NETOPS -> netops.
SECRET_PREFIX = 'IOS_SECRET_'
DEFAULT_SECRET = 'IOS_ACCOUNT_SECRET'


@dataclass
class Settings:
    netbox_url: str
    netbox_token: str
    netbox_verify_ssl: bool = True
    netbox_timeout: int = 30

    username: str = ''
    password: str = ''
    enable_secret: str = ''
    port: int = 22
    timeout: int = 30
    workers: int = 5
    device_type: str = 'cisco_ios'

    secrets: dict = field(default_factory=dict)


def load_env_file(path, required=True):
    """Read KEY=value pairs from a .env file into a dict.

    Deliberately narrow so there is no dependency to install: comments, blank
    lines, an optional `export ` prefix, and optional surrounding quotes. An
    unquoted value runs to the end of the line — no inline-comment stripping —
    so a password may contain '#' without being quoted.
    """
    if not os.path.isfile(path):
        if not required:
            return {}
        template = os.path.join(os.path.dirname(path) or '.', '.env.example')
        sys.exit('env file not found: %s\n'
                 '  copy the template and fill it in:\n'
                 '  cp %s %s && chmod 600 %s' % (path, template, path, path))
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRGRP | stat.S_IROTH):
        print('warning: %s holds credentials and is readable by other users '
              '— chmod 600 %s' % (path, path), file=sys.stderr)

    values = {}
    with open(path) as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith('#'):
                continue
            if line.startswith('export '):
                line = line[len('export '):].strip()
            key, sep, value = line.partition('=')
            if not sep:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
                value = value[1:-1]
            values[key.strip()] = value
    return values


def secrets_from_environment(values):
    """Collect remediation secrets into the {account: {var: value}} shape.

    '*' is the fleet-wide default and any per-account entry beats it. Nothing
    here is ever printed or logged: the plan carries a redacted `display`
    alongside the text that actually gets sent, and only the SSH write path
    touches the latter.
    """
    secrets = {}
    default = values.get(DEFAULT_SECRET)
    if default:
        secrets['*'] = {'secret': default}
    for key, value in values.items():
        if key.startswith(SECRET_PREFIX) and value:
            account = key[len(SECRET_PREFIX):].lower()
            secrets.setdefault(account, {})['secret'] = value
    return secrets


def load_settings(env_file=None, require_device_credentials=True):
    """Build Settings from a .env file, with the real environment taking precedence."""
    path = env_file or DEFAULT_ENV_FILE
    values = load_env_file(path, required=not _environment_is_complete())
    merged = dict(values)
    merged.update({k: v for k, v in os.environ.items() if v})

    def get(key, default=''):
        return os.environ.get(key) or values.get(key) or default

    def get_int(key, default):
        raw = get(key, str(default))
        try:
            return int(raw)
        except ValueError:
            sys.exit('%s must be a whole number, got: %r' % (key, raw))

    required = ['NETBOX_URL', 'NETBOX_TOKEN']
    if require_device_credentials:
        required += ['IOS_USERNAME', 'IOS_PASSWORD']
    missing = [key for key in required if not get(key)]
    if missing:
        sys.exit('%s is missing %s (see .env.example)' % (path, ' and '.join(missing)))

    return Settings(
        netbox_url=get('NETBOX_URL'),
        netbox_token=get('NETBOX_TOKEN'),
        netbox_verify_ssl=get('NETBOX_VERIFY_SSL', 'true').lower() in TRUE_VALUES,
        netbox_timeout=get_int('NETBOX_TIMEOUT', 30),
        username=get('IOS_USERNAME'),
        password=get('IOS_PASSWORD'),
        # Empty is normal and correct on a box where the login account already
        # lands in privileged exec; netmiko only sends `enable` when it needs to.
        enable_secret=get('IOS_ENABLE_SECRET', ''),
        port=get_int('IOS_PORT', 22),
        timeout=get_int('IOS_TIMEOUT', 30),
        workers=get_int('IOS_WORKERS', 5),
        device_type=get('IOS_DEVICE_TYPE', 'cisco_ios'),
        secrets=secrets_from_environment(merged),
    )


def _environment_is_complete():
    """True when the shell already supplies everything, so no .env is needed."""
    return all(os.environ.get(key) for key in
               ('NETBOX_URL', 'NETBOX_TOKEN', 'IOS_USERNAME', 'IOS_PASSWORD'))
