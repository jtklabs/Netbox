"""Pins the two places this design is duplicated, so neither can drift silently.

Some duplication here is unavoidable and deliberate. The plugin runs inside
NetBox; the checker runs on a poller box that has no NetBox on it. They cannot
import each other, so two things exist twice:

  the template substituter   netbox_compliance/models.py and iosconfig.py must
                             agree on what `{key}` means, or a standard that
                             validates in the UI renders differently on a switch.
  the five shipped standards netbox_compliance/standards_library.py is the
                             source; tests/conftest.py holds a copy so these
                             tests need no NetBox.

Both are checked against the real files rather than against a second copy of
the expectation, so editing one and not the other fails here.
"""

from __future__ import annotations

import os
import re
import sys
import types

import pytest
from conftest import ALL_STANDARDS
from iosconfig import render_template

PLUGIN = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    '..', '..', '..', 'plugins', 'netbox-compliance', 'netbox_compliance',
))

pytestmark = pytest.mark.skipif(
    not os.path.isdir(PLUGIN),
    reason='plugin source not present (running from a standalone copy of scripts/ios)',
)


def _source(name):
    with open(os.path.join(PLUGIN, name)) as handle:
        return handle.read()


def _plugin_standards():
    """Load standards_library.py without needing NetBox installed.

    It imports one name from the plugin's choices module, so a stub is enough —
    and stubbing beats copying, because a copy is the thing this test exists to
    catch.
    """
    choices = types.ModuleType('netbox_compliance.choices')

    class ConfigCheckTypeChoices:
        TYPE_ABSENT = 'absent'
        TYPE_PRESENT = 'present'
        TYPE_EXACT_SET = 'exact-set'

    choices.ConfigCheckTypeChoices = ConfigCheckTypeChoices
    package = types.ModuleType('netbox_compliance')
    package.__path__ = [PLUGIN]

    saved = {k: sys.modules.get(k) for k in ('netbox_compliance', 'netbox_compliance.choices')}
    sys.modules['netbox_compliance'] = package
    sys.modules['netbox_compliance.choices'] = choices
    try:
        namespace = {'__name__': 'netbox_compliance.standards_library'}
        exec(compile(_source('standards_library.py'), 'standards_library.py', 'exec'),
             namespace)
        return namespace['DEFAULT_STANDARDS']
    finally:
        for key, value in saved.items():
            if value is None:
                sys.modules.pop(key, None)
            else:
                sys.modules[key] = value


class TestTemplateSubstitution:
    def test_both_sides_use_the_same_pattern(self):
        """The regex literal must be character-for-character the same."""
        plugin = re.search(r"TEMPLATE_VARIABLE = re\.compile\((r'[^']+')",
                           _source('models.py')).group(1)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               '..', 'iosconfig.py')) as handle:
            script = re.search(r"_TEMPLATE_VARIABLE = re\.compile\((r'[^']+')",
                               handle.read()).group(1)
        assert plugin == script

    def test_a_known_value_is_substituted(self):
        text, missing = render_template('username {key} privilege {privilege}',
                                        {'key': 'netops', 'privilege': '15'})
        assert text == 'username netops privilege 15'
        assert missing == []

    def test_an_unknown_value_is_reported_and_left_in_place(self):
        text, missing = render_template('username {key} secret {secret}', {'key': 'a'})
        assert missing == ['secret']
        assert '{secret}' in text        # visibly unfinished, never sent

    def test_it_is_not_str_format(self):
        """`{key.__class__}` must render as literal text, not walk the object."""
        # The pattern only accepts a bare identifier, so this is not a
        # placeholder at all — it is literal text, and not a variable anybody
        # is being told is missing.
        text, missing = render_template('{key.__class__}', {'key': 'x'})
        assert text == '{key.__class__}'
        assert missing == []

    def test_braces_in_a_config_line_survive(self):
        text, _missing = render_template('banner motd ^C {not a var} ^C', {})
        assert '{not a var}' in text


class TestShippedStandards:
    def test_the_test_copies_match_the_plugin(self):
        plugin = {s['name']: s for s in _plugin_standards()}
        local = {s['name']: s for s in ALL_STANDARDS}
        assert set(plugin) == set(local)

        for name, expected in plugin.items():
            actual = local[name]
            for field in ('check_type', 'match_pattern', 'auto_remediable'):
                assert actual[field] == expected[field], '%s.%s' % (name, field)
            for field in ('add_template', 'remove_template'):
                assert actual.get(field, '') == expected.get(field, ''), \
                    '%s.%s' % (name, field)
            assert actual['entries'] == expected['expected_entries'], '%s entries' % name

    def test_every_shipped_pattern_compiles(self):
        for standard in _plugin_standards():
            re.compile(standard['match_pattern'])

    def test_the_exact_set_standard_captures_an_identity(self):
        local_users = next(s for s in _plugin_standards() if s['name'] == 'Local users')
        assert 'key' in re.compile(local_users['match_pattern']).groupindex

    def test_the_type_7_standard_ships_audit_only_with_no_templates(self):
        type7 = next(s for s in _plugin_standards() if s['name'] == 'No type-7 passwords')
        assert type7['auto_remediable'] is False
        assert not type7.get('add_template')
        assert not type7.get('remove_template')

    def test_nothing_ships_with_enforce_enabled(self):
        for standard in _plugin_standards():
            assert not standard.get('allow_enforce'), standard['name']
