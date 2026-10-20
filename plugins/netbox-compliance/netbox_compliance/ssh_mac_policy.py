"""SSH MAC YAML validation, shared by contract across two independent packages.

Keep the worker and NetBox copies identical; test_ssh_mac checks this contract.
Device software determines algorithm support. This validates configuration
shape and CLI-safe algorithm names, not a fixed cryptographic policy.
"""
import re

PLATFORM_KEYS = {
    'cisco_ios': {'macs', 'ios_scope'},
    'arista_eos': {'macs'},
    'juniper_junos': {'macs'},
    'cisco_asa': {'macs', 'asa_integrity'},
}
PRESETS = ('all', 'low', 'medium', 'high', 'fips')


def validate_ssh_mac(settings):
    def options(values, allowed, label):
        if not isinstance(values, dict) or any(key not in allowed for key in values):
            raise ValueError(f'{label} must be a mapping with only: {", ".join(sorted(allowed))}.')
        if 'macs' in values:
            macs = values['macs']
            if (not isinstance(macs, list) or not 1 <= len(macs) <= 32
                    or any(not isinstance(mac, str) or not re.fullmatch(
                        r'(?:hmac|umac)-[A-Za-z0-9][A-Za-z0-9@._-]{0,95}', mac) for mac in macs)):
                raise ValueError(f'{label}.macs must be a nonempty list of SSH MAC algorithm names (maximum 32).')
            if len(set(macs)) != len(macs):
                raise ValueError(f'{label}.macs must not contain duplicates.')
        if 'ios_scope' in values and values['ios_scope'] not in ('both', 'client', 'server'):
            raise ValueError(f'{label}.ios_scope must be both, client or server.')
        if 'asa_integrity' in values and values['asa_integrity'] not in PRESETS:
            raise ValueError(f'{label}.asa_integrity must be a named preset; use macs for a custom list.')

    options(settings, {'macs', 'ios_scope', 'asa_integrity', 'platforms'}, 'ssh_mac')
    if 'platforms' in settings:
        platforms = settings['platforms']
        if not isinstance(platforms, dict) or not platforms:
            raise ValueError('ssh_mac.platforms must be a nonempty platform mapping.')
        for platform, values in platforms.items():
            if platform not in PLATFORM_KEYS:
                raise ValueError(f'Unknown SSH MAC platform: {platform}.')
            if not values:
                raise ValueError(f'ssh_mac.platforms.{platform} must not be empty.')
            options(values, PLATFORM_KEYS[platform], f'ssh_mac.platforms.{platform}')
            if platform == 'cisco_asa' and 'macs' in values and 'asa_integrity' in values:
                raise ValueError('Choose cisco_asa.macs or cisco_asa.asa_integrity, not both.')
