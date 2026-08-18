"""The standards this plugin ships with, as data.

These five are the ones the operator asked for first. They live here rather
than in a fixture so the reasoning travels with them — particularly why two of
them look redundant and are not, and why one of them refuses to fix itself.

They are seeded, not enforced: `manage.py create_config_standards` creates them
if they are absent and leaves any existing standard of the same name alone. A
standard is an operational document, and having a management command silently
rewrite one somebody had adjusted would be its own kind of outage.

Every one is seeded with allow_enforce=False, including the two whose whole
purpose is a removal. Enforce is opt-in per standard by design, and "opt in"
has to mean a person went and did something, not that the seeding was
convenient enough to do it for them.
"""

from netbox_compliance.choices import ConfigCheckTypeChoices

__all__ = ('DEFAULT_STANDARDS', 'IOS_PLATFORM_HINTS')

# Platform names/slugs this seeder will attach if it finds them. Cisco classic
# IOS and IOS-XE take the same commands for all five of these, and an estate
# usually models them as two Platform objects.
IOS_PLATFORM_HINTS = ('ios', 'cisco-ios', 'ios-xe', 'iosxe', 'cisco-ios-xe')

DEFAULT_STANDARDS = [
    {
        'name': 'No HTTP server',
        'check_type': ConfigCheckTypeChoices.TYPE_ABSENT,
        'description': 'The IOS HTTP server must be off',
        'match_pattern': r'^ip http server\s*$',
        'expected_entries': [],
        'remove_template': 'no {line}',
        'auto_remediable': True,
        'remediation_notes': (
            'The cleartext HTTP management server. Nothing in this estate manages '
            'IOS over HTTP, so it is attack surface with no user. Note that the '
            'fix is a removal, which means it is only ever applied in enforce '
            'mode — an update run will report it and leave it alone.'
        ),
    },
    {
        'name': 'No HTTPS server',
        'check_type': ConfigCheckTypeChoices.TYPE_ABSENT,
        'description': 'The IOS HTTPS server must be off',
        'match_pattern': r'^ip http secure-server\s*$',
        'expected_entries': [],
        'remove_template': 'no {line}',
        'auto_remediable': True,
        'remediation_notes': (
            'A separate standard from the HTTP one because they are separate '
            'commands with separate defaults: turning off `ip http server` leaves '
            '`ip http secure-server` listening, which is the exact mistake this '
            'catches. Being encrypted does not make an unused management service '
            'worth running.'
        ),
    },
    {
        'name': 'Password encryption service',
        'check_type': ConfigCheckTypeChoices.TYPE_PRESENT,
        'description': 'service password-encryption must be configured',
        'match_pattern': r'^service password-encryption\s*$',
        'expected_entries': [{'key': 'service password-encryption', 'vars': {}}],
        'add_template': '{key}',
        'auto_remediable': True,
        'remediation_notes': (
            'Kept even though what it produces is weak — see "No type-7 passwords", '
            'which is a separate standard for exactly that reason. This one is a '
            'checkbox an auditor asks for and a shoulder-surfing defence; it is not '
            'protection, and the two standards must not be collapsed into one or '
            'passing the easy half will read as passing both.'
        ),
    },
    {
        'name': 'No type-7 passwords',
        'check_type': ConfigCheckTypeChoices.TYPE_ABSENT,
        'description': 'No reversibly-encoded (type 7) secrets anywhere in the config',
        # Matched with re.search against each line, so the leading (?:^|\s)
        # anchors on a token boundary rather than the start of the line — which
        # is what catches `username x privilege 15 password 7 ...`, an indented
        # `password 7 ...` inside a `line vty` block, and `key 7 ...` in a key
        # chain, all with one expression.
        'match_pattern': r'(?:^|\s)(?:password|key|key-string)\s+7\s+[0-9A-Fa-f]{4,}\b',
        'expected_entries': [],
        # No templates at all: there is nothing this may send.
        'auto_remediable': False,
        'remediation_notes': (
            'Audit only, deliberately. Type 7 is a Vigenere cipher with a published '
            'key — it is encoding, not encryption, and any of a dozen one-line '
            'scripts will reverse it. So a tool COULD decrypt each of these and '
            're-set it as a type 8/9 secret automatically. It will not: silently '
            'round-tripping production credentials through a script, writing them '
            'to memory and possibly to a log on the way, is not something to do '
            'because it happens to be technically possible.\n\n'
            'Fix each one by hand, with the plaintext you already hold:\n'
            '  enable secret <plaintext>                (replaces enable password)\n'
            '  username <name> secret <plaintext>       (replaces username ... password)\n'
            '  line vty 0 15 / no password / login local\n'
            'Type 8 or 9 is the target; type 5 is the floor. Then re-run the check.'
        ),
    },
    {
        'name': 'Local users',
        'check_type': ConfigCheckTypeChoices.TYPE_EXACT_SET,
        'description': 'Exactly the local accounts we say should exist',
        # `privilege` is captured as well as the key, and the checker needs it:
        # the guard that refuses to remove the last privilege-15 local account
        # reads it off the device's own lines. An account configured without an
        # explicit privilege is level 1, and the group is simply absent.
        'match_pattern': r'^username (?P<key>\S+)(?:\s+privilege\s+(?P<privilege>\d+))?',
        # Seeded with one example so the standard is valid on creation. It is
        # nearly certainly not your account list — edit it before checking
        # anything, and the seeder says so.
        'expected_entries': [{'key': 'netops', 'vars': {'privilege': '15'}}],
        # {secret} is not captured by the pattern and not in the entry vars, so
        # it is a runtime variable: the checker supplies it from its own
        # environment at the moment of the write. That is the whole reason
        # remediation is a template rather than a stored command — NetBox never
        # holds the credential.
        'add_template': 'username {key} privilege {privilege} secret {secret}',
        'remove_template': 'no username {key}',
        'auto_remediable': True,
        'remediation_notes': (
            'Update adds missing accounts; only enforce removes accounts that are '
            'not on the list, and enforce is off for this standard until somebody '
            'turns it on. Removing local accounts from a production switch is how '
            'people lock themselves out, so the checker refuses to remove the '
            'account it is logged in as and refuses to remove the last '
            'privilege-15 local account, whatever this standard says.'
        ),
    },
]
