"""ClearPass NTP audit and explicitly scoped publisher remediation over SSH."""

import ipaddress
import re
from collections.abc import Mapping

from . import archive
from .core import MODE_REPLACE
from .netbox import source_for

SHOW = 'show ntp'
CLUSTER = 'cluster list'


def ipv4(value):
    if not isinstance(value, str):
        raise ValueError('ClearPass NTP requires IPv4 address strings')
    try:
        return str(ipaddress.IPv4Address(value))
    except ValueError:
        raise ValueError('ClearPass NTP requires IPv4 literals, not IPv6 or DNS names') from None


def settings(value):
    value = {} if value is None else value
    if not isinstance(value, Mapping) or set(value) - {'cluster_members'}:
        raise ValueError('ntp.clearpass supports only cluster_members; credentials belong in inventory')
    members = value.get('cluster_members', [])
    if not isinstance(members, list):
        raise ValueError('ntp.clearpass.cluster_members must be a list of IPv4 management addresses')
    members = [ipv4(member) for member in members]
    if len(set(members)) != len(members):
        raise ValueError('ntp.clearpass.cluster_members contains duplicate addresses')
    return {'cluster_members': members}


class Client:
    """Use a generic SSH shell: no enable, paging changes or configuration mode."""

    def __init__(self, host):
        self.host = host
        self.connection = None

    def __enter__(self):
        from netmiko.terminal_server import TerminalServerSSH
        from .debuglog import protect

        params = self.host.get_connection_parameters('netmiko')
        extras = params.extras or {}
        kwargs = {key: extras[key] for key in (
            'conn_timeout', 'auth_timeout', 'banner_timeout', 'use_keys', 'key_file',
            'allow_agent', 'ssh_config_file', 'alt_host_keys', 'alt_key_file',
        ) if key in extras}
        protect([params.password])
        try:
            self.connection = TerminalServerSSH(
                host=params.hostname, port=params.port or 22, username=params.username,
                password=params.password, ssh_strict=True, system_host_keys=True, **kwargs)
            prompt = self.connection.find_prompt()
            if not re.fullmatch(r'\[appadmin(?:@[^\]\r\n]+)?\]#', prompt):
                raise ValueError('Expected the ClearPass appadmin CLI prompt')
            self.prompt = re.escape(prompt) + r'\s*$'
            return self
        except Exception:
            if self.connection:
                self.connection.disconnect()
            raise

    def __exit__(self, *args):
        self.connection.disconnect()

    def command(self, command):
        output = self.connection.send_command(command, expect_string=self.prompt, read_timeout=120)
        if re.search(r'(?im)^\s*(?:ERROR\b|Invalid\b|Usage:|Permission denied|Command not found|Failed\b|WARNING\b)', output):
            raise ValueError(f'ClearPass rejected {command.split()[0]} command; inspect appliance logs')
        if '[More]' in output or '--More--' in output:
            raise ValueError('ClearPass returned paginated output; refusing a partial audit')
        return output


def parse_ntp(output):
    """Require a complete labeled configuration, never infer absence from junk."""
    rows = []
    saw_primary = saw_secondary = header = False
    current = None
    for line in output.splitlines():
        line = line.strip()
        if not line or re.fullmatch(r'[=-]+', line) or line == SHOW or re.fullmatch(r'\[appadmin[^\]]*\]#(?: show ntp)?', line):
            continue
        if line == 'NTP Server Information':
            header = True
            continue
        match = re.fullmatch(r'(Primary|Secondary) NTP\s*:\s*(.+)', line)
        if match:
            label, address = match.groups()
            if label == 'Primary':
                if saw_primary or saw_secondary:
                    raise ValueError('ClearPass NTP response contains an ambiguous primary server')
                saw_primary = True
            else:
                saw_secondary = True
            current = None
            if address != '<not configured>':
                current = {'server': ipv4(address), 'role': label.lower(), 'key_id': None, 'algorithm': None}
                rows.append(current)
            continue
        match = re.fullmatch(r'(Key ID|Algorithm)\s*:\s*(.+)', line)
        if match and current is not None:
            key = 'key_id' if match[1] == 'Key ID' else 'algorithm'
            if current[key] is not None:
                raise ValueError('ClearPass NTP response contains duplicate authentication fields')
            value = match[2]
            if value not in ('<not configured>', 'None', '0'):
                if key == 'key_id' and (not value.isdigit() or not 1 <= int(value) <= 65534):
                    raise ValueError('ClearPass returned an invalid NTP key ID')
                current[key] = value
            continue
        raise ValueError('Unrecognized ClearPass show ntp output; refusing an incomplete audit')
    if not header or not saw_primary or not saw_secondary or len(rows) > 5:
        raise ValueError('Incomplete ClearPass NTP configuration; expected primary and secondary fields')
    if len({row['server'] for row in rows}) != len(rows):
        raise ValueError('ClearPass NTP configuration contains duplicate servers')
    return rows


def parse_cluster(output, address):
    """Read the documented 6.10 labels and 6.11 pipe-delimited cluster table."""
    if not re.search(r'Cluster Commu(?:nication|ication) Mode\s*:\s*ipv4\b', output):
        raise ValueError('ClearPass cluster communication mode must be IPv4')
    rows, columns = [], None
    for line in output.splitlines():
        line = line.strip()
        if (not line or re.fullmatch(r'[+\-]+', line) or line == CLUSTER or
                re.fullmatch(r'\[appadmin[^\]]*\]#(?: cluster list)?', line) or
                re.match(r'^Cluster (?:Commu(?:nication|ication) Mode|high-availability)\s*:', line) or
                line.startswith('REP_LAST_UPDATE_TS -')):
            continue
        if line.startswith('|'):
            cells = [cell.strip() for cell in line.strip('|').split('|')]
            if 'MGMT IP' in cells:
                if columns is not None or not {'MGMT IP', 'TYPE', 'STATUS'}.issubset(cells):
                    raise ValueError('Invalid ClearPass cluster table header')
                columns = cells
                continue
            if columns is None or len(cells) != len(columns):
                raise ValueError('Incomplete ClearPass cluster table')
            record = dict(zip(columns, cells))
            role = record['TYPE'].replace('[local machine]', '').strip().lower()
            if role not in ('publisher', 'subscriber'):
                raise ValueError('Unknown ClearPass cluster role')
            rows.append({'address': ipv4(record['MGMT IP']), 'role': role,
                         'local': '[local machine]' in record['TYPE'], 'status': record['STATUS']})
        elif re.match(r'^(Publisher|Subscriber)\s*:', line):
            match = re.fullmatch(r'(Publisher|Subscriber)\s*:\s*Management port IP=(\S+)\s+.*', line)
            if not match:
                raise ValueError('Incomplete ClearPass cluster member')
            rows.append({'address': ipv4(match[2]), 'role': match[1].lower(),
                         'local': '[local machine]' in line, 'status': None})
        else:
            raise ValueError('Unrecognized ClearPass cluster output; refusing an incomplete member list')
    local = [row for row in rows if row['local']]
    publishers = [row for row in rows if row['role'] == 'publisher']
    if (len(local) != 1 or local[0]['address'] != address or len(publishers) != 1 or
            len({row['address'] for row in rows}) != len(rows)):
        raise ValueError('Could not establish ClearPass local identity and unique cluster publisher')
    return rows


def command(servers):
    if not 1 <= len(servers) <= 5:
        raise ValueError('ClearPass requires one to five NTP servers')
    servers = [ipv4(item) for item in servers]
    return 'configure date -p ' + servers[0] + ''.join(' -s ' + item for item in servers[1:])


def plan(before, wanted, mode):
    wanted = list(dict.fromkeys(ipv4(item) for item in wanted))
    current = [row['server'] for row in before]
    after = wanted if mode == MODE_REPLACE else list(dict.fromkeys(current + wanted))
    if not 1 <= len(after) <= 5:
        raise ValueError('ClearPass requires one to five NTP servers')
    # Primary is not an NTP preference; order changes alone are not drift.
    return after, set(current) != set(after)


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .features.ntp import per_device

    payload = dict(platform='aruba_clearpass', mode=mode, current=[], desired=[], add=[], remove=[],
                   commands=[], compliant=False, advisories=[], notes=[], rollback=[],
                   rollback_unsupported=[], applied=False, saved=None, verified=None,
                   missing_after=[], save_command=None, skipped=False, skip_reason=None)
    attempted = False
    try:
        selected, authoritative = source_for(task.host, 'ntp')
        if selected or (not authoritative and variables.get('source')):
            raise ValueError('ClearPass configure date cannot select an NTP source interface; remove the source selection for this standard')
        _, variables = per_device(list(desired), dict(variables), task.host)
        if variables.get('manages_auth') or variables.get('prefer') or variables.get('vrf'):
            raise ValueError('ClearPass NTP authentication, preference and VRF overrides are not supported')
        options = settings(variables.get('clearpass'))
        from .clearpass_cluster import validate as validate_cluster
        if task.host.data.get('clearpass_cluster_error'):
            raise ValueError(task.host.data['clearpass_cluster_error'])
        tagged = task.host.data.get('clearpass_cluster')
        if tagged is not None:
            tagged = validate_cluster(tagged)
        pinned = variables.get('clearpass_cluster_snapshot')
        if pinned is not None and tagged != validate_cluster(pinned):
            raise ValueError('ClearPass tags, roles or management addresses changed since this job was scheduled; re-create the job')
        address = ipv4(task.host.hostname)
        if tagged is not None and not any(m['address'] == address and m['device_id'] == task.host.data.get('netbox_id') for m in tagged):
            raise ValueError('The target is not a tagged member of the global ClearPass cluster')
        wanted = [ipv4(item) for item in variables['servers']]
        payload['desired'] = wanted
        payload['verification_scope'] = 'local-node'
        payload['cluster_inventory'] = tagged
        if options['cluster_members']:
            payload['notes'].append('Legacy ntp.clearpass.cluster_members is ignored; membership comes from NetBox role tags')
        payload['notes'].append('Checks the local NTP server list, not clock synchronization; source routing, timezone and iburst are unmanaged')
        with Client(task.host) as client:
            before = parse_ntp(client.command(SHOW))
            after, drift = plan(before, wanted, mode)
            current = [row['server'] for row in before]
            payload.update(config_before=before, current=current, compliant=not drift,
                           add=[item for item in after if item not in current],
                           remove=[item for item in current if item not in after])
            cluster = parse_cluster(client.command(CLUSTER), address)
            if tagged is not None and ({m['address']: m['role'] for m in tagged} !=
                                       {m['address']: m['role'] for m in cluster}):
                raise ValueError('ClearPass live cluster membership or roles differ from NetBox tags')
            payload['cluster'] = cluster
            payload['affected_members'] = [row['address'] for row in cluster]
            if not drift:
                return Result(host=task.host, result=payload)
            local = next(row for row in cluster if row['local'])
            if local['role'] != 'publisher':
                payload['advisories'].append('NTP is cluster-managed; remediate the publisher and audit this subscriber afterward')
            if any(row['key_id'] or row['algorithm'] for row in before):
                payload['advisories'].append('Existing authenticated NTP configuration requires manual remediation; key material is not readable')
            if any(row['status'] not in (None, 'ENABLED') for row in cluster):
                payload['advisories'].append('Cluster includes a non-enabled member; review cluster health before remediation')
            if payload['advisories']:
                if not dry_run:
                    raise ValueError('; '.join(payload['advisories']))
                return Result(host=task.host, result=payload)
            payload['commands'] = [command(after)]
            if current:
                payload['rollback'] = [command(current)]
            else:
                payload['rollback_unsupported'] = ['Restoring an unconfigured NTP state requires manual intervention']
            payload['notes'].append('This command changes cluster NTP settings. Verification covers this publisher only; audit every subscriber separately.')
            if not dry_run:
                if tagged is None or not variables.get('allow_clearpass_cluster_changes'):
                    raise ValueError('ClearPass apply requires NetBox role-tag discovery and explicit cluster-wide change approval on the job/profile')
                if not save or not verify:
                    raise ValueError('ClearPass applies and persists immediately; saving and verification must remain enabled')
                if (parse_cluster(client.command(CLUSTER), address) != cluster or
                        parse_ntp(client.command(SHOW)) != before):
                    raise ValueError('ClearPass cluster or NTP configuration changed during planning; retry')
                archive.checkpoint(task, payload)
                attempted = True
                client.command(payload['commands'][0])
                payload.update(applied=True, verified=False)
                observed = parse_ntp(client.command(SHOW))
                payload['config_after'] = observed
                if (set(row['server'] for row in observed) != set(after) or
                        any(row['key_id'] or row['algorithm'] for row in observed)):
                    raise ValueError('ClearPass NTP readback differs after configuration')
                if parse_cluster(client.command(CLUSTER), address) != cluster:
                    raise ValueError('ClearPass cluster membership changed during configuration; audit the cluster')
                payload.update(verified=True, saved=True)
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        if attempted:
            payload['advisories'].append('A cluster change was attempted; review publisher and subscribers before retrying. No automatic rollback was performed.')
        return Result(host=task.host, result=payload, changed=attempted, failed=True, exception=exc)
