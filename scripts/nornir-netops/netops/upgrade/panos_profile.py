"""Explicit PAN-OS paths and topology; no guessed image, peer or release."""
import ipaddress
import re
from pathlib import Path
from urllib.parse import urlsplit

IMAGE = re.compile(r"^(PanOS_[a-z0-9-]+|Panorama_pc)-(\d+\.\d+\.\d+(?:-h\d+)?)$")


def require(condition, message):
    if not condition:
        raise ValueError(message)


def release(value):
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)(?:-h(\d+))?", str(value))
    require(match, f"invalid PAN-OS release: {value!r}")
    return tuple(int(part or 0) for part in match.groups())


def image_record(record):
    require(isinstance(record, dict) and set(record) == {'image', 'sha256', 'image_source'},
            'PAN-OS base images require image, sha256 and image_source')
    require(isinstance(record['image'], str) and IMAGE.fullmatch(record['image']),
            'PAN-OS requires an upgrade package, not a VHDX/OVA base VM image')
    require(isinstance(record['sha256'], str) and re.fullmatch(r'[a-fA-F0-9]{64}', record['sha256']),
            'PAN-OS requires the vendor-published 64-digit SHA-256 checksum')
    source = record['image_source']
    require(isinstance(source, str) and bool(source), 'PAN-OS image_source is required')
    parsed = urlsplit(source)
    if parsed.scheme:
        require(parsed.scheme in {'http', 'https'} and parsed.hostname and not (
            parsed.username or parsed.password or parsed.query or parsed.fragment),
            'PAN-OS image_source must be HTTP(S) without credentials or query')
    else:
        require(Path(source).is_absolute(), 'PAN-OS local image_source must be absolute')
    require(Path(parsed.path).name == record['image'], 'image_source basename must match image')


def validate(profile):
    image_record({key: getattr(profile, key) for key in ('image', 'sha256', 'image_source')})
    require(profile.md5 == '', 'PAN-OS uses sha256; set legacy md5 to an empty string')
    require(not (profile.volume or profile.allow_active or profile.license_check_date or
                 profile.bundle_conversion_validated or profile.skip_running_config_check),
            'PAN-OS does not accept other platform overrides or skipping configuration validation')
    target = release(profile.target_version)
    require(release(IMAGE.fullmatch(profile.image)[2]) == target, 'PAN-OS image release must match target_version')
    require(all(isinstance(m, str) and re.fullmatch(r'(?:PA-[A-Za-z0-9-]+|Panorama|M-\d+)', m)
                for m in profile.models), 'PAN-OS models must be exact device model names')
    require(all(release(v) < target for v in profile.starting_versions), 'PAN-OS starting versions must precede target')
    settings = profile.panos
    require(isinstance(settings, dict), 'panos must be a mapping')
    allowed = {'ha_mode', 'members', 'panorama', 'port', 'verify_tls', 'minimum_content',
               'base_images', 'probes', 'required_routes', 'required_bgp_peers', 'required_tunnels',
               'routing_engine', 'additional_checks'}
    require(not set(settings) - allowed, 'unknown PAN-OS profile fields')
    mode = settings.get('ha_mode')
    require(mode in {'standalone', 'active-passive', 'active-active'}, 'panos.ha_mode must be explicit')
    require(type(settings.get('verify_tls', True)) is bool, 'panos.verify_tls must be boolean')
    require(type(settings.get('port', 443)) is int and 1 <= settings.get('port', 443) <= 65535,
            'panos.port must be 1–65535')
    require(settings.get('routing_engine', 'legacy') in {'legacy', 'advanced'}, 'unknown PAN-OS routing engine')
    require(re.fullmatch(r'\d+-\d+', str(settings.get('minimum_content', ''))),
            'panos.minimum_content must pin the target release content prerequisite')
    members = settings.get('members', [])
    require(isinstance(members, list) and len(members) == (1 if mode == 'standalone' else 2),
            'PAN-OS members must explicitly name one standalone device or both HA peers in upgrade order')
    for member in members:
        require(isinstance(member, dict) and set(member) == {'address', 'serial'}, 'each PAN-OS member needs address and serial')
        ipaddress.ip_address(member['address'])
        require(isinstance(member['serial'], str) and re.fullmatch(r'[A-Za-z0-9]{6,32}', member['serial'])
                and member['serial'].lower() != 'unknown', 'PAN-OS requires registered, unique device serials')
    require(len({m['address'] for m in members}) == len(members) == len({m['serial'] for m in members}),
            'PAN-OS members must have distinct addresses and serials')
    if settings.get('panorama'):
        ipaddress.ip_address(settings['panorama'])
    if profile.image.startswith('Panorama_'):
        require(mode == 'standalone' and not settings.get('panorama'), 'Panorama upgrades require a standalone direct profile')
    bases = settings.get('base_images', [])
    require(isinstance(bases, list), 'panos.base_images must be a list')
    for base in bases:
        image_record(base)
        require(IMAGE.fullmatch(base['image'])[1] == IMAGE.fullmatch(profile.image)[1],
                'PAN-OS base package platform must match target package')
    require(len({b['image'] for b in bases}) == len(bases) and profile.image not in {b['image'] for b in bases},
            'PAN-OS image list contains duplicates')
    # Every feature hop needs an explicitly staged base. Installation remains a
    # single approved hop; unsupported skip paths cannot be inferred from a filename.
    for start in profile.starting_versions:
        a = release(start)
        require(a[:2] == target[:2] or (a[0] == target[0] and target[1] == a[1] + 1),
                'PAN-OS profiles support one same-major feature hop at a time')
        if a[:2] != target[:2]:
            require(any(release(IMAGE.fullmatch(b['image'])[2])[:3] == (*target[:2], 0) for b in bases),
                    'PAN-OS feature upgrades require the target feature base package in base_images')
    for key in ('required_routes', 'required_bgp_peers', 'required_tunnels', 'additional_checks'):
        values = settings.get(key, [])
        require(isinstance(values, list) and all(isinstance(v, str) and v for v in values), f'panos.{key} must be a string list')
    require(set(settings.get('additional_checks', [])) <= {'environmentals', 'dp_cpu_utilization', 'mp_cpu_utilization',
            'mp_mem_utilization', 'planes_clock_sync'}, 'unsupported additional PAN-OS readiness checks')
    probes = settings.get('probes', [])
    require(isinstance(probes, list), 'panos.probes must be a list')
    if not profile.image.startswith('Panorama_'):
        require(probes, 'PAN-OS upgrades require synthetic application probes routed through the firewall')
    for probe in probes:
        require(isinstance(probe, dict) and set(probe) == {'name', 'url', 'status', 'body_sha256'},
                'PAN-OS probes need name, url, status and body_sha256')
        parsed = urlsplit(probe['url'])
        require(parsed.scheme in {'http', 'https'} and parsed.hostname and not (
                parsed.username or parsed.password or parsed.fragment), 'invalid probe HTTP(S) URL')
        require(isinstance(probe['name'], str) and probe['name'] and type(probe['status']) is int
                and 200 <= probe['status'] < 400, 'invalid probe name or expected status')
        require(isinstance(probe['body_sha256'], str) and re.fullmatch(r'[a-fA-F0-9]{64}', probe['body_sha256']),
                'probe body_sha256 must identify the expected application response')
    require(len({p['name'] for p in probes}) == len(probes), 'probe names must be unique')
    return profile
