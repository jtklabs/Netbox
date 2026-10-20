"""Raw command standard validation; keep worker and NetBox copies identical."""
import re


PLATFORMS = {'cisco_ios', 'arista_eos', 'cisco_asa', 'cisco_nxos', 'juniper_junos', 'infoblox_nios'}
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

    def matches(value, label):
        if not isinstance(value, list) or len(value) > 256:
            raise ValueError(f'{label} must be a list of at most 256 literal strings or regex items.')
        for index, item in enumerate(value):
            item_label = f'{label}[{index}]'
            if isinstance(item, str):
                lines([item], item_label)
                continue
            if not isinstance(item, dict) or set(item) != {'regex'}:
                raise ValueError(f'{item_label} must be a literal string or a mapping with only a regex key.')
            pattern = item['regex']
            lines([pattern], item_label + '.regex')
            try:
                re.compile(pattern, re.MULTILINE)
            except (re.error, OverflowError) as exc:
                raise ValueError(f'{item_label}.regex is invalid: {exc}') from exc

    def bundle(value, platform, label):
        mapping(value, BUNDLE_KEYS, label)
        lines(value.get('commands', []), label + '.commands', False)
        if platform == 'infoblox_nios' and 'show' not in value:
            raise ValueError(f'{label}.show is required for Infoblox NIOS audit and verification.')
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
        matches(value.get('contains', []), label + '.contains')
        matches(value.get('reject', []), label + '.reject')
        if not any(value.get(key) for key in ('expect', 'contains', 'reject')):
            raise ValueError(f'{label} requires expect, contains or reject checks for audit and verification.')
        for command in value.get('commands', []):
            words = command.strip().lower().split()
            if platform == 'infoblox_nios':
                if (words[0] != 'set' or len(words) < 2
                        or any(token in command for token in (';', '|', '&', '>', '<', '`', '$('))):
                    raise ValueError(f'{label}: Infoblox requires one noninteractive set command per item.')
            elif platform == 'juniper_junos':
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
            raise ValueError(f'{label} requires default checks or regional bundles.')
        for slug, override in regions.items():
            if not isinstance(slug, str) or not slug or slug != slug.strip().lower():
                raise ValueError(f'{label}.regions keys must be lowercase region slugs.')
            bundle(override, platform, f'{label}.regions.{slug}')


def selected_standard_ids(profile):
    """Validate explicit selection; keep legacy single-ID plans compatible."""
    if 'raw_standard_id' in profile and 'raw_standard_ids' in profile:
        raise ValueError('Use raw_standard_ids or raw_standard_id, not both.')
    if 'raw_standard_id' in profile:
        value = profile['raw_standard_id']
        if type(value) is not int or value < 1:
            raise ValueError('raw_standard_id must be a positive integer.')
        selected = [value]
    elif 'raw_standard_ids' in profile:
        selected = profile['raw_standard_ids']
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 8
                or any(type(value) is not int or value < 1 for value in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError('raw_standard_ids must contain 1–8 distinct positive integers.')
    else:
        return []
    if 'raw_commands' not in profile.get('features', []):
        raise ValueError('A raw standard selection requires the raw_commands feature.')
    return list(selected)
