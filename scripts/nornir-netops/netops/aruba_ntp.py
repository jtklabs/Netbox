"""IPv4 NTP at an individual AOS8 managed-device node on Mobility Conductor."""

import ipaddress
import json
import re
import time
from collections.abc import Mapping
from urllib.parse import urlencode

from . import archive
from .core import MODE_REPLACE, validate_address

ROOT = '/v1/configuration/object/'
SERVERS = 'ntp_server_info'
SOURCE = 'ntp_source'
MAC = r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}'


def settings(value):
    value = {} if value is None else value
    if not isinstance(value, Mapping):
        raise ValueError('ntp.aruba must be a mapping')
    unknown = set(value) - {'conductor', 'port', 'verify_tls', 'timeout', 'exclusive_change'}
    if unknown:
        raise ValueError(f'Unknown ntp.aruba settings: {sorted(unknown)}')
    result = dict(conductor=None, port=4343, verify_tls=True, timeout=30, exclusive_change=False)
    result.update(value)
    if result['conductor'] is not None:
        if not isinstance(result['conductor'], str) or not result['conductor'].strip():
            raise ValueError('ntp.aruba.conductor must be a hostname or IPv4 address')
    for key in ('verify_tls', 'exclusive_change'):
        if type(result[key]) is not bool:
            raise ValueError(f'ntp.aruba.{key} must be true or false')
    for key, maximum in (('port', 65535), ('timeout', 300)):
        if type(result[key]) is not int or not 1 <= result[key] <= maximum:
            raise ValueError(f'ntp.aruba.{key} must be an integer from 1 to {maximum}')
    return result


def ipv4(value):
    try:
        if not isinstance(value, str):
            raise ValueError('not an address string')
        return str(ipaddress.IPv4Address(value))
    except (ValueError, TypeError):
        raise ValueError('Aruba NTP requires IPv4 literals; IPv6 and DNS names are not managed') from None


def checked(document, require_result=False):
    if not isinstance(document, dict):
        raise ValueError('Aruba API returned an invalid JSON object')
    found = False

    def visit(value):
        nonlocal found
        if isinstance(value, dict):
            for key, child in value.items():
                if key in ('_result', '_global_result'):
                    found = True
                    if not isinstance(child, dict) or str(child.get('status')) != '0':
                        # Responses can echo credentials; do not print their contents.
                        raise RuntimeError('Aruba API rejected the request')
                elif key.lower() == 'error':
                    raise RuntimeError('Aruba API returned an error')
                else:
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(document)
    if require_result and not found:
        raise ValueError('Aruba API response has no success result')
    return document


class Client:
    def __init__(self, host, address, options):
        import requests

        address = validate_address(str(address))
        if ':' in address:
            raise ValueError('Use an IPv4 address or hostname for Mobility Conductor')
        self.base = f'https://{address}:{options["port"]}'
        self.host, self.options = host, options
        self.session = requests.Session()
        self.token = None

    def request(self, method, path, params=None, body=None, login=False):
        import requests

        params = dict(params or {})
        if self.token and 'X-CSRF-Token' not in self.session.headers:
            params['UIDARUBA'] = self.token
        kwargs = {'data': body} if login else {'json': body}
        try:
            response = self.session.request(method, self.base + path, params=params,
                                            timeout=self.options['timeout'],
                                            verify=self.options['verify_tls'],
                                            allow_redirects=False, **kwargs)
            if not 200 <= response.status_code < 300:
                raise RuntimeError(f'Aruba {method} {path} failed (HTTP {response.status_code})')
            document = response.json()
        except requests.RequestException:
            raise RuntimeError(f'Aruba {method} {path} transport failed') from None
        except ValueError:
            raise ValueError(f'Aruba {method} {path} returned invalid JSON') from None
        return checked(document, require_result=method == 'POST')

    def __enter__(self):
        from .debuglog import protect

        try:
            if not self.host.username or not self.host.password:
                raise ValueError('Mobility Conductor requires inventory username and password')
            protect([self.host.password])
            result = self.request('POST', '/v1/api/login', login=True,
                                  body={'username': self.host.username, 'password': self.host.password})
            auth = result.get('_global_result', {})
            self.token = auth.get('UIDARUBA')
            csrf = auth.get('X-CSRF-Token') or auth.get('X-CSRFToken')
            if not isinstance(self.token, str) or not self.token or self.token == '(null)':
                raise ValueError('Mobility Conductor login returned no session token')
            protect([self.token, csrf] if csrf else [self.token])
            if csrf:
                self.session.headers['X-CSRF-Token'] = csrf
            return self
        except Exception:
            self.session.close()
            raise

    def __exit__(self, *args):
        try:
            self.request('GET', '/v1/api/logout')
        except Exception:
            pass
        finally:
            self.session.close()

    def get(self, name, path, **params):
        return self.request('GET', ROOT + name, {'config_path': path, **params})

    def post(self, name, path, body):
        return self.request('POST', ROOT + name, {'config_path': path}, body)

    def switches(self, debug=False):
        document = self.request('GET', '/v1/configuration/showcommand',
                                {'command': 'show switches debug' if debug else 'show switches'})
        rows = document.get('All Switches')
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError('Aruba show switches returned no usable device table')
        return rows


def device_path(path):
    if (not isinstance(path, str) or len(path) > 256 or
            not re.fullmatch(r'/md(?:/[A-Za-z0-9_.-]+)*/' + MAC, path) or
            any(part in ('.', '..') for part in path.split('/'))):
        raise ValueError('aruba_config_path must be a device path under /md ending in its MAC address')
    return path


def discover_path(client, address):
    switches = client.switches(debug=True)
    rows = [row for row in switches if row.get('IP Address') == address]
    if len(rows) != 1 or rows[0].get('Type') not in ('MD', 'LC'):
        raise ValueError('Conductor did not uniquely identify the managed WLC by its IPv4 address')
    mac, node = rows[0].get('MAC'), rows[0].get('Nodepath')
    if not isinstance(mac, str) or not re.fullmatch(MAC, mac) or not isinstance(node, str):
        raise ValueError('Conductor returned no valid WLC MAC or Nodepath')
    if sum(str(row.get('MAC', '')).lower() == mac.lower() for row in switches) != 1:
        raise ValueError('Conductor returned an ambiguous WLC MAC')
    # show switches debug normally returns the parent group, not the device leaf.
    if re.fullmatch(MAC, node.rsplit('/', 1)[-1]):
        if node.rsplit('/', 1)[-1].lower() != mac.lower():
            raise ValueError('Conductor Nodepath and WLC MAC disagree')
        return device_path(node)
    return device_path(node + '/' + mac.lower())


def identity(client, path, address, *, discovered=False):
    device_path(path)
    mac = path.rsplit('/', 1)[1].lower()
    if discovered:
        if discover_path(client, address) != path:
            raise ValueError('WLC configuration path changed during the job; retry after reviewing its mapping')
    else:
        rows = [row for row in client.switches(debug=True) if row.get('IP Address') == address]
        if len(rows) != 1 or str(rows[0].get('MAC', '')).lower() != mac:
            raise ValueError('Controller IPv4 address and config-path MAC do not match Conductor inventory')
    info = client.get('sys_info', path)
    if info.get('_global', {}).get('_switch_role') not in ('conductor', 'master'):
        raise ValueError('The selected API endpoint is not a Mobility Conductor')
    local = info.get('_local', {})
    if (local.get('_type') in (None, 'group', 'root') or
            str(local.get('_hardware', {}).get('_mac', '')).lower() != mac):
        raise ValueError('Mobility Conductor did not confirm the individual device node')
    pending = local.get('_pending', {}).get('write_mem_reqd')
    if type(pending) is not bool:
        raise ValueError('Mobility Conductor did not return a reliable pending-change state')
    if pending:
        raise ValueError('Device node has pending changes; review and resolve them before this job')


def state(client, address):
    rows = [row for row in client.switches() if row.get('IP Address') == address]
    if len(rows) != 1 or rows[0].get('Type') not in ('MD', 'LC'):
        raise ValueError('Mobility Conductor did not uniquely identify the managed controller')
    row = rows[0]
    if not str(row.get('Config ID', '')).isdigit():
        raise ValueError('Mobility Conductor returned no valid controller Config ID')
    return {'config_id': int(row['Config ID']), 'status': row.get('Status'),
            'configuration_state': row.get('Configuration State')}


def synced(observed):
    return observed['status'] == 'up' and observed['configuration_state'] == 'UPDATE SUCCESSFUL'


def read(client, path):
    result = {}
    for name in (SERVERS, SOURCE):
        document = client.get(name, path, type='committed', limit=65536, offset=1)
        data = document.get('_data')
        if not isinstance(data, dict) or name not in data:
            raise ValueError(f'Aruba response is missing {name}')
        value = data[name]
        if name == SERVERS:
            if not isinstance(value, list) or len(value) > 16:
                raise ValueError('Aruba NTP response must contain a complete server list (maximum 16)')
            seen = set()
            for row in value:
                if not isinstance(row, dict) or row.get('ip6') or row.get('fqdn'):
                    raise ValueError('Existing Aruba NTP servers include unsupported IPv6/DNS configuration')
                address = ipv4(row.get('ip'))
                if address in seen:
                    raise ValueError('Aruba NTP response has duplicate server addresses')
                seen.add(address)
                if type(row.get('iburst', False)) is not bool:
                    raise ValueError('Aruba NTP iburst must be boolean')
        elif value is not None and not isinstance(value, dict):
            raise ValueError('Aruba NTP source response must be an object or null')
        result[name] = value
    return result


def source(value):
    if not value:
        return None
    if value.lower() == 'loopback0':
        return {'loopback': True}
    match = re.fullmatch(r'Vlan(\d+)', value, re.I)
    if match and 1 <= int(match[1]) <= 4095:
        return {'vlanid': int(match[1])}
    raise ValueError('Aruba NTP source tag must select Loopback0 or Vlan1 through Vlan4095')


def protected(row):
    flags = row.get('_flags', {})
    if not isinstance(flags, dict):
        raise ValueError('Aruba NTP flags must be an object')
    return any(flags.get(key) for key in ('inherited', 'readonly', 'undeletable', 'system', 'pending'))


def plan(before, variables, mode):
    wanted = [ipv4(item) for item in variables['servers']]
    current = {row['ip']: row for row in before[SERVERS]}
    if len(set(wanted) | (set(current) if mode != MODE_REPLACE else set())) > 16:
        raise ValueError('Aruba supports at most 16 NTP servers')
    changes, blockers = [], []
    for address, row in current.items():
        if mode == MODE_REPLACE and address not in wanted:
            if protected(row):
                blockers.append(f'NTP server {address} is inherited or protected; change its owning node manually')
            else:
                changes.append((SERVERS, {'ip': address, '_action': 'delete'}))
    for address in wanted:
        row = current.get(address)
        if row is not None and bool(row.get('iburst', False)) == variables['iburst']:
            continue
        if row and protected(row):
            blockers.append(f'NTP server {address} is inherited or protected; change its owning node manually')
            continue
        # Modify only the managed property; preserve existing key bindings.
        changes.append((SERVERS, {'ip': address, 'iburst': variables['iburst'],
                                  '_action': 'modify' if row else 'add'}))
    selected = source(variables.get('source'))
    existing = before[SOURCE] or {}
    if selected and any(existing.get(key) != value for key, value in selected.items()):
        if protected(existing):
            blockers.append('NTP source is inherited or protected; change its owning node manually')
        else:
            changes.append((SOURCE, {**selected, '_action': 'add'}))
    return changes, blockers


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .features.ntp import per_device

    payload = dict(platform='aruba_os', mode=mode, current=[], desired=[], add=[], remove=[],
                   commands=[], compliant=False, advisories=[], notes=[], rollback=[],
                   rollback_unsupported=[], applied=False, saved=None, verified=None,
                   missing_after=[], save_command=None, skipped=False, skip_reason=None)
    attempted = False
    try:
        _, variables = per_device(list(desired), dict(variables), task.host)
        if variables.get('manages_auth') or variables.get('prefer') or variables.get('vrf'):
            raise ValueError('Aruba NTP authentication, preferred-server and VRF overrides are not supported')
        options = settings(variables.get('aruba'))
        inventory = task.host.data.get('mobility_conductor')
        tagged = 'mobility_conductor' in task.host.data
        if task.host.data.get('mobility_conductor_error'):
            raise ValueError(task.host.data['mobility_conductor_error'])
        pinned = variables.get('mobility_conductor_snapshot')
        if pinned is not None and inventory != pinned:
            raise ValueError('The tagged Mobility Conductor changed after scheduling; re-create the job')
        if tagged:
            from .mobility_conductor import validate
            inventory = validate(inventory)
            if inventory['device_id'] == task.host.data.get('netbox_id'):
                raise ValueError('Select managed WLC devices, not the tagged Mobility Conductor')
            conductor = inventory['address']
            path = None
        else:
            conductor = task.host.data.get('aruba_conductor') or options['conductor']
            path = task.host.data.get('aruba_config_path')
            if not conductor:
                raise ValueError('Use NetBox inventory with one mobility-conductor tag, or configure a CSV Conductor address')
        address = ipv4(task.host.hostname)
        if tagged and address == conductor:
            raise ValueError('The WLC and Mobility Conductor must have different management IPv4 addresses')
        payload.update(config_path=path, conductor=conductor, conductor_inventory=inventory, desired=variables['servers'])
        payload['notes'].append('Audit covers committed controller configuration and deployment, not NTP synchronization')
        with Client(task.host, conductor, options) as client:
            if tagged:
                path = discover_path(client, address)
                payload['config_path'] = path
            identity(client, path, address, discovered=tagged)
            initial = state(client, address)
            if not synced(initial):
                raise ValueError('Controller is offline or its configuration is not synchronized with Conductor')
            before = read(client, path)
            identity(client, path, address, discovered=tagged)
            if state(client, address) != initial:
                raise ValueError('Controller deployment changed during audit; retry the job')
            changes, blockers = plan(before, variables, mode)
            payload.update(config_before=before, current=[row['ip'] for row in before[SERVERS]],
                           deployment_before=initial, compliant=not changes and not blockers,
                           advisories=blockers,
                           add=[body['ip'] for name, body in changes if name == SERVERS and body['_action'] != 'delete'],
                           remove=[body['ip'] for name, body in changes if name == SERVERS and body['_action'] == 'delete'])
            if variables.get('region'):
                payload['notes'].append(f'NTP servers for region {variables["region"]}')
            if changes or blockers:
                payload['rollback_unsupported'] = ['Restore the captured NTP configuration at this device node manually; no automatic Conductor rollback or discard is performed.']
            if blockers:
                if not dry_run:
                    raise ValueError('; '.join(blockers))
                return Result(host=task.host, result=payload)
            query = urlencode({'config_path': path})
            payload['commands'] = [f'POST {ROOT}{name}?{query} {json.dumps(body, sort_keys=True)}'
                                   for name, body in changes]
            if changes:
                payload['save_command'] = f'POST {ROOT}write_memory?{query} {{}}'
            if changes and not dry_run:
                if not save or not verify or not options['exclusive_change']:
                    raise ValueError('Aruba apply requires saving, verification and ntp.aruba.exclusive_change: true during an exclusive change window')
                # write_memory commits ALL changes at this node, not just ours.
                identity(client, path, address, discovered=tagged)
                if read(client, path) != before or state(client, address) != initial:
                    raise ValueError('Controller configuration changed during planning; retry the job')
                archive.checkpoint(task, payload)
                attempted = True
                payload['saved'] = False
                for name, body in changes:
                    client.post(name, path, body)
                client.post('write_memory', path, {})
                payload.update(applied=True, saved=True, verified=False)
                deadline = time.monotonic() + options['timeout']
                while True:
                    observed = state(client, address)
                    payload['deployment_after'] = observed
                    if synced(observed) and observed['config_id'] > initial['config_id']:
                        after = read(client, path)
                        payload['config_after'] = after
                        remaining, blocked = plan(after, variables, mode)
                        if not remaining and not blocked:
                            identity(client, path, address, discovered=tagged)
                            payload['verified'] = True
                            break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('Could not verify committed NTP configuration and controller deployment before timeout')
                    time.sleep(2)
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        if attempted:
            payload['advisories'].append('A change was attempted. Review pending and committed configuration at the device node before retrying; no automatic discard was performed.')
        return Result(host=task.host, result=payload, changed=attempted, failed=True, exception=exc)
