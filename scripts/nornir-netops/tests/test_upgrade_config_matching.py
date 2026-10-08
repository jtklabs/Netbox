"""The saved profile can waive only the running-config baseline comparison."""
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops.upgrade import checks, report, workflow
from netops.upgrade.profile import Profile
from test_upgrade import baseline, profile


@pytest.mark.parametrize('value', ['false', 1, None])
def test_profile_rejects_non_boolean_skip(profile, value):
    data = asdict(profile)
    data.update(models=list(profile.models), starting_versions=list(profile.starting_versions),
                skip_running_config_check=value)
    with pytest.raises(ValueError, match='must be a boolean'):
        Profile.from_mapping(data)


def test_old_profile_keeps_matching_enabled(profile):
    data = asdict(profile)
    data.pop('skip_running_config_check')
    data.update(models=list(profile.models), starting_versions=list(profile.starting_versions))
    assert Profile.from_mapping(data).skip_running_config_check is False
    data['skip_running_config_check'] = True
    assert Profile.from_mapping(data).skip_running_config_check is True


def test_only_running_config_diff_is_waived():
    before, after = baseline(), baseline()
    after['config'] += '\nservice new-default'
    assert any(f['check'] == 'config' and f['severity'] == 'error' for f in checks.compare(before, after))
    findings = checks.compare(before, after, skip_running_config_check=True)
    assert not [f for f in findings if f['severity'] == 'error']
    assert any(f['check'] == 'config' and 'skipped' in f['message'] for f in findings)
    after['startup_config'] += '\nservice new-default'
    after['tables']['mac'] = []
    after['errors']['config'] = 'read failed'
    errors = {f['check'] for f in checks.compare(before, after, skip_running_config_check=True)
              if f['severity'] == 'error'}
    assert {'startup_config', 'mac', 'config'} <= errors


def test_saved_config_and_boot_checks_are_not_waived(profile):
    from dataclasses import replace
    profile = replace(profile, skip_running_config_check=True)
    before, after = baseline(), baseline()
    after['config'] += '\nservice new-default'
    after['config'] = after['config'].replace('flash:packages.conf', 'flash:wrong.bin')
    findings = workflow.target_findings(after, profile, before)
    assert any(f['check'] == 'config_boot' for f in findings)
    assert any('startup' in f.get('message', '').lower() for f in findings)


def test_convergence_uses_frozen_profile_and_reports_skipped_check(monkeypatch):
    before, after = baseline(), baseline()
    after['config'] += '\nservice new-default'
    plan = {'approved_profile': {'skip_running_config_check': True}}
    monkeypatch.setattr(workflow, 'comparison_report', lambda *args: ({}, None))
    monkeypatch.setattr(workflow.time, 'sleep', lambda seconds: None)
    emit = Mock()
    options = SimpleNamespace(validation_timeout=10, poll_interval=0)
    result = workflow.converge(emit, options, before, plan, Mock(), Mock(), lambda: after, lambda _: [])
    assert not result.failed
    assert result.result['status'] == 'completed_with_warnings'
    assert sum(call.args[0] == 'validating' for call in emit.call_args_list) == 2
    text = report.build(SimpleNamespace(name='lab', hostname='192.0.2.1'), before, after,
                        result.result['findings'], plan, result.result['status'])
    assert 'matching was skipped by the job profile' in text
    assert 'service new-default' in text
