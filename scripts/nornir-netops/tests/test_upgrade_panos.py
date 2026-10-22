"""PAN-OS safety regressions; simulated transitions are not a live HA qualification."""
import copy
import hashlib
import json
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest

from netops.upgrade.profile import Profile
from netops.upgrade import panos_validation as validation
from netops.upgrade.panos_api import ApiError, Client, configuration_digest
from netops.upgrade.panos_profile import release
from netops.upgrade import panos_workflow as workflow
from netops.upgrade.scheduler import platform_for


@pytest.fixture
def mapping(tmp_path):
    image = tmp_path / 'PanOS_vm-12.2.3'
    image.write_bytes(b'signed-package-fixture')
    base = tmp_path / 'PanOS_vm-12.2.0'
    base.write_bytes(b'base-package-fixture')
    return dict(name='PAN-OS lab test', models=['PA-VM'], starting_versions=['12.1.6'], target_version='12.2.3',
                image=image.name, image_source=str(image), md5='', sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                minimum_free_bytes=1500000000, panos={
                    'ha_mode': 'standalone', 'members': [{'address': '192.0.2.10', 'serial': 'SERIAL0001'}],
                    'minimum_content': '9010-9583', 'base_images': [dict(image=base.name, image_source=str(base),
                          sha256=hashlib.sha256(base.read_bytes()).hexdigest())],
                    'probes': [dict(name='transit-app', url='http://198.51.100.20/health', status=200,
                                    body_sha256=hashlib.sha256(b'healthy').hexdigest())]})


@pytest.fixture
def profile(mapping):
    return Profile.from_mapping(mapping)


@pytest.fixture
def snapshot():
    success = lambda data: dict(state=True, status='SUCCESS', reason='', snapshot=data)
    return {'collected_at': 1, 'facts': {'system': {'hostname': 'pan-one', 'model': 'PA-VM', 'serial': 'SERIAL0001',
            'sw-version': '12.2.3', 'uptime': '0 days, 00:05:00', 'vm-mem': str(16 * 1024 * 1024)}},
            'errors': {}, 'checks': {k: {'state': True} for k in (*validation.READINESS, 'content_version', 'certificate_expiry')}, 'configuration_sha256': 'same',
            'disk_free_mb': {'/opt/panrepo': 8192}, 'chassis_ready': 'yes',
            'ha': {'enabled': 'no'}, 'probes': {'transit-app': {'state': True}},
            'jobs': {'2': {'type': 'AutoCom', 'status': 'FIN', 'result': 'OK'}},
            'snapshots': {
                'nics': success({'ethernet1/1': 'up', 'ethernet1/2': 'down'}),
                'routes': success({'critical': {'flags': 'A S'}, 'vpn-user': {'flags': 'A B'}}),
                'bgp_peers': success({'default_provider_upstream': {'status': 'Established', 'prefix-counter': 1000}}),
                'ip_sec_tunnels': success({'branch': {'state': 'active'}}),
                'license': success({'PA-VM': {'expired': 'no'}}),
                'session_stats': success({'active': 100000, 'throughput': 90000000}),
                'arp_table': success({'users': 500}), 'global_jumbo_frame': success(False), 'mtu': success({'ethernet1/1': 1500}),
                'fib_routes': success({}), 'content_version': success('9010-9583')}}


def pair(snapshot, mode='active-passive'):
    left, right = copy.deepcopy(snapshot), copy.deepcopy(snapshot)
    left['facts']['system']['serial'], right['facts']['system']['serial'] = 'SERIAL0001', 'SERIAL0002'
    roles = ('passive', 'active') if mode == 'active-passive' else ('active-secondary', 'active-primary')
    members = [{'address': '192.0.2.10', 'serial': 'SERIAL0001'}, {'address': '192.0.2.11', 'serial': 'SERIAL0002'}]
    for index, side in enumerate((left, right)):
        side['ha'] = {'enabled': 'yes', 'group': {'mode': mode.title(), 'running-sync-enabled': 'yes',
            'running-sync': 'synchronized', 'local-info': {'state': roles[index], 'preemptive': 'no', 'state-sync': 'Complete'},
            'peer-info': {'state': roles[1-index], 'preemptive': 'no', 'conn-status': 'up', 'mgmt-ip': members[1-index]['address'],
                          **{key: {'conn-status': 'up'} for key in ('conn-ha1', 'conn-ha2', 'conn-ha3')}}}}
    return left, right, {'ha_mode': mode, 'members': members}


def test_panos_profile_and_scheduler(profile, mapping):
    assert profile.family == 'panos'
    assert platform_for(mapping) == 'paloalto_panos'
    assert release('12.2.3-h10') > release('12.2.3-h2') > release('12.2.3')


@pytest.mark.parametrize('field,value', [('image', 'PA-VM-HPV-12.2.3.vhdx'), ('sha256', 'a'*32),
    ('md5', 'a'*32), ('target_version', '12.2.4'), ('starting_versions', ['12.2.3']),
    ('starting_versions', ['11.1.4']), ('skip_running_config_check', True)])
def test_unsafe_profile_rejected(mapping, field, value):
    mapping[field] = value
    with pytest.raises(ValueError):
        Profile.from_mapping(mapping)


@pytest.mark.parametrize('field,value', [('base_images', []), ('probes', []), ('members', []),
    ('minimum_content', ''), ('ha_mode', 'automatic'), ('verify_tls', 'false'), ('port', True),
    ('additional_checks', ['shell-command'])])
def test_incomplete_topology_or_validation_rejected(mapping, field, value):
    mapping['panos'][field] = value
    with pytest.raises(ValueError):
        Profile.from_mapping(mapping)


def test_quiet_evening_does_not_fail(snapshot, profile):
    after = copy.deepcopy(snapshot)
    after['snapshots']['session_stats']['snapshot'] = {'active': 0, 'throughput': 0}
    after['snapshots']['arp_table']['snapshot'] = {}
    del after['snapshots']['routes']['snapshot']['vpn-user']
    after['snapshots']['bgp_peers']['snapshot']['default_provider_upstream']['prefix-counter'] = 2
    assert validation.compare(snapshot, after, profile) == []


@pytest.mark.parametrize('change,expected', [
    (lambda s: s['snapshots']['routes']['snapshot'].pop('critical'), 'static/connected route'),
    (lambda s: s['snapshots']['bgp_peers']['snapshot']['default_provider_upstream'].update(status='Idle'), 'bgp_peers lost'),
    (lambda s: s['snapshots']['nics']['snapshot'].update({'ethernet1/1': 'down'}), 'interface is down'),
    (lambda s: s['snapshots']['ip_sec_tunnels']['snapshot']['branch'].update(state='init'), 'ip_sec_tunnels lost'),
    (lambda s: s['probes']['transit-app'].update(state=False), 'application probe failed'),
    (lambda s: s['snapshots']['license']['snapshot'].clear(), 'licensed features disappeared'),
    (lambda s: s['facts']['system'].update(serial='OTHER0001'), 'identity changed'),
    (lambda s: s.update(configuration_sha256='different'), 'running configuration changed'),
    (lambda s: s['errors'].update(routes='ApiError'), 'collector failed'),
    (lambda s: s['jobs']['2'].update(result='FAIL'), 'auto-commit'),
    (lambda s: s['snapshots']['mtu']['snapshot'].update({'ethernet1/1': 9000}), 'mtu changed'),
])
def test_actual_regressions_fail(snapshot, profile, change, expected):
    after = copy.deepcopy(snapshot)
    change(after)
    assert any(expected in failure for failure in validation.compare(snapshot, after, profile))


@pytest.mark.parametrize('mode', ['active-passive', 'active-active'])
def test_pair_health_checks_both_members(snapshot, mode):
    left, right, settings = pair(snapshot, mode)
    assert len(validation.pair_health(left, right, settings)) == 2
    right['ha']['group']['peer-info']['conn-ha2']['conn-status'] = 'down'
    with pytest.raises(ValueError, match='conn-ha2'):
        validation.pair_health(left, right, settings)


def test_active_active_requires_ha3(snapshot):
    left, right, settings = pair(snapshot, 'active-active')
    del left['ha']['group']['peer-info']['conn-ha3']
    with pytest.raises(ValueError, match='conn-ha3'):
        validation.pair_health(left, right, settings)


@pytest.mark.parametrize('change,message', [
    (lambda l,r: l['ha']['group']['local-info'].update(state='active'), 'disagree'),
    (lambda l,r: l['ha']['group']['peer-info'].update({'mgmt-ip': '192.0.2.99'}), 'identity'),
    (lambda l,r: r['ha']['group']['local-info'].update(preemptive='yes'), 'preemption'),
    (lambda l,r: r['ha']['group'].update({'running-sync': 'not-synchronized'}), 'configuration'),
    (lambda l,r: l['ha']['group']['local-info'].update({'state-sync': 'Incomplete'}), 'state synchronization'),
])
def test_broken_pair_blocks(snapshot, change, message):
    left, right, settings = pair(snapshot)
    change(left, right)
    with pytest.raises(ValueError, match=message):
        validation.pair_health(left, right, settings)


def test_configuration_hash_ignores_metadata_but_preserves_rule_order():
    a = b'<config version="12.1"><devices><entry name="local" admin="admin"><rules><entry name="allow"/><entry name="deny"/></rules></entry></devices></config>'
    b = a.replace(b'12.1', b'12.2').replace(b'admin="admin"', b'admin="other"')
    assert configuration_digest(a) == configuration_digest(b)
    assert configuration_digest(a) != configuration_digest(a.replace(b'allow', b'block'))
    assert configuration_digest(a) != configuration_digest(a.replace(b'<entry name="allow"/><entry name="deny"/>', b'<entry name="deny"/><entry name="allow"/>'))


def test_transient_health_does_not_pass(monkeypatch):
    outcomes = iter([True, False, True, True])
    calls = []
    monkeypatch.setattr(workflow.cancellation, 'sleep', lambda *_: None)
    def check():
        calls.append(1)
        if not next(outcomes):
            raise ValueError('peer not ready')
        return 'healthy'
    assert workflow.wait_for(check, 10, .01, lambda *a: None, 'health') == 'healthy'
    assert len(calls) == 4


def test_failed_install_is_terminal():
    client = SimpleNamespace(job=lambda _: {'status': 'FIN', 'result': 'FAIL'})
    with pytest.raises(RuntimeError, match='failed'):
        workflow.wait_install(client, '12', SimpleNamespace(install_timeout=10, poll_interval=.1), lambda *a: None)


def test_probe_outage_is_retained_after_recovery():
    monitor = validation.ProbeMonitor({}, maximum_outage=15)
    monitor.observe({'test': {'state': True}}, 0)
    monitor.observe({'test': {'state': False}}, 5)
    monitor.observe({'test': {'state': True}}, 25)
    assert monitor.summary()['longest_outage_seconds'] == 20
    assert not monitor.summary()['healthy']


def test_backup_is_encrypted_and_not_overwritten(tmp_path, profile, monkeypatch):
    monkeypatch.setenv('NETOPS_PANOS_BACKUP_KEY', '12'*32)
    raw = b'<config><devices><entry name="device"><password>secret</password></entry></devices></config>'
    client = SimpleNamespace(member={'serial': 'SERIAL0001'}, configuration=lambda: raw)
    result = workflow.backup(client, profile, tmp_path, '123')
    path = tmp_path / 'SERIAL0001-123-pre-12.2.3.xml.aesgcm'
    assert b'secret' not in path.read_bytes()
    assert result['encrypted'] and path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        workflow.backup(client, profile, tmp_path, '123')


def test_api_targets_serial_and_never_retries_unknown_write(monkeypatch):
    import requests
    sent = []
    class Session:
        def post(self, url, **kwargs):
            sent.append(kwargs)
            if kwargs['data']['type'] == 'keygen':
                return SimpleNamespace(status_code=200, content=b'<response status="success"><result><key>private-api-key</key></result></response>')
            raise requests.Timeout()
        def close(self): pass
    monkeypatch.setattr(requests, 'Session', Session)
    with Client(SimpleNamespace(username='admin', password='private-password'), {'panorama': '192.0.2.1'},
                {'address': '192.0.2.10', 'serial': 'SERIAL0001'}) as client:
        with pytest.raises(ApiError, match='outcome may be unknown'):
            client.install('12.2.3')
    assert len(sent) == 2
    assert 'target' not in sent[0]['data']
    assert sent[1]['data']['target'] == 'SERIAL0001'
    assert 'key' not in sent[1]['data']
    assert sent[1]['headers']['X-PAN-KEY'] == 'private-api-key'
    assert sent[1]['allow_redirects'] is False


def test_ready_gate_failure_performs_no_mutation(monkeypatch, tmp_path, profile, snapshot):
    snapshot['facts']['system']['sw-version'] = '12.1.6'
    calls = []
    class FakeClient:
        def __init__(self, *args, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def upload(self, *args): calls.append('upload')
        def install(self, *args): calls.append('install')
    monkeypatch.setattr(workflow, 'Client', FakeClient)
    monkeypatch.setattr(validation, 'collect', lambda *a, **k: copy.deepcopy(snapshot))
    monkeypatch.setenv('NETOPS_PANOS_APPLY_ENABLED', 'true')
    events = []
    def emit(host, stage, *args, **kwargs):
        events.append(stage)
        return stage != 'ready'
    options = SimpleNamespace(lock_dir=tmp_path, apply=True, stage_only=False)
    host = SimpleNamespace(hostname='192.0.2.10', data={})
    result = workflow.upgrade_device(SimpleNamespace(host=host), profile, options, SimpleNamespace(emit=emit))
    assert result.failed and not result.changed
    assert 'ready' in events and not calls


class SimulatedPair:
    def __init__(self, snapshot, mode):
        self.rows = pair(snapshot, mode)[:2]
        self.mode, self.calls, self.images, self.fail = mode, [], [set(), set()], ''
        for row in self.rows:
            row['facts']['system']['sw-version'] = '12.1.6'

    def client(self, host, settings, member, **kwargs):
        lab = self
        index = next(i for i, m in enumerate(settings['members']) if m == member)
        class Device:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def system(self): return copy.deepcopy(lab.rows[index]['facts']['system'])
            def result(self, command):
                if command == 'show jobs all':
                    return ET.fromstring('<result><job><id>12</id><type>AutoCom</type><status>FIN</status><result>OK</result></job></result>')
                return ET.fromstring('<result>yes</result>')
            def upload(self, path):
                lab.calls.append((index, 'upload'))
                lab.images[index].add(path.name.removeprefix('PanOS_vm-'))
            def installed_images(self): return {v: {'downloaded': 'yes'} for v in lab.images[index]}
            def ha_state(self, state):
                lab.calls.append((index, state))
                own, other = lab.rows[index]['ha']['group'], lab.rows[1-index]['ha']['group']
                active = 'active' if lab.mode == 'active-passive' else 'active-primary'
                passive = 'passive' if lab.mode == 'active-passive' else 'active-secondary'
                own['local-info']['state'] = 'suspended' if state == 'suspend' else passive
                other['local-info']['state'] = active
                own['peer-info']['state'] = active
                other['peer-info']['state'] = own['local-info']['state']
                if lab.fail == 'handoff':
                    lab.rows[1-index]['probes']['transit-app']['state'] = False
            def install(self, version):
                lab.calls.append((index, 'install'))
                if lab.fail == 'lost-install-response':
                    raise ApiError('install response lost')
                return '11'
            def job(self, job): return {'id': job, 'type': 'SWInstall', 'status': 'FIN', 'result': 'OK'}
            def reboot(self):
                lab.calls.append((index, 'reboot'))
                lab.rows[index]['facts']['system']['sw-version'] = '12.2.3'
                if lab.fail == 'postcheck':
                    lab.rows[index]['snapshots']['nics']['snapshot']['ethernet1/1'] = 'down'
        device = Device()
        device.index = index
        return device


@pytest.fixture
def simulation(monkeypatch, mapping, snapshot, tmp_path):
    def build(mode):
        lab = SimulatedPair(snapshot, mode)
        mapping['panos'].update(pair(snapshot, mode)[2])
        mapping['validation_timeout'] = 1
        profile = Profile.from_mapping(mapping)
        monkeypatch.setattr(workflow, 'Client', lab.client)
        monkeypatch.setattr(validation, 'collect', lambda client, *a, **k: copy.deepcopy(lab.rows[client.index]))
        monkeypatch.setattr(workflow, 'backup', lambda *a: {'configuration_sha256': 'same'})
        monkeypatch.setattr(workflow.cancellation, 'sleep', lambda *_: None)
        monkeypatch.setenv('NETOPS_PANOS_APPLY_ENABLED', 'true')
        class Monitor:
            def __init__(self, *args): pass
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def summary(self): return {'healthy': True, 'samples': 20, 'longest_outage_seconds': 0}
        monkeypatch.setattr(validation, 'ProbeMonitor', Monitor)
        options = SimpleNamespace(lock_dir=tmp_path, apply=True, stage_only=False, install_timeout=1,
                                  validation_timeout=1, reload_timeout=1, poll_interval=.01)
        events = []
        def run(index):
            host = SimpleNamespace(hostname=mapping['panos']['members'][index]['address'], data={'upgrade_job': {'id': index+1}})
            def emit(host, stage, *args, **kwargs):
                events.append((index, stage))
                return True
            return workflow.upgrade_device(SimpleNamespace(host=host), profile, options, SimpleNamespace(emit=emit))
        return lab, run, events
    return build


@pytest.mark.parametrize('mode', ['active-passive', 'active-active'])
def test_simulated_pair_upgrade_is_ordered_and_preserves_health(simulation, mode):
    lab, run, events = simulation(mode)
    assert not run(0).failed
    assert not run(1).failed
    assert all(row['facts']['system']['sw-version'] == '12.2.3' for row in lab.rows)
    for index in (0, 1):
        commands = [action for member, action in lab.calls if member == index]
        assert commands == ['upload', 'upload', 'suspend', 'install', 'reboot', 'functional']
    assert (0, 'completed') in events and (1, 'completed') in events


def test_second_peer_cannot_run_without_first_peer_receipt(simulation):
    lab, run, events = simulation('active-active')
    assert run(1).failed
    assert not lab.calls


@pytest.mark.parametrize('failure', ['lost-install-response', 'handoff', 'postcheck'])
def test_manual_recovery_is_fenced_and_peer_cannot_continue(simulation, failure):
    lab, run, events = simulation('active-passive')
    lab.fail = failure
    assert run(0).failed
    assert (0, 'recovery_required') in events
    assert run(1).failed
    assert not any(member == 1 for member, action in lab.calls)
    if failure == 'lost-install-response':
        assert lab.calls.count((0, 'install')) == 1
        assert (0, 'reboot') not in lab.calls
    if failure == 'handoff':
        assert (0, 'install') not in lab.calls


def test_missing_mandatory_collector_is_not_a_pass(snapshot, profile):
    snapshot['snapshots'].pop('fib_routes')
    snapshot['checks'].pop('candidate_config')
    result = validation.readiness(snapshot, profile)
    assert 'fib_routes: snapshot missing' in result
    assert 'candidate_config: readiness result missing' in result


def test_healthy_rib_cannot_hide_missing_forwarding_entry(snapshot, profile):
    snapshot['snapshots']['routes']['snapshot']['critical']['destination'] = '0.0.0.0/0'
    snapshot['snapshots']['fib_routes']['snapshot']['default'] = {'Destination': '0.0.0.0/0', 'Next Hop': '192.0.2.1'}
    after = copy.deepcopy(snapshot)
    after['snapshots']['fib_routes']['snapshot'].clear()
    assert any('forwarding entry' in item for item in validation.compare(snapshot, after, profile))


def test_panorama_distributes_upload_only_to_one_serial():
    client = object.__new__(Client)
    client.target = None
    commands = []
    def op(command):
        commands.append(ET.fromstring(command))
        return ET.fromstring('<response status="success"><result><job>34</job></result></response>')
    client.op = op
    assert client.distribute('PanOS_vm-12.2.3', 'SERIAL0001') == '34'
    command = commands[0]
    assert command.findtext('./batch/software/upload/devices') == 'SERIAL0001'
    assert command.find('./batch/software/upload-install') is None
    assert command.find('.//reboot') is None


def test_panorama_parent_success_requires_child_success():
    client = SimpleNamespace(job=lambda _: {'status': 'FIN', 'result': 'OK', 'devices': [
        {'serial-no': 'SERIAL0001', 'result': 'FAIL'}]})
    with pytest.raises(ValueError, match='per-device confirmation'):
        workflow.wait_install(client, '12', SimpleNamespace(install_timeout=.02, poll_interval=.001),
                              lambda *a: None, device_serial='SERIAL0001')
