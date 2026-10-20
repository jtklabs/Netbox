"""Two-member HA protocol: pending sync is never a blanket override."""
import copy
from contextlib import ExitStack
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from netops.upgrade import f5, f5_ha
from test_upgrade_f5 import profile, options, iso, entries

A, B = '/Common/a', '/Common/b'
GROUPS = ['/Common/datasync-global-dg', '/Common/pair']


def snapshot(role, version='17.1.1.3', build='0.0.5', sync='In Sync'):
    return {'facts': {'version': version, 'build': build, 'active_status': 'complete',
                     'active_volume': 'HD1.1', 'license_service_check_date': '2025-03-01',
                     'failover_state': role, 'sync_status': sync, 'peers': ['peer']},
            'errors': {}, 'warnings': {}, 'tables': {'waf_policies': [{}, {}]}, 'routing': {},
            'waf': {'ready': True, 'provisioned': True, 'policy_count': 2}}


class Unit:
    def __init__(self, pair, name):
        self.pair, self.name = pair, name
        self.snapshot = snapshot('active' if name == A else 'standby')
        self.revision = 'original'
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get_json(self, path):
        if path == '/mgmt/tm/cm/device':
            return {'items': [{'fullPath': n, 'name': n.rsplit('/', 1)[1], 'selfDevice': str(n == self.name).lower(),
                               'managementIp': '192.0.2.1' if n == A else '192.0.2.2'} for n in (A, B)]}
        if path == '/mgmt/tm/cm/device-group':
            return {'items': [{'fullPath': n, 'name': n.rsplit('/', 1)[1], 'autoSync': 'disabled',
                               'type': 'sync-failover' if n.endswith('/pair') else 'sync-only'} for n in GROUPS]}
        if path.endswith('/devices'):
            return {'items': [{'fullPath': n} for n in (A, B)]}
        if path.endswith('device-group/stats'):
            return {'entries': {str(i): next(iter(entries(device=n, devicegroup=g, commitIdOriginator=n,
                     commitIdTime=u.revision)['entries'].values()))
                     for i, (n, u, g) in enumerate((n, u, g) for n, u in self.pair.items() for g in GROUPS)}}
        if path.endswith('traffic-group/stats'):
            return {'entries': {n: next(iter(entries(trafficGroup='/Common/tg', deviceName=n,
                     failoverState=u.snapshot['facts']['failover_state'])['entries'].values())) for n, u in self.pair.items()}}
        raise AssertionError(path)

    def request(self, method, path, payload):
        self.calls.append((method, path, payload))
        assert path == '/mgmt/tm/cm'
        for u in self.pair.values():
            u.snapshot['facts']['sync_status'] = 'In Sync'
        return {}


@pytest.fixture
def lab(monkeypatch):
    units = {}
    units.update({n: Unit(units, n) for n in (A, B)})
    monkeypatch.setattr(f5_ha.f5_waf, 'Client', lambda host, **kw: units[A if host.hostname.endswith('.1') else B])
    monkeypatch.setattr(f5, 'collect', lambda client, progress: copy.deepcopy(client.snapshot))
    return units


def prepare(lab, name, profile, options, stack):
    return f5_ha.prepare(lab[name], SimpleNamespace(username='u', password='p'), {},
                         copy.deepcopy(lab[name].snapshot), profile, options, stack)


def first_pass(lab, profile, options):
    with ExitStack() as stack:
        pair = prepare(lab, B, profile, options, stack)
        pair.begin()
        lab[B].snapshot['facts'].update(version=profile.target_version, build='0.0.19', sync_status='Changes Pending')
        lab[B].revision = 'upgraded'
        lab[A].snapshot['facts']['sync_status'] = 'Changes Pending'
        pair.finish(lab[B], lab[B].snapshot, {}, Mock())
    return pair


def test_full_guarded_handoff_and_final_sync(lab, profile, options):
    first_pass(lab, profile, options)
    with ExitStack() as stack:
        pair = prepare(lab, A, replace(profile, allow_active=True), options, stack)
        assert pair.second
        pair.begin()
        pair.before_failover()
        lab[A].snapshot['facts']['failover_state'] = 'standby'
        lab[B].snapshot['facts']['failover_state'] = 'active'
        pair.after_failover(Mock())
        lab[A].snapshot['facts'].update(version=profile.target_version, build='0.0.19')
        plan = {}
        final = pair.finish(lab[A], lab[A].snapshot, plan, Mock())
        assert final['facts']['sync_status'] == 'In Sync'
        assert plan['ha']['phase'] == 'complete'
        assert len(lab[A].calls) == 2


@pytest.mark.parametrize('change,message', [
    ('missing', 'no verified standby'), ('expired', 'expired'), ('active_changed', 'active configuration changed'),
    ('peer_changed', 'standby configuration changed'), ('peer_offline', 'roles changed'),
    ('wrong_build', 'exact approved target'), ('policy_missing', 'baseline health differs'),
    ('sync_error', 'unexpected synchronization'), ('wrong_image', 'different target image'),
])
def test_handoff_refuses_missing_or_invalid_evidence(lab, profile, options, change, message):
    first = first_pass(lab, profile, options)
    if change == 'missing': first.path.unlink()
    elif change == 'expired':
        first.receipt['created'] = 0
        f5_ha.write_receipt(first.path, first.receipt)
    elif change == 'active_changed': lab[A].revision = 'operator-edit'
    elif change == 'peer_changed': lab[B].revision = 'operator-edit'
    elif change == 'peer_offline': lab[B].snapshot['facts']['failover_state'] = 'offline'
    elif change == 'wrong_build': lab[B].snapshot['facts']['build'] = 'other'
    elif change == 'policy_missing': lab[B].snapshot['tables']['waf_policies'].pop()
    elif change == 'sync_error': lab[A].snapshot['facts']['sync_status'] = 'Disconnected'
    elif change == 'wrong_image': profile = replace(profile, md5='f'*32)
    with ExitStack() as stack, pytest.raises(ValueError, match=message):
        prepare(lab, A, replace(profile, allow_active=True), options, stack)
    assert not lab[A].calls


def test_active_cannot_go_first_even_with_allow_active(lab, profile, options):
    with ExitStack() as stack, pytest.raises(ValueError, match='upgrade the standby first'):
        prepare(lab, A, replace(profile, allow_active=True), options, stack)


def test_pair_lock_blocks_parallel_members(lab, profile, options):
    with ExitStack() as first, ExitStack() as second:
        prepare(lab, B, profile, options, first)
        with pytest.raises(ValueError, match='another local upgrade'):
            prepare(lab, A, replace(profile, allow_active=True), options, second)


def test_manual_override_does_not_produce_handoff_receipt(lab, profile, options):
    with ExitStack() as stack:
        pair = prepare(lab, B, profile, options, stack)
        pair.begin()
        with pytest.raises(ValueError, match='manual validation'):
            pair.finish(lab[B], lab[B].snapshot, {'validation_overrides': {'8': {}}}, Mock())
        assert pair.receipt['phase'] == 'first_started'


def test_failed_traffic_handoff_never_authorizes_second_reboot(lab, profile, options, monkeypatch):
    first_pass(lab, profile, options)
    with ExitStack() as stack:
        pair = prepare(lab, A, replace(profile, allow_active=True), options, stack)
        clock = [0]
        monkeypatch.setattr(f5_ha.time, 'monotonic', lambda: clock[0])
        monkeypatch.setattr(f5_ha.cancellation, 'sleep', lambda seconds: clock.__setitem__(0, clock[0] + 121))
        with pytest.raises(RuntimeError, match='second member will not reboot'):
            pair.after_failover(Mock())
        assert not pair.receipt.get('handoff_verified')
        assert not lab[A].calls


def test_sync_refused_if_peer_build_changes(lab, profile, options):
    first_pass(lab, profile, options)
    with ExitStack() as stack:
        pair = prepare(lab, A, replace(profile, allow_active=True), options, stack)
        lab[A].snapshot['facts'].update(failover_state='standby', version=profile.target_version, build='0.0.19')
        lab[B].snapshot['facts'].update(failover_state='active', build='wrong')
        with pytest.raises(ValueError, match='refusing sync'):
            pair.finish(lab[A], lab[A].snapshot, {}, Mock())
        assert not lab[A].calls
