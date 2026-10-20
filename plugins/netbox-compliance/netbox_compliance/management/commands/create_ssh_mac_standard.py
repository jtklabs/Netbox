"""Seed the SSH MAC standard without changing existing operational policy."""
from dcim.models import Platform
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from netbox_compliance.models import ConfigStandard

NAME = 'SSH MAC integrity'
PLATFORMS = {
    'ios', 'ios-xe', 'iosxe', 'cisco-ios', 'cisco-ios-xe', 'cisco-iosxe',
    'cisco_ios', 'cisco_xe', 'cisco_ios_xe', 'cisco_iosxe',
    'eos', 'arista-eos', 'arista_eos', 'asa', 'cisco-asa', 'cisco_asa',
    'junos', 'juniper-junos', 'juniper_junos', 'juniper-srx', 'juniper-mx',
}
DEFINITION = '''ssh_mac:
  ios_scope: both
  platforms:
    cisco_ios:
      macs: [hmac-sha2-256, hmac-sha2-512]
    arista_eos:
      macs: [hmac-sha2-256, hmac-sha2-512]
    cisco_asa:
      macs: [hmac-sha2-256]
    juniper_junos:
      macs: [hmac-sha2-256, hmac-sha2-512]
'''


class Command(BaseCommand):
    help = 'Create the platform-scoped SSH MAC integrity standard (audit-only initially).'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true')
        parser.add_argument('--platform', action='append', default=[],
                            help='Exact platform name or slug (repeatable); otherwise detect supported platforms')
        parser.add_argument('--ios-scope', choices=('both', 'client', 'server'), default='both')

    @transaction.atomic
    def handle(self, *args, **options):
        if ConfigStandard.objects.filter(name=NAME).exists():
            self.stdout.write('exists   ' + NAME + ' (unchanged)')
            return
        platforms = list(Platform.objects.all())
        if options['platform']:
            selected = []
            for name in options['platform']:
                matches = [p for p in platforms if name.lower() in (p.name.lower(), p.slug.lower())]
                if len(matches) != 1:
                    raise CommandError(f'Platform {name!r} must identify exactly one existing platform.')
                selected.extend(matches)
        else:
            selected = [p for p in platforms if p.slug.lower() in PLATFORMS
                        or p.name.lower().replace(' ', '-') in PLATFORMS
                        or getattr(p, 'napalm_driver', '') in ('ios', 'eos', 'junos')]
        if not selected:
            raise CommandError('No supported platforms found. Use --platform with an existing platform; refusing fleet-wide scope.')
        standard = ConfigStandard(
            name=NAME, check_type='netops', description='SSH MAC integrity policy for IOS/IOS-XE, EOS, ASA and Junos',
            definition_yaml=DEFINITION.replace('ios_scope: both', 'ios_scope: ' + options['ios_scope']),
            auto_remediable=False, allow_enforce=False,
            remediation_notes=(
                'IOS/IOS-XE: client and server MAC lists by default; EOS/Junos: SSH MAC list. '
                'Edit the YAML to choose allowed MACs per platform; ASA lists use custom integrity. '
                'Encryption and key exchange are separate. '
                'Audit first and check peer compatibility. To apply, enable automatic remediation '
                'and Allow enforce, then use replace mode. Add mode only reports drift.'),
        )
        standard.full_clean(exclude=['platforms', 'roles', 'sites', 'device_tags'])
        if not options['dry_run']:
            standard.save()
            standard.platforms.set(selected)
        self.stdout.write(('would create   ' if options['dry_run'] else 'created   ') + NAME)
        self.stdout.write('Platforms: ' + ', '.join(p.name for p in selected))
        self.stdout.write('Audit-only; automatic remediation and enforce are off.')
