"""Seed the five configuration standards this plugin ships with.

Idempotent and non-destructive: a standard whose name already exists is left
exactly as it is, because a standard is an operational document and somebody
may well have adjusted it. Re-running after an upgrade adds anything new and
touches nothing else.
"""

from dcim.models import Platform
from django.core.management.base import BaseCommand
from django.db import transaction

from netbox_compliance.models import ConfigStandard
from netbox_compliance.standards_library import DEFAULT_STANDARDS, IOS_PLATFORM_HINTS


class Command(BaseCommand):
    help = 'Create the default Cisco IOS configuration standards, if they do not exist.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='report what would be created without writing')
        parser.add_argument('--platform', action='append', default=[],
                            help='platform name or slug to scope the standards to '
                                 '(repeatable; default: auto-detect IOS platforms)')
        parser.add_argument('--local-user', action='append', default=[],
                            help='local account for the "Local users" standard, as '
                                 'name or name:privilege (repeatable)')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        platforms = self._platforms(options['platform'])
        if platforms:
            self.stdout.write('Scoping to platform(s): %s'
                              % ', '.join(p.name for p in platforms))
        else:
            self.stdout.write(self.style.WARNING(
                'No matching platform found, so these standards will be scoped to '
                'EVERY platform. Check the scope on each before running a check.'
            ))

        users = self._local_users(options['local_user'])
        created, skipped = [], []

        with transaction.atomic():
            for spec in DEFAULT_STANDARDS:
                if ConfigStandard.objects.filter(name=spec['name']).exists():
                    skipped.append(spec['name'])
                    continue
                if dry_run:
                    created.append(spec['name'])
                    continue
                fields = {k: v for k, v in spec.items()}
                if users and fields['name'] == 'Local users':
                    fields['expected_entries'] = users
                standard = ConfigStandard(**fields)
                standard.full_clean(exclude=['platforms', 'roles', 'sites', 'device_tags'])
                standard.save()
                if platforms:
                    standard.platforms.set(platforms)
                created.append(spec['name'])

            if dry_run:
                transaction.set_rollback(True)

        for name in created:
            self.stdout.write(self.style.SUCCESS('  created  %s' % name))
        for name in skipped:
            self.stdout.write('  exists   %s' % name)

        if dry_run:
            self.stdout.write(self.style.WARNING('dry run — nothing was written'))
            return

        # Two things that will bite an operator who stops reading here.
        if 'Local users' in created and not users:
            self.stdout.write(self.style.WARNING(
                '\n"Local users" was seeded with one example account (netops, '
                'privilege 15). That is almost certainly not your account list. '
                'Edit it before checking anything, or every device will report '
                'the wrong drift.'
            ))
        self.stdout.write(self.style.WARNING(
            '\nEvery standard was created with enforce OFF, including the two whose '
            'fix is a removal. Enforce is opt-in per standard: until you tick '
            '"Allow enforce" on one, no run will remove configuration for it.'
        ))

    # ------------------------------------------------------------------ #
    def _platforms(self, requested):
        """Named platforms if given, else whatever looks like Cisco IOS.

        Auto-detection is a convenience with a loud fallback, not a guess we
        act on silently — if it finds nothing, the standards are created
        fleet-wide and the command says so.
        """
        if requested:
            found = []
            for name in requested:
                platform = Platform.objects.filter(name__iexact=name).first() \
                    or Platform.objects.filter(slug__iexact=name).first()
                if platform is None:
                    self.stderr.write(self.style.ERROR(
                        'No platform called "%s" — create it first, or leave '
                        '--platform off.' % name
                    ))
                    continue
                found.append(platform)
            return found

        matches = []
        for platform in Platform.objects.all():
            haystack = '%s %s' % (platform.name.lower(), (platform.slug or '').lower())
            if any(hint in haystack for hint in IOS_PLATFORM_HINTS):
                matches.append(platform)
        return matches

    def _local_users(self, requested):
        """`netops` or `netops:15` -> the expected_entries shape."""
        entries = []
        for item in requested:
            name, _, privilege = item.partition(':')
            name = name.strip()
            if not name:
                continue
            entries.append({
                'key': name,
                'vars': {'privilege': (privilege or '15').strip()},
            })
        return entries
