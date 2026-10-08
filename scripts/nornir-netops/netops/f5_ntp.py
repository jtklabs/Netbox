"""BIG-IP NTP server-list reconciliation through the existing iControl client."""
import json

from . import archive, f5_waf
from .core import MODE_REPLACE, normalize, validate_address
from .netbox import source_for

ENDPOINT = '/mgmt/tm/sys/ntp'


def servers(document):
    if not isinstance(document, dict) or not isinstance(document.get('servers'), list):
        raise ValueError('F5 NTP response must contain a server list')
    if document.get('include') and str(document['include']).strip() not in ('none', ''):
        raise ValueError('F5 custom NTP include directives require manual review')
    if any(not isinstance(item, str) for item in document['servers']):
        raise ValueError('F5 NTP servers must be strings')
    return list(dict.fromkeys(normalize(validate_address(item)) for item in document['servers']))


def run(task, desired, variables, mode, dry_run, save, verify):
    from nornir.core.task import Result
    from .features.ntp import per_device
    payload = {'platform': 'f5_tmsh', 'mode': mode, 'current': [], 'desired': [],
               'add': [], 'remove': [], 'commands': [], 'compliant': False,
               'advisories': [], 'notes': [], 'rollback': [], 'rollback_unsupported': [],
               'applied': False, 'saved': None, 'verified': None, 'missing_after': [],
               'save_command': None, 'skipped': False, 'skip_reason': None}
    attempted = False
    try:
        source, authoritative = source_for(task.host, 'ntp')
        if source or (not authoritative and variables.get('source')):
            raise ValueError('F5 NTP source routing is not configured by an interface tag; remove the NTP source selection for this job')
        desired, variables = per_device(list(desired), dict(variables), task.host)
        if variables.get('manages_auth') or variables.get('prefer') or variables.get('vrf'):
            raise ValueError('F5 NTP adapter supports server lists, not authentication, prefer, or VRF overrides')
        wanted = variables['servers']
        payload['desired'] = wanted
        if not task.host.username or not task.host.password:
            raise ValueError('F5 REST requires a username and password')
        with f5_waf.Client(task.host, **variables['f5']) as client:
            before = servers(client.get_json(ENDPOINT))
            after = wanted if mode == MODE_REPLACE else list(dict.fromkeys(before + wanted))
            payload.update(current=before, config_before={'servers': before},
                           add=[s for s in wanted if s not in before],
                           remove=[s for s in before if s not in after],
                           compliant=set(before) == set(after))
            if not payload['compliant']:
                body = {'servers': after}
                payload['commands'] = ['PATCH ' + ENDPOINT + ' ' + json.dumps(body)]
                payload['rollback_steps'] = [{'transport': 'rest', 'method': 'PATCH', 'path': ENDPOINT,
                                              'body': {'servers': before}, 'purpose': 'restore'}]
                payload['notes'].append('F5 source routing, timezone and NTP restrict settings are left unchanged')
                if save:
                    payload['save_command'] = 'POST /mgmt/tm/sys/config {"command": "save"}'
                if not dry_run:
                    archive.checkpoint(task, payload)
                    attempted = True
                    client.patch_json(ENDPOINT, body)
                    payload['applied'] = True
                    if verify:
                        observed = servers(client.get_json(ENDPOINT))
                        payload['config_after'] = {'servers': observed}
                        payload['verified'] = set(observed) == set(after)
                        payload['missing_after'] = [] if payload['verified'] else ['NTP server list differs after write']
                    if save:
                        payload['saved'] = False
                        if payload['verified'] is not False:
                            client.save_config()
                            payload['saved'] = True
        return Result(host=task.host, result=payload, changed=attempted)
    except Exception as exc:
        return Result(host=task.host, result=payload, changed=attempted, failed=True, exception=exc)
