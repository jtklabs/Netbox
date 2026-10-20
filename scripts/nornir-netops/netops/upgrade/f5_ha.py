"""Guard a two-member rolling upgrade with local, private completion evidence.

Both jobs must run on the same poller. A pending-sync exception is valid only
for the unchanged second member of a recorded, healthy first-member upgrade.
"""
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

from .. import cancellation, f5_waf
from . import checks, f5
from .profile import F5_IMAGE
from .workflow import device_lock

MAX_AGE = 24 * 3600


def require(condition, message):
    if not condition:
        raise ValueError('HA upgrade: ' + message)


def topology(client):
    rows = client.get_json('/mgmt/tm/cm/device').get('items', [])
    me = [r for r in rows if str(r.get('selfDevice')).lower() == 'true']
    require(len(rows) == 2 and len(me) == 1, 'requires exactly two readable trusted devices')
    peer = next(r for r in rows if r is not me[0])
    for row in rows:
        require(bool(row.get('fullPath')), 'device identity is missing')
        ipaddress.ip_address(row['managementIp'])
    return me[0], peer


def group_path(name):
    require(bool(re.fullmatch(r'/[\w.-]+/[\w.-]+', name)), 'unsupported device-group name')
    return '/mgmt/tm/cm/device-group/' + quote(name.replace('/', '~'), safe='~')


def groups(client, members):
    rows = client.get_json('/mgmt/tm/cm/device-group').get('items', [])
    selected = [r for r in rows if r.get('type') == 'sync-failover']
    require(len(selected) == 1, 'requires one sync-failover group')
    selected += [r for r in rows if r.get('name') == 'datasync-global-dg']
    names = []
    for row in selected:
        name = row['fullPath']
        require(row.get('autoSync') == 'disabled', 'automatic sync must be disabled before rolling upgrades')
        found = client.get_json(group_path(name) + '/devices').get('items', [])
        require(sorted(r.get('fullPath') for r in found) == sorted(members), 'device-group membership differs from the pair')
        names.append(name)
    return sorted(names)


def revisions(client, names, member):
    rows = f5.rows_from_stats(client.get_json('/mgmt/tm/cm/device-group/stats'),
                             {k: k for k in ('device', 'devicegroup', 'commitIdOriginator', 'commitIdTime')})
    result = {}
    for row in rows:
        if row['device'] == member and row['devicegroup'] in names:
            require(row['devicegroup'] not in result and all(row.values()), 'configuration revision is unreadable')
            result[row['devicegroup']] = [row['commitIdOriginator'], row['commitIdTime']]
    require(set(result) == set(names), 'configuration revision is incomplete')
    return result


def ownership(client, active, standby):
    rows = f5.rows_from_stats(client.get_json('/mgmt/tm/cm/traffic-group/stats'),
                             {k: k for k in ('trafficGroup', 'deviceName', 'failoverState')})
    found = {}
    for row in rows:
        group = row['trafficGroup']
        if group and group.endswith('/traffic-group-local-only'):
            continue
        require(bool(group) and row['deviceName'] in (active, standby), 'traffic-group ownership is unreadable')
        require(row['deviceName'] not in found.setdefault(group, {}), 'duplicate traffic-group ownership')
        found[group][row['deviceName']] = row['failoverState']
    require(bool(found) and all(v == {active: 'active', standby: 'standby'} for v in found.values()),
            'traffic groups are not consistently active/standby on the expected members')
    return sorted(found)


def healthy(baseline, current):
    require(not current['errors'], 'peer health collection failed')
    errors = [r for r in checks.compare(baseline, current) if r['severity'] == 'error']
    require(not errors, 'baseline health differs: ' + checks.describe_findings(errors))
    require(bool(current['facts'].get('license_service_check_date')), 'peer license is unreadable')


def exact_target(snapshot, profile):
    image = F5_IMAGE.fullmatch(profile.image)
    info = snapshot['facts']
    return (image is not None and info.get('version') == profile.target_version
            and info.get('build') == image[2] and info.get('active_status') == 'complete')


def write_receipt(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.ha-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(data, handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Pair:
    def __init__(self, client, peer_client, me, peer, profile, options, path, receipt, second):
        self.client, self.peer_client = client, peer_client
        self.me, self.peer = me, peer
        self.profile, self.options = profile, options
        self.path, self.receipt, self.second = path, receipt, second

    def begin(self):
        self.receipt['phase'] = 'second_started' if self.second else 'first_started'
        write_receipt(self.path, self.receipt)

    def verify_second(self, local, peer):
        data = self.receipt
        for client in (self.client, self.peer_client):
            require(groups(client, [self.me, self.peer]) == data['groups'], 'device-group topology changed')
        require(data['phase'] in ('standby_validated', 'second_started'), 'standby upgrade has not passed validation')
        require(0 <= time.time() - data['created'] <= MAX_AGE, 'standby completion evidence has expired')
        require(exact_target(peer, self.profile), 'standby is not running the exact approved target version/build')
        require(local['facts']['failover_state'] == 'active' and peer['facts']['failover_state'] == 'standby', 'roles changed before handoff')
        require(all(s['facts']['sync_status'] in ('In Sync', 'Changes Pending') for s in (local, peer)), 'unexpected synchronization state')
        require(all(local['facts'].get(k) == data['active_before']['facts'].get(k)
                    for k in ('version', 'build', 'active_volume')), 'second member software changed since the first upgrade')
        healthy(data['active_before'], local)
        healthy(data['standby_before'], peer)
        require(revisions(self.client, data['groups'], self.me) == data['active_revision'], 'active configuration changed since the first upgrade')
        require(revisions(self.peer_client, data['groups'], self.peer) == data['standby_revision'], 'standby configuration changed since validation')
        for client in (self.client, self.peer_client):
            require(ownership(client, self.me, self.peer) == data['traffic_groups'], 'traffic-group topology changed')

    def before_failover(self):
        self.verify_second(f5.collect(self.client, lambda _: None), f5.collect(self.peer_client, lambda _: None))

    def after_failover(self, emit):
        deadline = time.monotonic() + 120
        while True:
            try:
                for client in (self.client, self.peer_client):
                    require(ownership(client, self.peer, self.me) == self.receipt['traffic_groups'], 'waiting for peer traffic ownership')
                peer = f5.collect(self.peer_client, lambda _: None)
                require(peer['facts']['failover_state'] == 'active' and exact_target(peer, self.profile), 'upgraded peer is not active on target')
                healthy(self.receipt['standby_before'], peer)
                self.receipt['handoff_verified'] = True
                write_receipt(self.path, self.receipt)
                emit('failing_over', 'Upgraded peer owns every traffic group and passes health checks')
                return
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError('HA failover could not be verified; second member will not reboot') from exc
                emit('failing_over', 'Waiting for verified traffic ownership on the upgraded peer')
                cancellation.sleep(max(1, self.options.poll_interval))

    def finish(self, client, after, plan, emit):
        self.client = client
        require(not plan.get('validation_overrides'), 'manual validation cannot authorize the next HA member')
        require(exact_target(after, self.profile) and after['facts']['failover_state'] == 'standby', 'upgraded member is not healthy standby on exact target')
        healthy(self.receipt['active_before' if self.second else 'standby_before'], after)
        peer = f5.collect(self.peer_client, lambda _: None)
        healthy(self.receipt['standby_before' if self.second else 'active_before'], peer)
        active = self.peer
        for current in (client, self.peer_client):
            require(ownership(current, active, self.me) == self.receipt['traffic_groups'], 'traffic ownership changed during upgrade')
        if not self.second:
            require(all(peer['facts'].get(k) == self.receipt['active_before']['facts'].get(k)
                        for k in ('version', 'build', 'active_volume')), 'active software changed during standby upgrade')
            require(revisions(self.peer_client, self.receipt['groups'], self.peer) == self.receipt['active_revision'], 'active configuration changed during standby upgrade')
            self.receipt.update(phase='standby_validated', standby_revision=revisions(client, self.receipt['groups'], self.me))
            write_receipt(self.path, self.receipt)
            plan['ha'] = {'phase': 'standby_validated', 'peer': self.peer, 'next_member_requires_allow_active': True}
            emit('validating', 'Standby upgrade verified; saved guarded handoff evidence for the active member')
            return after
        require(exact_target(peer, self.profile), 'peer no longer runs the target release; refusing sync')
        require(revisions(self.peer_client, self.receipt['groups'], self.peer) == self.receipt['standby_revision'], 'peer configuration changed during second upgrade; refusing sync')
        emit('validating', 'Both members passed health checks on the exact target; synchronizing the device group')
        for name in self.receipt['groups']:
            result = client.request('POST', '/mgmt/tm/cm', {'command': 'run', 'utilCmdArgs': 'config-sync to-group ' + name})
            require(not str(result.get('commandResult', '')).strip(), 'configuration sync was rejected')
        deadline = time.monotonic() + (self.profile.validation_timeout or self.options.validation_timeout)
        clean = 0
        while True:
            try:
                local, peer = f5.collect(client, lambda _: None), f5.collect(self.peer_client, lambda _: None)
                healthy(self.receipt['active_before'], local)
                healthy(self.receipt['standby_before'], peer)
                require(exact_target(local, self.profile) and exact_target(peer, self.profile), 'target release changed during sync')
                for current in (client, self.peer_client):
                    require(ownership(current, self.peer, self.me) == self.receipt['traffic_groups'], 'traffic ownership changed during sync')
                require(all(s['facts']['sync_status'] == 'In Sync' for s in (local, peer)), 'waiting for both members to report In Sync')
                clean += 1
            except Exception:
                clean = 0
            if clean >= 2:
                self.receipt.update(phase='complete', completed=time.time())
                write_receipt(self.path, self.receipt)
                plan['ha'] = {'phase': 'complete', 'peer': self.peer, 'traffic_ownership_verified': True, 'both_in_sync': True}
                emit('validating', 'Both HA members passed two final health/sync checks on target; traffic ownership verified', {'upgrade_plan': plan})
                return local
            require(time.monotonic() < deadline, 'final health/synchronization timed out; inspect both members')
            emit('validating', f'Waiting for two clean pair health/sync checks; {clean}/2 passed')
            cancellation.sleep(max(1, self.options.poll_interval))



def prepare(client, host, settings, before, profile, options, stack):
    if not before['facts'].get('peers'):
        return None
    me, peer = topology(client)
    members = sorted((me['fullPath'], peer['fullPath']))
    key = hashlib.sha256(json.dumps(members).encode()).hexdigest()
    stack.enter_context(device_lock(options.lock_dir, 'f5-ha:' + key))
    stack.enter_context(device_lock(options.lock_dir, f"{peer['managementIp']}:{settings.get('port', 443)}"))
    peer_host = SimpleNamespace(hostname=peer['managementIp'], username=host.username, password=host.password)
    other = stack.enter_context(f5_waf.Client(peer_host, **settings))
    remote_me, remote_peer = topology(other)
    require(remote_me['fullPath'] == peer['fullPath'] and remote_peer['fullPath'] == me['fullPath'], 'peer identity does not match the trusted pair')
    names = groups(client, members)
    require(groups(other, members) == names, 'peer device groups differ')
    current_peer = f5.collect(other, lambda _: None)
    healthy(current_peer, current_peer)
    path = Path(options.lock_dir) / 'f5-ha' / (key + '.json')
    second = before['facts'].get('failover_state') == 'active'
    if second:
        require(profile.allow_active, 'upgrade the standby first; active handoff requires allow_active')
        require(path.is_file(), 'no verified standby upgrade on this poller; upgrade the standby first')
        receipt = json.loads(path.read_text())
        require(receipt.get('phase') in ('standby_validated', 'second_started'), 'no completed standby upgrade is available')
        require(receipt.get('image') == profile.image and receipt.get('md5') == profile.md5.lower(), 'standby evidence is for a different target image')
        require(receipt.get('active') == me['fullPath'] and receipt.get('standby') == peer['fullPath'] and receipt.get('groups') == names, 'standby evidence belongs to a different pair')
    else:
        require(before['facts'].get('failover_state') == 'standby' and current_peer['facts'].get('failover_state') == 'active', 'first upgrade must be standby with an active peer')
        require(all(s['facts'].get('sync_status') == 'In Sync' for s in (before, current_peer)), 'both members must be In Sync before the first upgrade')
        require((before['facts']['version'], before['facts']['build']) == (current_peer['facts']['version'], current_peer['facts']['build']), 'initial pair releases differ')
        traffic_groups = ownership(client, peer['fullPath'], me['fullPath'])
        require(ownership(other, peer['fullPath'], me['fullPath']) == traffic_groups, 'peer traffic ownership differs')
        receipt = dict(created=time.time(), phase='prepared', image=profile.image, md5=profile.md5.lower(), groups=names,
                       standby=me['fullPath'], active=peer['fullPath'], standby_before=before, active_before=current_peer,
                       active_revision=revisions(other, names, peer['fullPath']), traffic_groups=traffic_groups)
    pair = Pair(client, other, me['fullPath'], peer['fullPath'], profile, options, path, receipt, second)
    if second:
        pair.verify_second(before, current_peer)
    return pair
