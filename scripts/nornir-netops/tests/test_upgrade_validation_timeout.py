"""Each upgrade profile can choose its convergence window without changing other jobs."""
from dataclasses import asdict
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops.upgrade import workflow
from netops.upgrade.profile import Profile
from test_upgrade import baseline, profile


@pytest.mark.parametrize('value', [0, -1, True, False, '7200', 1.5, float('inf')])
def test_invalid_profile_validation_timeout(profile, value):
    data = asdict(profile)
    data.update(models=list(profile.models), starting_versions=list(profile.starting_versions),
                validation_timeout=value)
    with pytest.raises(ValueError, match='validation_timeout'):
        Profile.from_mapping(data)


@pytest.mark.parametrize('family', ['cisco', 'f5', 'eos'])
def test_timeout_supported_for_every_family_and_optional(profile, family):
    data = asdict(profile)
    data.update(models=list(profile.models), starting_versions=list(profile.starting_versions))
    if family == 'f5':
        data.update(models=['BIG-IP i5800'], starting_versions=['17.1.1.3'], target_version='17.5.1.8',
                    image='BIGIP-17.5.1.8-0.0.19.iso')
    elif family == 'eos':
        data.update(models=['DCS-7050SX3-48YC8'], starting_versions=['4.30.5M'], target_version='4.32.1F',
                    image='EOS-4.32.1F.swi')
    data.pop('validation_timeout')
    assert Profile.from_mapping(data).validation_timeout is None
    data['validation_timeout'] = 7200
    assert Profile.from_mapping(data).validation_timeout == 7200


@pytest.mark.parametrize('configured,worker,expected', [(7200, 600, 7200), (600, 7200, 600),
                                                       (None, 600, 600)])
def test_profile_deadline_overrides_worker_without_mutating_it(monkeypatch, configured, worker, expected):
    clock = [0]
    monkeypatch.setattr(workflow.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(workflow.time, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    monkeypatch.setattr(workflow, 'comparison_report', lambda *args: ({}, None))
    before = baseline()
    def collect():
        after = baseline()
        if clock[0] < 4800:
            after['errors']['waf_ready'] = 'ASM is still loading'
        return after
    options = SimpleNamespace(validation_timeout=worker, poll_interval=600)
    plan = {'approved_profile': {'validation_timeout': configured}}
    emit = Mock()
    result = workflow.converge(emit, options, before, plan, Mock(), Mock(), collect, lambda _: [])
    assert result.failed == (expected == 600)
    assert options.validation_timeout == worker
    assert plan['validation_timeout'] == expected
    progress = [c.args[2]['progress_summary'] for c in emit.call_args_list if c.args[0] == 'validating']
    assert progress[0]['seconds_remaining'] == expected
    assert all(p['validation_timeout'] == expected for p in progress)
    if result.failed:
        assert '600s' in emit.call_args.args[1]
    else:
        assert clock[0] == 5400  # First clean pass at 80 minutes, then a second pass.


def test_legacy_job_without_timeout_uses_worker(monkeypatch):
    monkeypatch.setattr(workflow, 'comparison_report', lambda *args: ({}, None))
    monkeypatch.setattr(workflow.time, 'sleep', lambda _: None)
    options = SimpleNamespace(validation_timeout=600, poll_interval=0)
    plan = {'approved_profile': {}}
    result = workflow.converge(Mock(), options, baseline(), plan, Mock(), Mock(), baseline, lambda _: [])
    assert not result.failed and plan['validation_timeout'] == 600
