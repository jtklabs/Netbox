"""Carry the device's NTP transport into address-only standards changes."""

from .core import validate_word
from .ntp_discovery import interface_name


def routing_instance(value):
    return None if not value or value == 'default' else validate_word(str(value), 'NTP VRF')


def resolve(current, desired, variables, *, platform, notes=None):
    """Resolve once per device, then keep the same intent for verification.

    Missing inventory is not an instruction to move NTP to another routing
    table or remove a source. Multiple routing tables each get the new server
    set; different sources within one table require an explicit selection for
    new addresses, since one server statement cannot represent both.
    """
    if '_ntp_context_keys' in variables:
        return variables['_ntp_context_keys']
    entries = variables.get('entries') or {}
    if not entries:
        return desired
    servers = [entry.data for entry in current if entry.data.get('kind') == 'server']
    selected = routing_instance(variables.get('vrf'))
    explicit_vrf = variables.get('vrf_explicit', bool(variables.get('vrf')))
    scopes = ([selected] if explicit_vrf else
              list(dict.fromkeys(routing_instance(row.get('vrf')) for row in servers)) or [None])
    if platform == 'arista_eos' and len(scopes) > 1:
        raise ValueError('Existing EOS NTP servers span multiple VRFs; select the intended NTP VRF')

    def source(row):
        # A global source stays global. Compare its effective interface so an
        # equivalent inline source does not create a false ambiguity.
        name = row.get('source') or row.get('inherited_source')
        return (interface_name(name).lower() if name else None, row.get('source_address'))

    resolved, keys, options = {}, [], {}
    for key in desired:
        wanted = entries[key]
        if wanted.get('kind') != 'server':
            resolved[key] = dict(wanted)
            keys.append(key)
            continue
        for scope in scopes:
            candidates = [row for row in servers if routing_instance(row.get('vrf')) == scope]
            existing = [row for row in candidates if row['host'] == wanted['host']]
            donors = existing or candidates
            record = {**wanted, 'vrf': scope}
            if not wanted.get('source') and donors:
                if len({source(row) for row in donors}) != 1:
                    raise ValueError(f'Existing NTP sources differ in VRF {scope or "default"}; '
                                     'select a source interface for the new server addresses')
                # Prefer leaving an already configured global source in place.
                donor = next((row for row in donors if not row.get('source')), donors[0])
                record['source'] = interface_name(donor['source']) if donor.get('source') else None
                record['source_address'] = donor.get('source_address')
            if not variables.get('manages_auth') and not wanted.get('key') and donors:
                ids = {row.get('key') for row in donors}
                if not existing:
                    # Reuse the configured authentication key when some old
                    # endpoints were unkeyed; never weaken the new endpoints.
                    ids.discard(None)
                if len(ids) > 1:
                    raise ValueError(f'Existing NTP authentication keys differ in VRF {scope or "default"}; '
                                     'select an NTP authentication key for the new server addresses')
                record['key'] = next(iter(ids), None)
            effective_key = key + (f':vrf:{scope or "default"}' if len(scopes) > 1 else '')
            resolved[effective_key] = record
            keys.append(effective_key)
            extras = [list(row.get('extra', [])) +
                      (['iburst'] if platform != 'arista_eos' and row.get('iburst') else []) for row in donors]
            if extras and all(extra == extras[0] for extra in extras):
                options[effective_key] = extras[0]
    variables.update(entries=resolved, server_options=options, _ntp_context_keys=keys)
    if len(scopes) == 1:
        variables['vrf'] = scopes[0]
    if notes is not None and servers:
        if not explicit_vrf:
            notes.append('Using existing NTP routing context: ' + ', '.join(scope or 'default' for scope in scopes))
        if not variables.get('source'):
            notes.append('Preserving existing NTP source interfaces/addresses; global source settings remain in place')
    return keys
