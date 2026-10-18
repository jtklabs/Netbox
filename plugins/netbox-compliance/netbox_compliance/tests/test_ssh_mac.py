from io import StringIO

from dcim.models import Platform
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from netbox_compliance.definitions import parse_definition
from netbox_compliance.management.commands.create_ssh_mac_standard import DEFINITION, NAME
from netbox_compliance.models import ConfigStandard


class SSHMACStandardTest(TestCase):
    def test_definition_validation(self):
        self.assertEqual(parse_definition(DEFINITION)['ssh_mac']['ios_scope'], 'both')
        for settings in ('macs: []', 'macs: hmac-sha2-512', 'macs: [hmac-sha1]',
                         'macs: [hmac-sha2-512, hmac-sha1]', 'ios_scope: invalid',
                         'ios_scope: []', 'asa_integrity: low', 'password: secret'):
            with self.subTest(settings=settings), self.assertRaises(ValidationError):
                parse_definition('ssh_mac:\n  ' + settings)

    def test_seed_is_scoped_audit_only_and_idempotent(self):
        ios = Platform.objects.create(name='SSH test IOS', slug='cisco-ios')
        other = Platform.objects.create(name='SSH test Linux', slug='ssh-test-linux')
        call_command('create_ssh_mac_standard', stdout=StringIO())
        standard = ConfigStandard.objects.get(name=NAME)
        self.assertEqual(set(standard.platforms.all()), {ios})
        self.assertNotIn(other, standard.platforms.all())
        self.assertFalse(standard.auto_remediable)
        self.assertFalse(standard.allow_enforce)
        standard.definition_yaml = DEFINITION.replace('both', 'client')
        standard.save()
        revision = standard.revision
        call_command('create_ssh_mac_standard', stdout=StringIO())
        standard.refresh_from_db()
        self.assertEqual(standard.revision, revision)
        self.assertIn('ios_scope: client', standard.definition_yaml)

    def test_no_platform_never_creates_global_policy(self):
        with self.assertRaises(CommandError):
            call_command('create_ssh_mac_standard', platform=['missing'], stdout=StringIO())
        self.assertFalse(ConfigStandard.objects.filter(name=NAME).exists())

    def test_dry_run_does_not_create_and_explicit_scope_works(self):
        platform = Platform.objects.create(name='SSH custom IOS', slug='ssh-custom-ios')
        call_command('create_ssh_mac_standard', platform=[platform.slug], dry_run=True, stdout=StringIO())
        self.assertFalse(ConfigStandard.objects.filter(name=NAME).exists())
        call_command('create_ssh_mac_standard', platform=[platform.name], ios_scope='client', stdout=StringIO())
        standard = ConfigStandard.objects.get(name=NAME)
        self.assertIn('ios_scope: client', standard.definition_yaml)
        self.assertEqual(list(standard.platforms.all()), [platform])
