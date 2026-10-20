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
COLLECT = f5.collect


def snapshot(role, version='17.1.1.3', build='0.0.5', sync='In Sync'):
    return {'facts': {'version': version, 'build': build, 'active_status': 'complete',
                     'active_volume': 'HD1.1', 'license_service_check_date': '2025-03-01',
                     'failover_state': role, 'sync_status': sync, 'peers': ['peer']},
            'errors': {}, 'warnings': {}, 'tables': {'waf_policies': [{'fullPath': '/Common/p1'}, {'fullPath': '/Common/p2'}]}, 'routing': {},
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
                               'managementIp': '192.0.2.1' if n == A else '192.0.2.2',
                               'failoverState': self.pair[n].snapshot['facts']['failover_state']} for n in (A, B)]}
        if path == '/mgmt/tm/cm/device-group':
            return {'items': [{'fullPath': n, 'name': n.rsplit('/', 1)[1], 'autoSync': 'disabled',
                               'type': 'sync-failover' if n.endswith('/pair') else 'sync-only'} for n in GROUPS]}
        if path.endswith('/devices'):
            return {'items': [{'fullPath': n} for n in (A, B)]}
        if path.endswith('device-group/stats'):
            return {'entries': {str(i): next(iter(entries(device=n, devicegroup=g, commitIdOriginator=A if u.revision == 'original' else n,
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
        plan = {'validation_overrides': {'8': {}}}
        assert pair.finish(lab[B], lab[B].snapshot, plan, Mock()) == lab[B].snapshot
        assert pair.receipt['phase'] == 'handoff_withheld'
        assert plan['ha']['automatic_sync_skipped']
        assert not lab[B].calls
    with ExitStack() as stack, pytest.raises(ValueError, match='no completed standby upgrade'):
        prepare(lab, A, replace(profile, allow_active=True), options, stack)


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


@pytest.mark.parametrize('acceptance', ['strict', 'threshold', 'manual'])
def test_real_workflow_runs_standby_then_handoff_then_second(lab, profile, options, monkeypatch, acceptance):
    from test_upgrade_f5 import FakeBigIP, IMAGE_BYTES, ISO
    from netops.upgrade import workflow

    from netops import cancellation
    from test_f5_validation import control, override
    channel = control()
    if acceptance == 'threshold':
        profile = replace(profile, f5_ltm_min_unchanged_percent=50)
    timeline, units = [], {}
    class Appliance(FakeBigIP):
        def __init__(self, name):
            super().__init__(failover='active' if name == A else 'standby', peers=('b' if name == A else 'a',))
            self.name, self.pair, self.revision = name, units, 'original'
            self.images[ISO] = {'verified': 'yes', 'bytes': bytearray(IMAGE_BYTES)}

        @property
        def snapshot(self):
            return snapshot(self.failover, self.version, sync=self.sync)

        def _get(self, path):
            if path == '/mgmt/tm/cm/device':
                rows = Unit.get_json(self, path)['items']
                for row in rows:
                    unit = units[row['fullPath']]
                    active = next(v for v in unit.volumes if v['active'])
                    row.update(hostname=row['name'], marketingName=self.model, platformId='C119',
                               chassisId=row['name'], failoverState=unit.failover, version=unit.version, build=active['build'])
                return {'items': rows}
            if path.startswith('/mgmt/tm/cm/device-group') or path.endswith('traffic-group/stats'):
                return Unit.get_json(self, path)
            return super()._get(path)

        def _post(self, path, payload):
            if path == '/mgmt/tm/cm':
                timeline.append((self.name, 'sync'))
                assert all(u.version == profile.target_version for u in units.values())
                for unit in units.values(): unit.sync = 'In Sync'
                return {}
            if path == '/mgmt/tm/sys/failover':
                timeline.append((self.name, 'failover'))
                for name, unit in units.items(): unit.failover = 'standby' if name == self.name else 'active'
            if path == '/mgmt/tm/sys' and payload.get('command') == 'reboot':
                timeline.append((self.name, 'reboot'))
                assert self.failover == 'standby'
                self.revision = 'upgraded'
                if acceptance != 'strict':
                    self.virtual_availability = 'offline'
                for unit in units.values(): unit.sync = 'Changes Pending'
            return super()._post(path, payload)

    units.update({n: Appliance(n) for n in (A, B)})
    # Replace the protocol-only fixture's snapshot stub with the production collector.
    monkeypatch.setattr(f5, 'collect', COLLECT)
    monkeypatch.setattr(f5_ha.f5_waf, 'Client', lambda host, **kw: units[A if host.hostname.endswith('.1') else B])
    options.apply = True
    for name, address in ((B, '192.0.2.2'), (A, '192.0.2.1')):
        host = SimpleNamespace(name=name.rsplit('/', 1)[1], hostname=address, platform='f5_tmsh', username='u', password='p', data={})
        def emit(host, stage, message, payload, **kwargs):
            if acceptance == 'manual' and payload.get('progress_summary', {}).get('error_count'):
                channel.observe({'supports_validation_overrides': True, 'validation_overrides': {'8': override(8)}})
            return True
        reporter = Mock(emit=Mock(side_effect=emit))
        with cancellation.bind(channel):
            result = workflow.upgrade_device(SimpleNamespace(host=host), replace(profile, allow_active=name == A), options, reporter)
        assert not result.failed, [call.args[2] for call in reporter.emit.call_args_list][-3:]
        if acceptance != 'strict':
            assert result.result['status'] == 'completed_with_warnings'
            assert any(f['check'] == 'ha_handoff' for f in result.result['findings'])
            assert not any(f['severity'] == 'error' for f in result.result['findings'])
            assert timeline == [(B, 'reboot')]
            with ExitStack() as stack, pytest.raises(ValueError, match='no completed standby upgrade'):
                prepare(units, A, replace(profile, allow_active=True), options, stack)
            return
    final_payload = [c.args[3] for c in reporter.emit.call_args_list if len(c.args) > 3 and 'post' in c.args[3]][-1]
    assert final_payload['post']['facts']['sync_status'] == 'In Sync'
    assert final_payload['findings'] == []
    assert timeline == [(B, 'reboot'), (A, 'failover'), (A, 'reboot'), (A, 'sync'), (A, 'sync')]
    assert units[B].failover == 'active' and units[A].failover == 'standby'
    assert all(u.sync == 'In Sync' for u in units.values())


@pytest.mark.parametrize('change', ['active', 'peer_offline', 'local_edit', 'peer_edit', 'peer_errors'])
def test_last_moment_change_prevents_first_reboot(lab, profile, options, change):
    with ExitStack() as stack:
        pair = prepare(lab, B, profile, options, stack)
        pair.before_reboot(Mock())
        if change == 'active': lab[B].snapshot['facts']['failover_state'] = 'active'
        elif change == 'peer_offline': lab[A].snapshot['facts']['failover_state'] = 'offline'
        elif change == 'local_edit': lab[B].revision = 'operator-edit'
        elif change == 'peer_edit': lab[A].revision = 'operator-edit'
        elif change == 'peer_errors': lab[A].snapshot['errors']['waf'] = 'unavailable'
        with pytest.raises(ValueError):
            pair.before_reboot(Mock())
        assert not lab[B].calls


def test_second_reboot_rechecks_upgraded_peer(lab, profile, options):
    first_pass(lab, profile, options)
    with ExitStack() as stack:
        pair = prepare(lab, A, replace(profile, allow_active=True), options, stack)
        pair.begin()
        lab[A].snapshot['facts']['failover_state'] = 'standby'
        lab[B].snapshot['facts']['failover_state'] = 'active'
        pair.after_failover(Mock())
        pair.before_reboot(Mock())
        lab[B].snapshot['facts']['build'] = 'unexpected'
        with pytest.raises(ValueError, match='reboot cancelled'):
            pair.before_reboot(Mock())
        assert not lab[A].calls


@pytest.mark.parametrize('second', [False, True])
def test_threshold_acceptance_completes_local_validation_without_authorizing_pair(lab, profile, options, second):
    profile = replace(profile, f5_waf_min_unchanged_percent=50)
    if second:
        first_pass(lab, profile, options)
    name = A if second else B
    with ExitStack() as stack:
        pair = prepare(lab, name, replace(profile, allow_active=second), options, stack)
        pair.begin()
        lab[name].snapshot['tables']['waf_policies'].pop()
        plan = {}
        assert pair.finish(lab[name], lab[name].snapshot, plan, Mock()) == lab[name].snapshot
        assert pair.receipt['phase'] == 'handoff_withheld'
        assert plan['ha']['automatic_sync_skipped']
        assert not lab[name].calls
