"""Require a numeric release bump for deployable poller changes (Python 3.11+)."""
import argparse
import ast
from pathlib import PurePosixPath
import re
import subprocess
import tomllib


POLLERS = {
    'nornir-netops': 'netops/__init__.py',
    'snmp-inventory': 'snmpinv/__init__.py',
}


def git(*args):
    return subprocess.check_output(['git', *args], text=True)


def version(ref, name, source):
    root = f'scripts/{name}'
    tree = ast.parse(git('show', f'{ref}:{root}/{source}'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == '__version__' for target in node.targets
        ):
            value = ast.literal_eval(node.value)
            break
    else:
        # Historical nornir releases predate the shared version constant.
        value = tomllib.loads(git('show', f'{ref}:{root}/pyproject.toml'))['project']['version']
    if not isinstance(value, str) or not re.fullmatch(r'(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', value):
        raise ValueError(f'{root}/{source}: expected a numeric major.minor.patch version')
    return tuple(map(int, value.split('.')))


def deployable(path, name):
    relative = PurePosixPath(path).relative_to(f'scripts/{name}')
    return not (
        relative.parts[0] in {'tests', 'docs'}
        or relative.suffix.lower() == '.md'
        or relative.name in {'.gitignore', '.gitkeep'}
        or relative.name.endswith('.example')
    )


def check(base, head):
    errors = []
    for name, source in POLLERS.items():
        changed = git('diff', '--name-only', '--no-renames', '-z', base, head, '--', f'scripts/{name}').split('\0')
        if not any(deployable(path, name) for path in changed if path):
            continue
        before, after = version(base, name, source), version(head, name, source)
        if after <= before:
            errors.append(f'{name}: increment {source} above {".".join(map(str, before))} for deployable changes')
        if name == 'nornir-netops':
            metadata = tomllib.loads(git('show', f'{head}:scripts/{name}/pyproject.toml'))
            if ('version' not in metadata['project'].get('dynamic', []) or
                    metadata.get('tool', {}).get('setuptools', {}).get('dynamic', {}).get('version') !=
                    {'attr': 'netops.__version__'}):
                errors.append('nornir-netops: package version must read netops.__version__')
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', required=True, help='Current main commit or ref')
    parser.add_argument('--head', default='HEAD', help='Candidate commit or ref (default: HEAD)')
    args = parser.parse_args()
    try:
        errors = check(args.base, args.head)
    except (ValueError, KeyError, SyntaxError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f'Cannot verify poller versions: {exc}\n')
    if errors:
        parser.exit(1, '\n'.join(errors) + '\n')
    print('Poller release versions verified.')


if __name__ == '__main__':
    main()
