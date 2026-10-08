from datetime import timedelta

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site, VirtualChassis
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from users.models import ObjectPermission
from rest_framework.test import APIClient

from netbox_compliance.grid import config_cells
from netbox_compliance.models import ConfigCompliance, ConfigStandard
from netbox_compliance.scoping import device_standard_rows, standard_rollup
from netbox_compliance.template_content import DeviceComplianceCard


class StackComplianceTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('stack-admin', 'test@example.test', 'test-only')
        site = Site.objects.create(name='Stack lab', slug='stack-lab')
        role = DeviceRole.objects.create(name='Switch', slug='switch')
        vendor = Manufacturer.objects.create(name='Vendor', slug='vendor')
        model = DeviceType.objects.create(model='Switch', slug='switch', manufacturer=vendor)
        self.master, self.member = [Device.objects.create(name=name, site=site, role=role, device_type=model)
                                   for name in ('master', 'member')]
        self.stack = VirtualChassis.objects.create(name='Stack', master=self.master)
        for position, device in enumerate((self.master, self.member), 1):
            device.virtual_chassis = self.stack
            device.vc_position = position
            device.save()
        self.standard = ConfigStandard.objects.create(name='NTP', match_pattern='^ntp server',
                                                       expected_entries=['ntp server 192.0.2.1'])
        self.record = ConfigCompliance.objects.create(device=self.master, standard=self.standard,
            result='non-compliant', standard_revision=self.standard.revisions.first(),
            last_checked=timezone.now(), findings={'missing': ['ntp server 192.0.2.1']})

    def rows(self, user=None):
        return device_standard_rows([self.master, self.member], user=user or self.user)

    def test_member_inherits_live_master_verdict_and_evidence(self):
        rows = self.rows()
        self.assertEqual([r['status'] for r in rows], ['non-compliant', 'non-compliant'])
        self.assertEqual(rows[1]['source_device'], self.master)
        self.assertTrue(rows[1]['inherited'])
        self.assertFalse(rows[0]['inherited'])
        self.assertEqual(rows[1]['record'].device_id, self.master.pk)
        for key in ('record', 'findings', 'checked_revision', 'last_checked'):
            self.assertEqual(rows[0][key], rows[1][key])
        self.assertEqual(standard_rollup(rows)[0]['non_compliant'], 2)
        ConfigCompliance.objects.filter(pk=self.record.pk).update(result='compliant')
        self.assertEqual([r['status'] for r in self.rows()], ['compliant', 'compliant'])
        self.assertEqual(ConfigCompliance.objects.count(), 1)

    def test_old_member_result_never_overrides_current_master(self):
        ConfigCompliance.objects.create(device=self.member, standard=self.standard, result='compliant',
            standard_revision=self.standard.revisions.first(), last_checked=timezone.now())
        self.assertEqual(self.rows()[1]['status'], 'non-compliant')
        self.stack.master = self.member
        self.stack.save()
        self.assertEqual([r['status'] for r in self.rows()], ['compliant', 'compliant'])

    def test_unchecked_and_missing_master_never_become_passes(self):
        self.record.delete()
        self.assertEqual(self.rows()[1]['status'], 'unknown')
        self.stack.master = None
        self.stack.save()
        row = self.rows()[1]
        self.assertEqual(row['status'], 'unknown')
        self.assertIsNone(row['source_device'])

    def test_member_uses_masters_standard_scope(self):
        self.master.platform = Platform.objects.create(name='IOS', slug='ios')
        self.master.save()
        self.standard.platforms.add(self.master.platform)
        rows = self.rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1]['device'], self.member)
        self.assertEqual(rows[1]['record'].pk, self.record.pk)

    def test_hidden_master_record_is_not_inherited(self):
        user = get_user_model().objects.create_user('device-only-reader')
        permission = ObjectPermission.objects.create(name='Devices', actions=['view'])
        permission.object_types.add(ContentType.objects.get_for_model(Device))
        permission.users.add(user)
        row = device_standard_rows([self.member], user=user)[0]
        self.assertEqual(row['source_device'], self.master)
        self.assertIsNone(row['record'])
        self.assertEqual(row['status'], 'unknown')

    def test_hidden_master_does_not_leak_verdict(self):
        user = get_user_model().objects.create_user('member-reader')
        for model, constraints in ((Device, {'pk': self.member.pk}), (ConfigCompliance, {})):
            permission = ObjectPermission.objects.create(name=model.__name__, actions=['view'], constraints=constraints)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        row = device_standard_rows([self.member], user=user)[0]
        self.assertEqual(row['status'], 'unknown')
        self.assertIsNone(row['record'])
        self.assertIsNone(row['source_device'])

    def test_staleness_and_revision_are_inherited(self):
        ConfigCompliance.objects.filter(pk=self.record.pk).update(
            result='compliant', last_checked=timezone.now() - timedelta(days=365))
        rows = self.rows()
        self.assertEqual(rows[0]['status'], rows[1]['status'])
        self.assertTrue(rows[1]['is_stale'])
        self.assertEqual(rows[1]['status'], 'stale')

    def test_report_grid_and_device_card_identify_master(self):
        cells, _ = config_cells([self.member], user=self.user)
        cell = cells[(self.member.pk, self.standard.pk)]
        self.assertIn('Inherited from stack master master', cell['title'])
        self.assertEqual(cell['url'], self.record.get_absolute_url())
        card = DeviceComplianceCard({'object': self.member})
        self.assertEqual(card._rows(self.member)[0]['record'].pk, self.record.pk)
        self.client.force_login(self.user)
        response = self.client.get(reverse('plugins:netbox_compliance:compliance_report'))
        self.assertContains(response, 'Inherited from stack master master')
        response = self.client.get(self.member.get_absolute_url())
        self.assertContains(response, 'Inherited from stack master master')

    def test_effective_api_includes_source_and_evidence(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse('plugins-api:netbox_compliance-api:configcompliance-effective')
        response = client.get(url, {'device_id': self.member.pk, 'limit': 1})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['count'], 1)
        row = response.data['results'][0]
        self.assertEqual(row['device_id'], self.member.pk)
        check = row['standards'][0]
        self.assertEqual(check['source_device_id'], self.master.pk)
        self.assertEqual(check['status'], 'non-compliant')
        self.assertTrue(check['inherited'])
        self.assertEqual(check['record_id'], self.record.pk)
        self.assertEqual(client.get(url, {'siet_id': 1}).status_code, 400)
