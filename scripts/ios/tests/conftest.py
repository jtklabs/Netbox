"""Shared fixtures.

The standards here are the same five `manage.py create_config_standards` seeds,
written out in the shape the REST API returns them. Keeping a copy is a
deliberate duplication: these tests must be runnable on a laptop with no NetBox
anywhere, and test_standards_library.py asserts the two stay identical, so the
copy cannot drift without a test failing.
"""

from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))          # scripts/ios
sys.path.insert(0, os.path.join(HERE, 'emulator'))

FIXTURES = os.path.join(HERE, 'fixtures')


def read_fixture(name):
    with open(os.path.join(FIXTURES, name)) as handle:
        return handle.read()


@pytest.fixture
def dirty_config():
    """A switch with every one of the five standards broken."""
    return read_fixture('dirty-c9300.cfg')


@pytest.fixture
def clean_config():
    """A switch that passes all five."""
    return read_fixture('clean-c9300.cfg')


def standard(name, check_type, match_pattern, **overrides):
    base = {
        'name': name,
        'check_type': check_type,
        'match_pattern': match_pattern,
        'entries': [],
        'add_template': '',
        'remove_template': '',
        'auto_remediable': True,
        'allow_enforce': False,
        'remediation_notes': '',
    }
    base.update(overrides)
    return base


NO_HTTP = standard(
    'No HTTP server', 'absent', r'^ip http server\s*$', remove_template='no {line}',
)
NO_HTTPS = standard(
    'No HTTPS server', 'absent', r'^ip http secure-server\s*$', remove_template='no {line}',
)
PASSWORD_ENCRYPTION = standard(
    'Password encryption service', 'present', r'^service password-encryption\s*$',
    entries=[{'key': 'service password-encryption', 'vars': {}}],
    add_template='{key}',
)
NO_TYPE_7 = standard(
    'No type-7 passwords', 'absent',
    r'(?:^|\s)(?:password|key|key-string)\s+7\s+[0-9A-Fa-f]{4,}\b',
    auto_remediable=False,
    remediation_notes='Audit only, deliberately. Fix each one by hand.',
)
LOCAL_USERS = standard(
    'Local users', 'exact-set',
    r'^username (?P<key>\S+)(?:\s+privilege\s+(?P<privilege>\d+))?',
    entries=[{'key': 'netops', 'vars': {'privilege': '15'}}],
    add_template='username {key} privilege {privilege} secret {secret}',
    remove_template='no username {key}',
)

ALL_STANDARDS = [NO_HTTP, NO_HTTPS, PASSWORD_ENCRYPTION, NO_TYPE_7, LOCAL_USERS]


@pytest.fixture
def standards():
    return [dict(item) for item in ALL_STANDARDS]
