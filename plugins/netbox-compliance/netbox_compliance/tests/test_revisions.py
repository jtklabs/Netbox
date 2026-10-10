from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import ObjectPermission

from netbox_compliance.definitions import SnapshotResolver, parse_definition, snapshot_for_device
from netbox_compliance.filtersets import ConfigComplianceFilterSet
from netbox_compliance.models import ConfigCompliance, ConfigStandard
from .test_device_grid import GridTest
from .factories import feature_standards


class RevisionTest(GridTest):
    def test_batch_snapshots_are_isolated_and_next_batch_reads_new_revisions(self):
        standard = feature_standards()[0]
        device = self.devices['sw1']
        resolver = SnapshotResolver(self.user)
        with self.assertNumQueries(0):
            first = snapshot_for_device(self.user, device, ['ntp'], 'add', resolver=resolver)
            first['document']['ntp']['servers'].append('192.0.2.99')
            second = snapshot_for_device(self.user, device, ['ntp'], 'add', resolver=resolver)
        self.assertEqual(second['document']['ntp']['servers'], ['192.0.2.10'])
        standard.definition_yaml = 'ntp:\n  servers: [192.0.2.11]\n'
        standard.save()
        fresh = snapshot_for_device(self.user, device, ['ntp'], 'add')
        self.assertEqual(fresh['document']['ntp']['servers'], ['192.0.2.11'])
        self.assertEqual(fresh['revisions'][0]['revision'], standard.revision)
        hidden = SnapshotResolver(get_user_model().objects.create_user('hidden-batch-standards'))
        with self.assertRaises(ValidationError):
            snapshot_for_device(self.user, device, ['ntp'], 'add', resolver=hidden)

    def test_revisions_preserve_definition_and_actor_and_ignore_noop(self):
        standard = feature_standards()[0]
        original = standard.revisions.first()
        with patch('netbox_compliance.revisions.current_request') as request:
            request.get.return_value = SimpleNamespace(user=self.user)
            standard.definition_yaml = 'ntp:\n  servers: [192.0.2.11]\n'
            standard.save()
        self.assertEqual(standard.revision, 2)
        self.assertEqual(standard.revisions.first().author, self.user.username)
        original.refresh_from_db()
        self.assertEqual(original.definition['settings']['ntp']['servers'], ['192.0.2.10'])
        standard.save()
        self.assertEqual(standard.revisions.count(), 2)
        standard.sites.add(self.devices['sw1'].site)
        self.assertEqual(standard.revision, 3)
        with self.assertRaises(ValidationError):
            original.save()

    def test_yaml_rejects_duplicates_aliases_unknown_sections_and_bad_shapes(self):
        for text in ('ntp: []', 'ntp: {}', 'ntp: {servers: [], servers: []}',
                     'netbox: {url: http://elsewhere}', 'ntp: {typo: true}',
                     'ntp: &loop {servers: [*loop]}', 'ntp: {}\nsyslog: {}'):
            with self.subTest(text=text), self.assertRaises(ValidationError):
                parse_definition(text)

    def test_scope_changes_cannot_reset_revision_on_a_stale_instance(self):
        standard = feature_standards()[0]
        other = ConfigStandard.objects.get(pk=standard.pk)
        other.sites.add(self.devices['sw1'].site)
        standard.save()
        standard.refresh_from_db()
        self.assertEqual(standard.revision, 2)

    def test_aruba_ntp_settings_are_versionable_without_credentials(self):
        document = parse_definition('ntp:\n  servers: [192.0.2.10]\n  aruba:\n    conductor: 192.0.2.254\n    exclusive_change: false\n')
        self.assertEqual(document['ntp']['aruba']['conductor'], '192.0.2.254')
        for options in ('{password: secret}', '{exclusive_change: "false"}',
                        '{timeout: 0}', '{port: true}', '[]'):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                parse_definition('ntp:\n  servers: [192.0.2.10]\n  aruba: ' + options)

    def test_clearpass_ntp_cluster_scope_is_versioned(self):
        standard = feature_standards()[0]
        standard.definition_yaml = ('ntp:\n  servers: [192.0.2.10]\n'
                                    '  clearpass:\n    cluster_members: [192.0.2.1, 192.0.2.2]\n')
        standard.save()
        settings = standard.revisions.get(number=standard.revision).definition['settings']
        self.assertEqual(settings['ntp']['clearpass']['cluster_members'], ['192.0.2.1', '192.0.2.2'])
        for options in ('{password: secret}', '{cluster_members: "192.0.2.1"}',
                        '{cluster_members: [192.0.2.1, 192.0.2.1]}',
                        '{cluster_members: ["2001:db8::1"]}', '{cluster_members: [123]}', '[]'):
            with self.subTest(options=options), self.assertRaises(ValidationError):
                parse_definition('ntp:\n  servers: [192.0.2.10]\n  clearpass: ' + options)

    def test_version_change_and_age_remove_pass_from_reporting(self):
        record = ConfigCompliance.objects.get(device=self.devices['sw1'], standard=self.ntp)
        self.assertEqual(record.status, 'compliant')
        self.ntp.expected_entries = ['ntp server 192.0.2.99']
        self.ntp.save()
        record.refresh_from_db()
        self.assertEqual(record.status, 'outdated')
        self.assertTrue(ConfigComplianceFilterSet({'status': ['outdated']}).qs.filter(pk=record.pk).exists())
        columns, cells, _ = self.grid(code_columns=False)
        self.assertIs(cells['sw1']['NTP servers']['ok'], False)
        self.assertEqual(next(c for c in columns if c['label'] == 'NTP servers')['passed'], 0)
        record.standard_revision = self.ntp.revisions.first()
        record.last_checked = timezone.now() - timedelta(days=365)
        record.save()
        self.assertEqual(record.status, 'stale')

    def test_api_reports_exact_revision_and_does_not_invent_missing_revision(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse('plugins-api:netbox_compliance-api:configcompliance-report')
        data = {'device_id': self.devices['sw3'].pk, 'standard_id': self.ntp.pk,
                'revision': self.ntp.revision, 'result': 'compliant'}
        response = client.post(url, data, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        record = ConfigCompliance.objects.get(device=self.devices['sw3'], standard=self.ntp)
        self.assertEqual(record.checked_revision, self.ntp.revision)
        response = client.post(url, {**data, 'revision': 99999}, format='json')
        self.assertEqual(response.data['summary'], {'error': 1})
        client.post(url, {k: v for k, v in data.items() if k != 'revision'}, format='json')
        record.refresh_from_db()
        self.assertEqual(record.status, 'outdated')
        response = client.get(reverse('plugins-api:netbox_compliance-api:configstandard-revisions', args=[self.ntp.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data[0]['number'], self.ntp.revision)

    def test_yaml_form_api_and_history_pages(self):
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse('plugins:netbox_compliance:configstandard_add'), {
            'name': 'Regional NTP', 'check_type': 'netops', 'definition_yaml': 'ntp:\n  servers: [192.0.2.10]',
            'valid_from': timezone.localdate().isoformat(), 'auto_remediable': 'on',
        })
        self.assertEqual(response.status_code, 302, response.content[:2000])
        standard = ConfigStandard.objects.get(name='Regional NTP')
        response = client.get(standard.get_absolute_url())
        self.assertContains(response, 'Revision History')
        response = client.get(reverse('plugins:netbox_compliance:standard_revision', args=[standard.pk, standard.revision]))
        self.assertContains(response, '192.0.2.10')
        api = APIClient()
        api.force_authenticate(self.user)
        response = api.patch(reverse('plugins-api:netbox_compliance-api:configstandard-detail', args=[standard.pk]),
                             {'definition_yaml': 'ntp:\n  servers: [192.0.2.11]'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['revision'], standard.revision + 1)

    def test_scope_ambiguity_permissions_and_write_policy_fail_closed(self):
        ntp, syslog = feature_standards()
        snapshot = snapshot_for_device(self.user, self.devices['sw1'], ['ntp'], 'add')
        self.assertEqual(set(snapshot['document']), {'ntp'})
        self.assertEqual(snapshot['revisions'][0]['revision'], ntp.revision)
        ntp.auto_remediable = False
        ntp.allow_enforce = False
        ntp.save()
        with self.assertRaisesRegex(ValidationError, 'does not permit'):
            snapshot_for_device(self.user, self.devices['sw1'], ['ntp'], 'add')
        with self.assertRaisesRegex(ValidationError, 'no accessible'):
            snapshot_for_device(get_user_model().objects.create_user('no-standards'), self.devices['sw1'], ['ntp'], 'add')

    def test_report_and_history_respect_object_permissions(self):
        user = get_user_model().objects.create_user('scoped-reader')
        for model, constraints in ((type(self.devices['sw1']), {'id': self.devices['sw1'].pk}),
                                   (ConfigStandard, {'id': self.ntp.pk}),
                                   (ConfigCompliance, {'device_id': self.devices['sw1'].pk})):
            perm = ObjectPermission.objects.create(name=f'review-{model.__name__}', actions=['view'], constraints=constraints)
            perm.object_types.add(ContentType.objects.get_for_model(model))
            perm.users.add(user)
        client = Client()
        client.force_login(user)
        response = client.get(reverse('plugins:netbox_compliance:compliance_report'))
        self.assertContains(response, '>sw1<')
        self.assertNotContains(response, '>sw2<')
        self.assertNotContains(response, 'Syslog host')
        response = client.get(reverse('plugins:netbox_compliance:standard_revision', args=[self.syslog.pk, self.syslog.revision]))
        self.assertEqual(response.status_code, 404)
