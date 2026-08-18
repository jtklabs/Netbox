"""Remediation planning, and the guards that stop it locking somebody out.

These are the tests that matter most in this directory. Everything else
describes a report; this describes what gets typed into a production switch.
"""

import pytest
from conftest import LOCAL_USERS, NO_HTTP, NO_TYPE_7, PASSWORD_ENCRYPTION
from iosconfig import (
    MODE_AUDIT,
    MODE_ENFORCE,
    MODE_UPDATE,
    evaluate,
    parse_config,
    plan_remediation,
)

SECRETS = {'*': {'secret': 'S3cretFromEnv'}}


def plan(standard, config, mode, **kwargs):
    evaluation = evaluate(standard, parse_config(config))
    return plan_remediation(standard, evaluation, mode, **kwargs)


def enforceable(standard):
    return dict(standard, allow_enforce=True)


class TestAuditChangesNothing:
    def test_audit_plans_no_commands_at_all(self, dirty_config):
        for standard in (NO_HTTP, PASSWORD_ENCRYPTION, LOCAL_USERS):
            result = plan(standard, dirty_config, MODE_AUDIT, secrets=SECRETS)
            assert result.commands == [], standard['name']
            assert not result.will_change

    def test_a_compliant_device_plans_nothing_in_any_mode(self, clean_config):
        for mode in (MODE_AUDIT, MODE_UPDATE, MODE_ENFORCE):
            assert not plan(LOCAL_USERS, clean_config, mode, secrets=SECRETS).will_change


class TestUpdateAddsOnly:
    def test_update_adds_a_missing_present_line(self, dirty_config):
        result = plan(PASSWORD_ENCRYPTION, dirty_config, MODE_UPDATE)
        assert [c.text for c in result.add] == ['service password-encryption']
        assert result.remove == []

    def test_update_adds_a_missing_account_with_the_runtime_secret(self, dirty_config):
        result = plan(LOCAL_USERS, dirty_config, MODE_UPDATE, secrets=SECRETS)
        assert [c.text for c in result.add] == \
            ['username netops privilege 15 secret S3cretFromEnv']
        assert result.remove == []

    def test_update_refuses_to_remove_anything(self, dirty_config):
        # An `absent` standard's fix IS a removal, so update reports it and
        # does nothing — the surprising-but-correct consequence of "update
        # never removes anything".
        result = plan(NO_HTTP, dirty_config, MODE_UPDATE)
        assert result.commands == []
        assert any('enforce' in item.why for item in result.blocked)


class TestSecretsNeverLeak:
    def test_the_displayed_command_is_redacted(self, dirty_config):
        result = plan(LOCAL_USERS, dirty_config, MODE_UPDATE, secrets=SECRETS)
        command = result.add[0]
        assert 'S3cretFromEnv' in command.text          # what actually gets sent
        assert 'S3cretFromEnv' not in command.display   # what gets printed and stored
        assert command.display.endswith('secret <redacted>')

    def test_a_per_account_secret_beats_the_default(self, dirty_config):
        secrets = {'*': {'secret': 'fleetwide'}, 'netops': {'secret': 'justnetops'}}
        result = plan(LOCAL_USERS, dirty_config, MODE_UPDATE, secrets=secrets)
        assert result.add[0].text.endswith('secret justnetops')

    def test_a_missing_secret_blocks_the_addition_rather_than_half_rendering_it(
        self, dirty_config
    ):
        result = plan(LOCAL_USERS, dirty_config, MODE_UPDATE, secrets={})
        assert result.add == []
        assert any('secret' in item.why for item in result.blocked)
        # And nothing containing a leftover placeholder is ever queued.
        assert not any('{' in c.text for c in result.commands)


class TestEnforceRemoves:
    def test_enforce_negates_an_absent_violation(self, dirty_config):
        result = plan(enforceable(NO_HTTP), dirty_config, MODE_ENFORCE)
        assert [c.text for c in result.remove] == ['no ip http server']

    def test_enforce_is_refused_when_the_standard_does_not_allow_it(self, dirty_config):
        result = plan(NO_HTTP, dirty_config, MODE_ENFORCE)
        assert result.commands == []
        assert any('enforce is not enabled' in item.why for item in result.blocked)

    def test_removal_enters_the_block_the_line_lives_in(self, dirty_config):
        # An indented `password 7 ...` cannot be negated from global config
        # mode, so the parent line is part of the command sequence.
        standard = dict(NO_TYPE_7, auto_remediable=True, allow_enforce=True,
                        remove_template='no {line}')
        result = plan(standard, dirty_config, MODE_ENFORCE)
        vty = next(c for c in result.remove if c.context == 'line vty 0 4')
        assert vty.as_sent() == ['line vty 0 4', 'no password 7 121A0C041104', 'exit']
        assert vty.as_shown() == ['line vty 0 4', 'no password 7 <redacted>', 'exit']

    def test_adds_are_ordered_before_removes(self, dirty_config):
        result = plan(enforceable(LOCAL_USERS), dirty_config, MODE_ENFORCE, secrets=SECRETS)
        kinds = [c.kind for c in result.commands]
        assert kinds.index('add') < kinds.index('remove')


class TestAuditOnlyStandards:
    def test_nothing_is_ever_planned_for_an_audit_only_standard(self, dirty_config):
        for mode in (MODE_UPDATE, MODE_ENFORCE):
            result = plan(dict(NO_TYPE_7, allow_enforce=True), dirty_config, mode)
            assert result.commands == []
            assert result.blocked

    def test_the_block_reason_is_the_standard_own_guidance(self, dirty_config):
        result = plan(NO_TYPE_7, dirty_config, MODE_ENFORCE)
        assert 'Audit only' in result.blocked[0].why


class TestLockoutGuards:
    """The point of the whole exercise: enforce must not strand the operator."""

    def test_the_session_account_is_never_removed(self, dirty_config):
        result = plan(enforceable(LOCAL_USERS), dirty_config, MODE_ENFORCE,
                      secrets=SECRETS, session_user='admin')
        assert 'admin' not in {c.entry for c in result.remove}
        assert any('logged in as' in item.why for item in result.blocked)
        # The other extras are still removed — the guard is surgical.
        assert {c.entry for c in result.remove} == {'olduser', 'monitor'}

    def test_replacing_the_last_admin_within_one_run_is_allowed(self):
        config = 'username onlyadmin privilege 15 secret 9 $14$abc\nend\n'
        # No secret supplied, so the replacement cannot be built either — but
        # test the privilege guard on its own by expecting the account that IS
        # there and only removing the other one.
        standard = dict(enforceable(LOCAL_USERS),
                        entries=[{'key': 'netops', 'vars': {'privilege': '15'}}])
        result = plan(standard, config, MODE_ENFORCE, secrets={'*': {'secret': 'x'}})
        # netops is added at privilege 15, so removing onlyadmin is now safe.
        assert [c.entry for c in result.add] == ['netops']
        assert [c.entry for c in result.remove] == ['onlyadmin']

    def test_it_refuses_when_the_replacement_cannot_be_built(self):
        config = 'username onlyadmin privilege 15 secret 9 $14$abc\nend\n'
        standard = dict(enforceable(LOCAL_USERS))
        result = plan(standard, config, MODE_ENFORCE, secrets={})   # no secret
        assert result.add == []
        assert result.remove == []
        assert any('could not be built' in item.why for item in result.blocked)

    def test_a_privilege_1_account_can_go_without_a_replacement(self):
        config = ('username keeper privilege 15 secret 9 $14$abc\n'
                  'username monitor privilege 1 secret 9 $14$def\nend\n')
        standard = dict(enforceable(LOCAL_USERS),
                        entries=[{'key': 'keeper', 'vars': {'privilege': '15'}}])
        result = plan(standard, config, MODE_ENFORCE, secrets=SECRETS)
        assert [c.entry for c in result.remove] == ['monitor']
        assert result.add == []

    def test_the_last_admin_is_kept_when_nothing_replaces_it(self):
        config = 'username onlyadmin privilege 15 secret 9 $14$abc\nend\n'
        # The standard governs accounts but expects one that is already there,
        # so there is nothing to add and onlyadmin is surplus — and removing it
        # would leave no privileged account at all.
        standard = dict(enforceable(LOCAL_USERS),
                        entries=[{'key': 'monitor', 'vars': {'privilege': '1'}}])
        result = plan(standard, config, MODE_ENFORCE, secrets=SECRETS)
        assert [c.entry for c in result.add] == ['monitor']
        assert result.remove == []
        assert any('last privilege-15' in item.why for item in result.blocked)


class TestBlockedIsAlwaysExplained:
    @pytest.mark.parametrize('standard,mode,secrets', [
        (NO_HTTP, MODE_ENFORCE, None),
        (NO_TYPE_7, MODE_ENFORCE, None),
        (LOCAL_USERS, MODE_UPDATE, {}),
    ])
    def test_every_refusal_carries_a_reason(self, dirty_config, standard, mode, secrets):
        result = plan(standard, dirty_config, mode, secrets=secrets or {})
        assert result.blocked
        for item in result.blocked:
            assert item.what and item.why
