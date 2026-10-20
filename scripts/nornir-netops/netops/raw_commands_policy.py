"""Raw command standard validation; keep worker and NetBox copies identical."""
PLATFORMS = {'cisco_ios', 'arista_eos', 'cisco_asa', 'cisco_nxos', 'juniper_junos'}
BUNDLE_KEYS = {'commands', 'show', 'expect', 'contains', 'reject'}
SHOW_FILTERS = {'include', 'exclude', 'begin', 'section', 'count', 'match', 'except',
                'find', 'display', 'no-more', 'trim'}


def validate_raw_commands(settings):
    def mapping(value, allowed, label):
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError(f'{label} supports only: {", ".join(sorted(allowed))}.')

    def lines(value, label, required=True):
        if (not isinstance(value, list) or len(value) > 256 or (required and not value)
                or any(not isinstance(line, str) or not line.strip() or not line.isprintable()
                       or len(line) > 2048 for line in value)):
            raise ValueError(f'{label} must be a list of single-line strings (1–256 entries, at most 2048 characters each).')

    def bundle(value, platform, label):
        mapping(value, BUNDLE_KEYS, label)
        lines(value.get('commands'), label + '.commands')
        if 'show' in value:
            command = value['show']
            lines([command], label + '.show')
            parts = command.strip().split('|')
            words = parts[0].split()
            if (len(words) < 2 or words[0].lower() != 'show'
                    or any(token in command for token in (';', '&', '>', '<', '`', '$('))
                    or any(not part.strip() or part.split()[0].lower() not in SHOW_FILTERS
                           for part in parts[1:])):
                raise ValueError(f'{label}.show must be one show command with read-only output filters.')
        lines(value.get('expect', []), label + '.expect', False)
        lines(value.get('contains', []), label + '.contains', False)
        lines(value.get('reject', []), label + '.reject', False)
        if not any(value.get(key) for key in ('expect', 'contains', 'reject')):
            raise ValueError(f'{label} requires expect, contains or reject checks for audit and verification.')
        for command in value['commands']:
            words = command.strip().lower().split()
            if platform == 'juniper_junos':
                if words[0] not in ('set', 'delete', 'activate', 'deactivate', 'insert', 'rename') or ';' in command or '|' in command:
                    raise ValueError(f'{label}: Junos requires configuration statements; commit is managed by the worker.')
            elif words[0] in ('conf', 'configure', 'end', 'write', 'wr', 'copy', 'reload', 'exit-config', 'commit'):
                raise ValueError(f'{label}: supply configuration statements; mode entry and saving are managed by the worker.')

    mapping(settings, {'enabled', 'platforms'}, 'raw_commands')
    if type(settings.get('enabled')) is not bool:
        raise ValueError('raw_commands.enabled must explicitly be true or false.')
    platforms = settings.get('platforms')
    if not isinstance(platforms, dict) or not platforms:
        raise ValueError('raw_commands.platforms must be a nonempty platform mapping.')
    for platform, value in platforms.items():
        if platform not in PLATFORMS:
            raise ValueError(f'Unsupported raw command platform: {platform}.')
        label = f'raw_commands.platforms.{platform}'
        mapping(value, BUNDLE_KEYS | {'regions'}, label)
        default = {key: val for key, val in value.items() if key != 'regions'}
        if default:
            bundle(default, platform, label)
        regions = value.get('regions', {})
        if not isinstance(regions, dict) or ('regions' in value and not regions):
            raise ValueError(f'{label}.regions must be a nonempty region mapping.')
        if not default and not regions:
            raise ValueError(f'{label} requires default commands or regional bundles.')
        for slug, override in regions.items():
            if not isinstance(slug, str) or not slug or slug != slug.strip().lower():
                raise ValueError(f'{label}.regions keys must be lowercase region slugs.')
            bundle(override, platform, f'{label}.regions.{slug}')
