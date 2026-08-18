"""End to end against an emulated switch, over a real SSH session.

Everything here goes through netmiko to a paramiko server on loopback. That is
what makes these tests worth having over the unit tests next door: prompt
detection, paging, config mode and `write mem` are the parts that cannot be
proved by calling functions, and they are also the parts that fail in ways a
mock would happily agree with.
"""

import time
from types import SimpleNamespace

import pytest
from conftest import ALL_STANDARDS, LOCAL_USERS, NO_HTTP, PASSWORD_ENCRYPTION, read_fixture
from fake_ios import DEFAULT_PASSWORD, DEFAULT_USERNAME, FakeIosDevice

import ios_standards
from iosconfig import MODE_AUDIT, MODE_ENFORCE, MODE_UPDATE

pytest.importorskip('netmiko')


class FakeNetBox:
    """Stands in for the REST API: serves standards, collects posted results."""

    def __init__(self, standards):
        self.standards = standards
        self.posted = []

    def standards_for_device(self, device_id):
        return [dict(s) for s in self.standards]

    def all(self, path, **params):
        return [dict(s) for s in self.standards]

    def post_results(self, items):
        self.posted.extend(items)
        return {'summary': {'created': len(items)}}


def settings_for(device, secrets=None):
    return SimpleNamespace(
        device_type='cisco_ios', port=device.port,
        username=DEFAULT_USERNAME, password=DEFAULT_PASSWORD,
        enable_secret='', timeout=15, workers=1,
        secrets=secrets or {},
    )


def run(device, standards, mode=MODE_AUDIT, commit=False, secrets=None, only=None):
    target = ios_standards.Target(name='lab-sw-01', address='127.0.0.1', device_id=1)
    args = SimpleNamespace(mode=mode, commit=commit, only=only)
    netbox = FakeNetBox(standards)
    outcome = ios_standards.check_device(settings_for(device, secrets), netbox, target, args)
    return outcome, netbox


@pytest.fixture
def dirty_device():
    with FakeIosDevice(read_fixture('dirty-c9300.cfg'), hostname='lab-sw-01') as device:
        time.sleep(0.2)
        yield device


@pytest.fixture
def clean_device():
    with FakeIosDevice(read_fixture('clean-c9300.cfg'), hostname='lab-sw-02') as device:
        time.sleep(0.2)
        yield device


class TestAudit:
    def test_a_dirty_switch_fails_every_standard(self, dirty_device):
        outcome, _netbox = run(dirty_device, ALL_STANDARDS)
        assert not outcome.failed
        verdicts = {s['name']: e.result for s, e, _p, _c in outcome.results}
        assert verdicts == {
            'No HTTP server': 'non-compliant',
            'No HTTPS server': 'non-compliant',
            'Password encryption service': 'non-compliant',
            'No type-7 passwords': 'non-compliant',
            'Local users': 'non-compliant',
        }

    def test_a_clean_switch_passes_every_standard(self, clean_device):
        outcome, _netbox = run(clean_device, ALL_STANDARDS)
        assert {e.result for _s, e, _p, _c in outcome.results} == {'compliant'}

    def test_audit_sends_no_configuration(self, dirty_device):
        run(dirty_device, ALL_STANDARDS, mode=MODE_AUDIT, commit=True)
        # --commit with no mode is still an audit: nothing may be written.
        assert not any(c.startswith('configure') for c in dirty_device.cli.received)
        assert not dirty_device.cli.saved

    def test_results_posted_to_netbox_carry_no_secrets(self, dirty_device):
        outcome, netbox = run(dirty_device, ALL_STANDARDS)
        ios_standards._post_results(netbox, [outcome])
        blob = repr(netbox.posted)
        for secret in ('070C285F4D06', '0822455D0A16', '121A0C041104',
                       '060506324F41584B56', '$14$'):
            assert secret not in blob
        assert len(netbox.posted) == len(ALL_STANDARDS)

    def test_the_posted_payload_is_the_shape_the_api_expects(self, dirty_device):
        outcome, netbox = run(dirty_device, [NO_HTTP])
        ios_standards._post_results(netbox, [outcome])
        item = netbox.posted[0]
        assert item['device'] == 'lab-sw-01'
        assert item['standard'] == 'No HTTP server'
        assert item['result'] == 'non-compliant'
        assert item['source'] == 'ssh'
        assert item['findings']['violations'][0]['line'] == 'ip http server'


class TestUpdate:
    def test_update_adds_the_missing_service(self, dirty_device):
        outcome, _netbox = run(dirty_device, [PASSWORD_ENCRYPTION],
                               mode=MODE_UPDATE, commit=True)
        assert 'service password-encryption' in dirty_device.cli.config_text()
        # And the recorded verdict is the state AFTER the change, re-read from
        # the device rather than assumed.
        assert outcome.results[0][1].result == 'compliant'
        assert dirty_device.cli.saved

    def test_update_without_commit_writes_nothing(self, dirty_device):
        run(dirty_device, [PASSWORD_ENCRYPTION], mode=MODE_UPDATE, commit=False)
        assert 'service password-encryption' not in dirty_device.cli.config_text()

    def test_update_adds_the_account_with_a_real_secret(self, dirty_device):
        secrets = {'*': {'secret': 'S3cretFromEnv'}}
        run(dirty_device, [LOCAL_USERS], mode=MODE_UPDATE, commit=True, secrets=secrets)
        config = dirty_device.cli.config_text()
        assert 'username netops privilege 15 secret S3cretFromEnv' in config
        # ...and leaves the accounts it was not asked to remove.
        assert 'username olduser' in config

    def test_update_leaves_an_absent_violation_alone(self, dirty_device):
        run(dirty_device, [NO_HTTP], mode=MODE_UPDATE, commit=True)
        assert 'ip http server' in dirty_device.cli.config_text()


class TestEnforce:
    def test_enforce_removes_the_http_server(self, dirty_device):
        outcome, _netbox = run(dirty_device, [dict(NO_HTTP, allow_enforce=True)],
                               mode=MODE_ENFORCE, commit=True)
        lines = dirty_device.cli.config_text().splitlines()
        assert 'ip http server' not in lines
        assert outcome.results[0][1].result == 'compliant'

    def test_enforce_replaces_the_account_set_and_keeps_the_session_user(
        self, dirty_device
    ):
        standard = dict(LOCAL_USERS, allow_enforce=True)
        secrets = {'*': {'secret': 'S3cretFromEnv'}}
        run(dirty_device, [standard], mode=MODE_ENFORCE, commit=True, secrets=secrets)
        config = dirty_device.cli.config_text()
        assert 'username netops privilege 15 secret S3cretFromEnv' in config
        assert 'username olduser' not in config
        assert 'username monitor' not in config
        # DEFAULT_USERNAME is netops, which is also the account the standard
        # wants, so nothing here should have removed it.
        assert 'username netops' in config

    def test_the_session_account_survives_enforce(self):
        # A switch whose only privileged account is the one we are logged in as.
        config = ('hostname lab-sw-09\n'
                  'username netops privilege 15 secret 9 $14$abc\n'
                  'username stale privilege 15 secret 9 $14$def\n'
                  'end\n')
        standard = dict(LOCAL_USERS, allow_enforce=True,
                        entries=[{'key': 'keeper', 'vars': {'privilege': '15'}}])
        with FakeIosDevice(config, hostname='lab-sw-09') as device:
            time.sleep(0.2)
            run(device, [standard], mode=MODE_ENFORCE, commit=True,
                secrets={'*': {'secret': 'x'}})
            after = device.cli.config_text()
        assert 'username netops' in after       # never remove who we are
        assert 'username keeper' in after       # the standard's account was added
        assert 'username stale' not in after    # ...and the surplus one went

    def test_removals_are_abandoned_when_the_addition_cannot_be_built(self):
        config = ('hostname lab-sw-10\n'
                  'username onlyadmin privilege 15 secret 9 $14$abc\n'
                  'end\n')
        standard = dict(LOCAL_USERS, allow_enforce=True)
        with FakeIosDevice(config, hostname='lab-sw-10') as device:
            time.sleep(0.2)
            run(device, [standard], mode=MODE_ENFORCE, commit=True, secrets={})
            after = device.cli.config_text()
        # No secret was supplied, so netops could not be created — and the
        # account that IS there must therefore stay.
        assert 'username onlyadmin' in after
        assert not any(c.startswith('no username') for c in device.cli.received)

    def test_an_audit_only_standard_is_never_written(self, dirty_device):
        type7 = next(s for s in ALL_STANDARDS if s['name'] == 'No type-7 passwords')
        run(dirty_device, [dict(type7, allow_enforce=True)],
            mode=MODE_ENFORCE, commit=True)
        assert 'enable password 7 070C285F4D06' in dirty_device.cli.config_text()
        assert not any('no ' in c for c in dirty_device.cli.received)


class TestRemediationEvidence:
    def test_a_write_records_the_pre_change_config_and_the_commands(self, dirty_device):
        secrets = {'*': {'secret': 'S3cretFromEnv'}}
        outcome, netbox = run(dirty_device, [dict(LOCAL_USERS, allow_enforce=True)],
                              mode=MODE_ENFORCE, commit=True, secrets=secrets)
        ios_standards._post_results(netbox, [outcome])
        item = netbox.posted[0]
        assert item['remediated'] is True
        # The rollback reference is the governed lines as they were, redacted.
        assert 'username olduser privilege 15 secret 9 <redacted>' in \
            item['pre_change_config']
        assert '$14$' not in item['pre_change_config']
        # The command log shows what was sent, with the new secret removed.
        assert 'username netops privilege 15 secret <redacted>' in item['remediation_log']
        assert 'S3cretFromEnv' not in repr(netbox.posted)


class TestFailures:
    def test_a_refused_login_is_reported_against_every_standard_in_scope(self):
        with FakeIosDevice('hostname x\nend\n', password='thecorrectone') as device:
            time.sleep(0.2)
            target = ios_standards.Target(name='lab-sw-11', address='127.0.0.1',
                                          device_id=1)
            args = SimpleNamespace(mode=MODE_AUDIT, commit=False, only=None)
            netbox = FakeNetBox(ALL_STANDARDS)
            settings = settings_for(device)
            settings.password = 'wrong'
            outcome = ios_standards.check_device(settings, netbox, target, args)

        assert outcome.failed
        items = ios_standards.build_report(outcome)
        # Every standard reports Check failed — the device does not simply
        # vanish from the fleet report because nobody could log in.
        assert len(items) == len(ALL_STANDARDS)
        assert {i['result'] for i in items} == {'error'}
        assert all(i['error_message'] for i in items)


class TestOnly:
    def test_only_narrows_to_one_standard(self, dirty_device):
        outcome, _netbox = run(dirty_device, ALL_STANDARDS, only=['no http server'])
        assert [s['name'] for s, _e, _p, _c in outcome.results] == ['No HTTP server']
