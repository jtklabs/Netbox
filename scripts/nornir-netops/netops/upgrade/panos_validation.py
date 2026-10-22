"""PAN-OS assurance with stable invariants and application probes.

Sessions, ARP, dynamic route counts and throughput are evidence, not availability
thresholds. Mandatory collectors fail closed; an API error is never an empty
healthy table. The vendor HA checker is intentionally not used: it only supports
active/passive and does not validate the other member independently.
"""
import hashlib
import ipaddress
import threading
import time
import xml.etree.ElementTree as ET

from .. import cancellation
from .panos_api import configuration_digest
from .panos_profile import require, release

SNAPSHOTS = ('nics', 'routes', 'bgp_peers', 'license', 'arp_table', 'content_version',
             'session_stats', 'ip_sec_tunnels', 'fib_routes', 'global_jumbo_frame', 'mtu')
READINESS = ('candidate_config', 'jobs', 'config_locks', 'ntp_sync', 'expired_licenses',
             'certificates_requirements', 'dynamic_updates')
INFORMATIONAL = {'arp_table', 'session_stats'}


class ProbeMonitor:
    """Continuously exercise a known application during a rolling HA upgrade."""
    def __init__(self, settings, interval=5, maximum_outage=15):
        self.settings, self.interval, self.maximum_outage = settings, interval, maximum_outage
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.samples, self.outage_started, self.longest_outage = 0, None, 0
        self.last = None
        self.thread = threading.Thread(target=self.run, daemon=True)

    def observe(self, result, now):
        with self.lock:
            self.samples += 1
            self.last = result
            healthy = bool(result) and all(v.get('state') is True for v in result.values())
            if not healthy and self.outage_started is None:
                self.outage_started = now
            if self.outage_started is not None:
                self.longest_outage = max(self.longest_outage, now - self.outage_started)
                if healthy:
                    self.outage_started = None

    def run(self):
        while not self.stop.is_set():
            started = time.monotonic()
            result = probes(self.settings)
            healthy = bool(result) and all(v.get('state') is True for v in result.values())
            self.observe(result, time.monotonic() if healthy else started)
            self.stop.wait(self.interval)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=30)

    def summary(self):
        with self.lock:
            longest = max(self.longest_outage, time.monotonic() - self.outage_started if self.outage_started is not None else 0)
            return {'samples': self.samples, 'longest_outage_seconds': longest,
                    'maximum_outage_seconds': self.maximum_outage,
                    'healthy': self.samples >= 2 and self.outage_started is None and longest <= self.maximum_outage}


def probes(settings):
    import requests
    result = {}
    with requests.Session() as session:
        session.trust_env = False
        for item in settings.get('probes', []):
            cancellation.checkpoint()
            try:
                digest, size = hashlib.sha256(), 0
                with session.get(item['url'], timeout=(5, 15), stream=True, allow_redirects=False) as response:
                    for block in response.iter_content(65536):
                        cancellation.checkpoint()
                        size += len(block)
                        if size > 1024 * 1024:
                            raise ValueError('probe response exceeds 1 MiB')
                        digest.update(block)
                    ok = response.status_code == item['status'] and digest.hexdigest() == item['body_sha256'].lower()
                    result[item['name']] = {'state': ok, 'http_status': response.status_code,
                                            'body_matches': digest.hexdigest() == item['body_sha256'].lower()}
            except (requests.RequestException, ValueError) as exc:
                result[item['name']] = {'state': False, 'error': type(exc).__name__}
    return result


def collect(client, profile, emit=lambda *args: None):
    from panos_upgrade_assurance.check_firewall import CheckFirewall
    from panos_upgrade_assurance.firewall_proxy import FirewallProxy
    proxy = FirewallProxy(firewall=client)
    checker = CheckFirewall(proxy, skip_force_locale=True)
    result = {'collected_at': time.time(), 'facts': {}, 'checks': {}, 'snapshots': {}, 'errors': {}, 'probes': {}}

    def capture(name, action, destination):
        cancellation.checkpoint()
        emit('precheck', f'Collecting PAN-OS {name}')
        try:
            destination[name] = action()
        except Exception as exc:
            # Some vendor parser errors contain complete response/config data.
            result['errors'][name] = type(exc).__name__

    capture('system', client.system, result['facts'])
    def configuration():
        raw = client.configuration()
        node = ET.fromstring(raw)
        # Do not certify a feature whose operational parser has not been tested.
        unsupported = [name for name in ('ospf', 'ospfv3', 'rip', 'bfd')
                       if any((entry.findtext('enable') or entry.findtext('enabled')) == 'yes'
                              for entry in node.findall(f'.//network//{name}'))]
        if unsupported:
            result['errors']['protocol_coverage'] = 'Additional operational validation needed: ' + ', '.join(unsupported)
        return configuration_digest(raw)
    capture('configuration_sha256', configuration, result)
    capture('ha', proxy.get_ha_configuration, result)
    capture('disk_free_mb', proxy.get_disk_utilization, result)
    capture('jobs', proxy.get_jobs, result)
    pano = profile.image.startswith('Panorama_')
    if not pano:
        capture('chassis_ready', lambda: (client.result('show chassis-ready').text or '').strip(), result)
    for name in READINESS + tuple(profile.panos.get('additional_checks', [])):
        capture(name, lambda name=name: checker.run_readiness_checks([name])[name], result['checks'])
    def certificates():
        certs = proxy.get_certificates()
        failures = []
        for name, certificate in certs.items():
            expiration = certificate.get('expiry-epoch')
            if not str(expiration or '').isdigit() or int(expiration) <= time.time() + 86400:
                failures.append(name)
        return {'state': not failures, 'reason': 'Certificates expire within 24 hours or have unreadable expiry: ' + ', '.join(failures) if failures else '',
                'count': len(certs)}
    capture('certificate_expiry', certificates, result['checks'])
    if not pano:
        capture('content_version', lambda: checker.check_content_version(profile.panos['minimum_content']).to_dict(), result['checks'])
        if profile.panos.get('panorama'):
            capture('panorama', lambda: checker.check_panorama_connectivity().to_dict(), result['checks'])
        snapshots = list(SNAPSHOTS)
        if profile.panos.get('routing_engine') == 'advanced':
            snapshots = [s for s in snapshots if s not in {'routes', 'fib_routes', 'bgp_peers'}] + ['are_routes', 'are_fib_routes']
            # Advanced BGP needs a tested schema; never quietly certify it with
            # a legacy collector returning an empty table.
            result['errors']['advanced_bgp'] = 'Advanced routing BGP validation is not implemented'
        for name in snapshots:
            capture(name, lambda name=name: checker.run_snapshots([name])[name], result['snapshots'])
        licenses = result['snapshots'].get('license', {}).get('snapshot') or {}
        for value in licenses.values():
            value.pop('authcode', None)
        result['probes'] = probes(profile.panos)
    return result


def snapshot_data(snapshot, key):
    return snapshot.get('snapshots', {}).get(key, {}).get('snapshot')


def summary(snapshot):
    """Bounded NetBox evidence; full tables remain in the private run archive."""
    if not snapshot:
        return {}
    system = snapshot.get('facts', {}).get('system', {})
    group = (snapshot.get('ha') or {}).get('group') or {}
    return {
        'identity': {key: system.get(key) for key in ('hostname', 'serial', 'model', 'sw-version')},
        'checks': {key: {'state': value.get('state'), 'status': value.get('status'),
                         'reason': str(value.get('reason', ''))[:500]} for key, value in snapshot.get('checks', {}).items()},
        'snapshots': {key: {'status': value.get('status'), 'objects': len(value.get('snapshot') or {})
                            if isinstance(value.get('snapshot'), (dict, list)) else None,
                            'informational_only': key in INFORMATIONAL}
                      for key, value in snapshot.get('snapshots', {}).items()},
        'ha': {'mode': group.get('mode'), 'local_state': group.get('local-info', {}).get('state'),
               'peer_state': group.get('peer-info', {}).get('state'), 'configuration_sync': group.get('running-sync'),
               'links': {key: group.get('peer-info', {}).get(key, {}).get('conn-status') for key in ('conn-ha1', 'conn-ha2', 'conn-ha3')}},
        'probes': snapshot.get('probes', {}), 'collector_errors': snapshot.get('errors', {}),
        'configuration_sha256': snapshot.get('configuration_sha256'),
        'traffic_counters_are_informational': True,
    }


def forwarding(snapshot):
    ha = snapshot.get('ha') or {}
    return ha.get('enabled') == 'no' or (ha.get('group', {}).get('local-info', {}).get('state') in
                                       {'active', 'active-primary', 'active-secondary'})


def readiness(snapshot, profile, *, post=False):
    failures = [f'{name}: collector failed ({reason})' for name, reason in snapshot['errors'].items()]
    system = snapshot.get('facts', {}).get('system', {})
    if not isinstance(snapshot.get('ha'), dict) or snapshot['ha'].get('enabled') not in {'yes', 'no'}:
        failures.append('HA state response is missing or malformed')
    expected_checks = set(READINESS) | {'certificate_expiry'} | set(profile.panos.get('additional_checks', []))
    if not profile.image.startswith('Panorama_'):
        expected_checks.add('content_version')
        if profile.panos.get('panorama'):
            expected_checks.add('panorama')
        for key in set(SNAPSHOTS) - set(snapshot.get('snapshots', {})):
            failures.append(f'{key}: snapshot missing')
    for key in expected_checks - set(snapshot.get('checks', {})):
        failures.append(f'{key}: readiness result missing')
    if system.get('model') not in profile.models:
        failures.append('model is not approved by profile')
    current = system.get('sw-version')
    expected = [profile.target_version] if post else [*profile.starting_versions, profile.target_version]
    if current not in expected:
        failures.append('software release is not approved for this phase')
    if system.get('serial') not in {m['serial'] for m in profile.panos['members']}:
        failures.append('device serial does not match approved topology')
    for name, value in snapshot['checks'].items():
        if name == 'certificates_requirements' and value.get('status') == 'SKIPPED' and value.get('reason') == 'No certificates installed on device.':
            continue  # The collector confirmed there are no configured certificates.
        if value.get('state') is not True:
            failures.append(f'{name}: {value.get("reason") or value.get("status", "unknown")}')
    for name, value in snapshot['snapshots'].items():
        if name not in INFORMATIONAL and value.get('state') is not True:
            failures.append(f'{name}: snapshot unavailable')
    if not profile.image.startswith('Panorama_'):
        if snapshot.get('chassis_ready') != 'yes':
            failures.append('dataplane chassis is not ready')
        for name, value in snapshot.get('probes', {}).items():
            if value.get('state') is not True:
                failures.append(f'application probe failed: {name}')
        if len(snapshot.get('probes', {})) != len(profile.panos.get('probes', [])):
            failures.append('application probe results are incomplete')
    if not snapshot.get('configuration_sha256'):
        failures.append('configuration baseline is missing')
    if forwarding(snapshot):
        for name, row in (snapshot_data(snapshot, 'bgp_peers') or {}).items():
            if row.get('status') != 'Established':
                failures.append(f'BGP peer is not established: {name}')
    jobs = snapshot.get('jobs', {})
    for job in jobs.values():
        if job.get('status') != 'FIN':
            failures.append('pending device job')
    autocommits = [(int(key), value) for key, value in jobs.items() if str(key).isdigit() and value.get('type') == 'AutoCom']
    if not autocommits or max(autocommits)[1].get('result') != 'OK':
        failures.append('latest startup auto-commit has not succeeded')
    if post:
        failures += required_invariants(snapshot, profile)
    return list(dict.fromkeys(failures))


def required_invariants(snapshot, profile):
    failures = []
    if not forwarding(snapshot):
        return failures  # The independently checked forwarding member owns these gates.
    for field, area in (('required_routes', 'routes'), ('required_bgp_peers', 'bgp_peers'), ('required_tunnels', 'ip_sec_tunnels')):
        rows = snapshot_data(snapshot, area) or {}
        for name in profile.panos.get(field, []):
            if name not in rows:
                failures.append(f'{area}: required object missing: {name}')
            elif area == 'bgp_peers' and rows[name].get('status') != 'Established':
                failures.append(f'BGP peer is not established: {name}')
            elif area == 'ip_sec_tunnels' and rows[name].get('state') != 'active':
                failures.append(f'IPsec tunnel is not active: {name}')
    return failures


def compare(before, after, profile, *, role_changed=False):
    """Structural comparisons; never compare user population or packet counters."""
    failures = readiness(after, profile, post=True)
    for key in ('serial', 'model', 'hostname'):
        if before['facts']['system'].get(key) != after['facts']['system'].get(key):
            failures.append(f'device identity changed: {key}')
    if before.get('configuration_sha256') != after.get('configuration_sha256'):
        failures.append('running configuration changed; inspect schema migration or configuration drift')
    for area in ('global_jumbo_frame', 'mtu'):
        if snapshot_data(before, area) != snapshot_data(after, area):
            failures.append(f'{area} changed')
    before_nics, after_nics = snapshot_data(before, 'nics') or {}, snapshot_data(after, 'nics') or {}
    if set(before_nics) != set(after_nics):
        failures.append('interface inventory changed')
    if not role_changed:
        for name, state in before_nics.items():
            if state == 'up' and after_nics.get(name) != 'up':
                failures.append(f'previously up interface is down: {name}')
    for area, state_key, healthy in (('bgp_peers', 'status', 'Established'), ('ip_sec_tunnels', 'state', 'active')):
        old, new = snapshot_data(before, area) or {}, snapshot_data(after, area) or {}
        if set(old) != set(new):
            failures.append(f'{area} inventory changed')
        for name, row in old.items():
            if row.get(state_key) == healthy and new.get(name, {}).get(state_key) != healthy and not role_changed:
                failures.append(f'{area} lost healthy state: {name}')
    # Static/connected forwarding and explicit critical destinations must remain.
    # Other learned routes can legitimately change as remote users disconnect.
    if not role_changed:
        new_routes = snapshot_data(after, 'routes') or {}
        old_fib, new_fib = snapshot_data(before, 'fib_routes') or {}, snapshot_data(after, 'fib_routes') or {}
        for key, row in (snapshot_data(before, 'routes') or {}).items():
            flags = (row.get('flags') or '').split()
            if 'A' in flags and set(flags) & {'S', 'C'}:
                new = new_routes.get(key, {})
                if 'A' not in (new.get('flags') or '').split():
                    failures.append(f'active static/connected route lost: {key}')
                destination = row.get('destination')
                if destination:
                    expected = {name for name, item in old_fib.items() if item.get('Destination') == destination}
                    for name in expected:
                        if new_fib.get(name) != old_fib[name]:
                            failures.append(f'static/connected forwarding entry changed: {name}')
    old_license, new_license = snapshot_data(before, 'license') or {}, snapshot_data(after, 'license') or {}
    if set(old_license) - set(new_license):
        failures.append('licensed features disappeared')
    return list(dict.fromkeys(failures))


def pair_health(local, peer, settings, *, suspended=False, mixed=False):
    """Read both members independently, including HA3 for active/active."""
    mode = settings['ha_mode']
    require(mode != 'standalone', 'pair health requires an HA profile')
    states = []
    for mine, other in ((local, peer), (peer, local)):
        ha = mine.get('ha') or {}
        require(ha.get('enabled') == 'yes', 'HA is not enabled on both members')
        group = ha.get('group') or {}
        require(str(group.get('mode', '')).lower() == mode, 'HA mode differs from approved topology')
        own, remote = group.get('local-info') or {}, group.get('peer-info') or {}
        other_own = (other.get('ha', {}).get('group') or {}).get('local-info') or {}
        require(remote.get('state') == other_own.get('state'), 'HA peers disagree about their states')
        other_serial = other.get('facts', {}).get('system', {}).get('serial')
        expected_address = next((m['address'] for m in settings['members'] if m['serial'] == other_serial), None)
        try:
            remote_address = str(ipaddress.ip_interface(remote.get('mgmt-ip', '')).ip)
        except ValueError:
            remote_address = None
        require(expected_address is not None and remote_address == expected_address, 'HA peer management identity differs from approved pair')
        require(own.get('preemptive') == remote.get('preemptive') == 'no', 'disable preemption on both peers before upgrading')
        require(remote.get('conn-status') == 'up', 'HA peer communication is down')
        for link in ('conn-ha1', 'conn-ha2') + (('conn-ha3',) if mode == 'active-active' else ()):
            require((remote.get(link) or {}).get('conn-status') == 'up', f'{link} is not confirmed up')
        if not suspended:
            require(own.get('state-sync') == 'Complete', 'HA state synchronization is incomplete')
        if not mixed:
            require(group.get('running-sync-enabled') == 'yes' and group.get('running-sync') == 'synchronized',
                    'HA running configuration is not synchronized')
        for category in ('link-monitoring', 'path-monitoring'):
            monitor = group.get(category) or {}
            if monitor.get('enabled') == 'yes':
                def failed(value):
                    if isinstance(value, dict):
                        return any((key in {'status', 'state'} and str(item).lower() in {'down', 'failed', 'fail'}) or failed(item)
                                   for key, item in value.items())
                    return isinstance(value, list) and any(failed(item) for item in value)
                require(not failed(monitor), f'HA {category} reports a failure')
        states.append(own.get('state'))
    wanted = ({'suspended', 'active'} if mode == 'active-passive' else {'suspended', 'active-primary'}) if suspended else (
        {'active', 'passive'} if mode == 'active-passive' else {'active-primary', 'active-secondary'})
    require(set(states) == wanted, 'HA roles do not form the expected healthy pair')
    return states
