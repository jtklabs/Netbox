from django.core.management.base import BaseCommand

from netbox_refresh.cisco import CiscoEoxError
from netbox_refresh.sync import sync


class Command(BaseCommand):
    help = 'Populate hardware lifecycle records from the Cisco EoX API.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='report what would change without writing')
        parser.add_argument('--force', action='store_true',
                            help='also overwrite records marked as manually maintained')
        parser.add_argument('--limit', type=int,
                            help='only process the first N device/module types')

    def handle(self, *args, **options):
        try:
            summary = sync(
                dry_run=options['dry_run'],
                force=options['force'],
                limit=options.get('limit'),
                logger_fn=lambda msg: self.stdout.write('  %s' % msg),
            )
        except CiscoEoxError as exc:
            self.stderr.write(self.style.ERROR(str(exc)))
            return

        self.stdout.write('')
        for key in ('types', 'pids', 'created', 'updated', 'no_data',
                    'skipped_manual', 'replacements_linked', 'errors'):
            self.stdout.write('  %-20s %s' % (key, summary.get(key, 0)))
        if options['dry_run']:
            self.stdout.write(self.style.WARNING('dry run — nothing was written'))
