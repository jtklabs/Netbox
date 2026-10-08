"""Versioned, platform-neutral settings consumed by the existing feature workers."""
import ipaddress
import json

import yaml
from django.core.exceptions import ValidationError

FEATURE_SECTIONS = {
    'ntp': 'ntp', 'syslog': 'syslog', 'snmp': 'snmp',
    'snmp_packetsize': 'snmp', 'banner': 'banner', 'acl': 'acls',
    'users': 'local_accounts',
}
SECTION_KEYS = {
    'ntp': {'servers', 'regions', 'vrf', 'source', 'prefer', 'iburst', 'authentication', 'aruba', 'clearpass'},
    'syslog': {'destinations', 'severity', 'source', 'vrf', 'facility', 'origin_id'},
    'snmp': {'allow', 'acl', 'communities', 'contact', 'location', 'chassis_id',
             'users', 'groups', 'views', 'hosts', 'packetsize'},
    'banner': {'motd', 'login', 'delimiter'},
    'local_accounts': {'names', 'privilege', 'role'},
    'acls': None,
}


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in mapping:
            raise ValueError('YAML keys must be unique strings.')
        mapping[key] = loader.construct_object(value_node)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping)


def parse_definition(text):
    """One section per standard; no credentials or arbitrary worker options."""
    try:
        if len(text) > 65536:
            raise ValueError('A standard must be at most 64 KiB.')
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(text)):
            raise ValueError('YAML aliases are not supported; write each value explicitly.')
        document = yaml.load(text, Loader=UniqueKeyLoader)
        if not isinstance(document, dict) or len(document) != 1:
            raise ValueError('Supply exactly one section, for example ntp: with a servers list.')
        section, settings = next(iter(document.items()))
        if section not in SECTION_KEYS:
            raise ValueError('Supported sections: ' + ', '.join(SECTION_KEYS))
        if section == 'acls':
            if not isinstance(settings, list) or not settings:
                raise ValueError('acls must be a nonempty list.')
        else:
            if not isinstance(settings, dict) or not settings:
                raise ValueError(f'{section} must be a nonempty mapping.')
            unknown = set(settings) - SECTION_KEYS[section]
            if unknown:
                raise ValueError(f'Unknown {section} settings: {", ".join(sorted(unknown))}')
            if section == 'ntp' and 'aruba' in settings:
                aruba = settings['aruba']
                allowed = {'conductor', 'port', 'verify_tls', 'timeout', 'exclusive_change'}
                if not isinstance(aruba, dict) or set(aruba) - allowed:
                    raise ValueError('ntp.aruba supports conductor, port, verify_tls, timeout and exclusive_change only; never store credentials here.')
                if 'conductor' in aruba and (not isinstance(aruba['conductor'], str) or not aruba['conductor'].strip()):
                    raise ValueError('ntp.aruba.conductor must be a hostname or IPv4 address.')
                for key in ('verify_tls', 'exclusive_change'):
                    if key in aruba and type(aruba[key]) is not bool:
                        raise ValueError(f'ntp.aruba.{key} must be true or false.')
                for key, maximum in (('port', 65535), ('timeout', 300)):
                    if key in aruba and (type(aruba[key]) is not int or not 1 <= aruba[key] <= maximum):
                        raise ValueError(f'ntp.aruba.{key} must be an integer from 1 to {maximum}.')
        if section == 'ntp' and 'clearpass' in settings:
            clearpass = settings['clearpass']
            if not isinstance(clearpass, dict) or set(clearpass) - {'cluster_members'}:
                raise ValueError('ntp.clearpass supports only cluster_members; never store credentials here.')
            members = clearpass.get('cluster_members', [])
            if not isinstance(members, list) or any(not isinstance(member, str) for member in members):
                raise ValueError('ntp.clearpass.cluster_members must be a list of IPv4 management addresses.')
            addresses = [str(ipaddress.IPv4Address(member)) for member in members]
            if len(set(addresses)) != len(addresses):
                raise ValueError('ntp.clearpass.cluster_members contains duplicates.')
        # Reject YAML-specific values and recursive aliases before storing a snapshot.
        encoded = json.dumps(document, allow_nan=False)
        if len(encoded) > 65536:
            raise ValueError('Expanded settings must be at most 64 KiB.')
        return json.loads(encoded)
    except (yaml.YAMLError, ValueError, TypeError, RecursionError) as exc:
        raise ValidationError({'definition_yaml': str(exc)}) from None


def snapshot_for_device(user, device, features, mode, *, audit=False):
    from .models import ConfigStandard
    from .scoping import StandardResolver, active_standards

    standards = active_standards(queryset=ConfigStandard.objects.restrict(user, 'view')).filter(
        check_type='netops').prefetch_related('platforms', 'roles', 'sites', 'device_tags')
    document, revisions = {}, []
    for standard in StandardResolver(standards=list(standards)).for_device(device):
        revision = standard.revisions.get(number=standard.revision)
        section, settings = next(iter(revision.definition['settings'].items()))
        if section in document:
            raise ValidationError(f'{device}: multiple YAML standards define {section}; narrow their scopes.')
        document[section] = settings
        revisions.append({'standard_id': standard.pk, 'revision': revision.number,
                          'name': standard.name, 'section': section,
                          'auto_remediable': standard.auto_remediable,
                          'allow_enforce': standard.allow_enforce})
    for feature in features:
        section = FEATURE_SECTIONS[feature]
        standard = next((r for r in revisions if r['section'] == section), None)
        if standard is None:
            raise ValidationError(f'{device}: no accessible, in-force YAML standard for {feature}.')
        if not audit and (not standard['auto_remediable'] or (mode == 'replace' and not standard['allow_enforce'])):
            raise ValidationError(f'{device}: {standard["name"]} does not permit {mode} remediation.')
    if 'snmp' in features or 'snmp_packetsize' in features:
        settings = document['snmp']
        required = ({'snmp_packetsize'} if 'packetsize' in settings else set())
        if set(settings) - {'packetsize'}:
            required.add('snmp')
        if not required.issubset(features):
            raise ValidationError(f'{device}: select {", ".join(sorted(required))} to check the complete SNMP standard.')
    needed = {FEATURE_SECTIONS[feature] for feature in features}
    # Preserve dotted references such as acls.permit -> snmp.allow, without
    # pinning unrelated standards to an NTP-only job.
    def references(value):
        if isinstance(value, dict):
            return set().union(*(references(v) for v in value.values())) if value else set()
        if isinstance(value, list):
            return set().union(*(references(v) for v in value)) if value else set()
        if isinstance(value, str) and '.' in value and value.split('.')[0] in document:
            return {value.split('.')[0]}
        return set()
    while True:
        expanded = needed | set().union(*(references(document[key]) for key in needed))
        if expanded == needed:
            break
        needed = expanded
    document = {key: value for key, value in document.items() if key in needed}
    revisions = [revision for revision in revisions if revision['section'] in needed]
    return {'document': document, 'revisions': revisions}
