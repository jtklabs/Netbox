from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from tenancy.models import Tenant

from netbox_compliance.models import ConfigCompliance, ConfigStandard


class ComplianceReportTenantTest(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_superuser('report-admin', 'report@example.test', 'test-only')
        cls.tenants = [Tenant.objects.create(name=name, slug=name) for name in ('tenant-a', 'tenant-b')]
        site = Site.objects.create(name='Report lab', slug='report-lab')
        other_site = Site.objects.create(name='Other lab', slug='other-lab')
        manufacturer = Manufacturer.objects.create(name='Report vendor', slug='report-vendor')
        dtype = DeviceType.objects.create(model='Report model', slug='report-model', manufacturer=manufacturer)
        role = DeviceRole.objects.create(name='Report role', slug='report-role')
        cls.devices = [Device.objects.create(name=f'report-sw{index}', tenant=tenant, site=location,
                                            role=role, device_type=dtype)
                       for index, (tenant, location) in enumerate((
                           (cls.tenants[0], site), (cls.tenants[1], site), (None, site),
                           (cls.tenants[0], other_site)), start=1)]
        cls.standard = ConfigStandard.objects.create(name='NTP', match_pattern='^ntp server',
                                                     expected_entries=['ntp server 192.0.2.1'])
        for device, result in zip(cls.devices, ('compliant', 'non-compliant')):
            ConfigCompliance.objects.create(device=device, standard=cls.standard, result=result,
                                            standard_revision=cls.standard.revisions.first(),
                                            last_checked=timezone.now())

    def report(self, params=None):
        self.client.force_login(self.user)
        response = self.client.get(reverse('plugins:netbox_compliance:compliance_report'), params or {})
        self.assertEqual(response.status_code, 200)
        return response

    def test_tenant_scopes_rows_summary_and_rollup_including_unchecked_devices(self):
        response = self.report({'tenant': [self.tenants[0].pk]})
        self.assertContains(response, 'name="tenant"')
        self.assertContains(response, '>report-sw1<')
        self.assertContains(response, '>report-sw4<')
        self.assertNotContains(response, '>report-sw2<')
        self.assertNotContains(response, '>report-sw3<')
        self.assertEqual(response.context['device_count'], 2)
        self.assertEqual(response.context['total_count'], 2)
        counts = {row['status']: row['count'] for row in response.context['summary']}
        self.assertEqual(counts['compliant'], 1)
        self.assertEqual(counts['non-compliant'], 0)
        rollup = list(response.context['rollup_table'].data)[0]
        self.assertEqual((rollup['in_scope'], rollup['checked'], rollup['coverage']), (2, 1, 50.0))

    def test_multiple_tenants_and_empty_selection(self):
        response = self.report({'tenant': [tenant.pk for tenant in self.tenants]})
        self.assertEqual(response.context['device_count'], 3)
        self.assertNotContains(response, '>report-sw3<')
        response = self.report()
        self.assertEqual(response.context['device_count'], 4)
        self.assertContains(response, '>report-sw3<')

    def test_tenant_combines_with_site_and_status(self):
        response = self.report({'tenant': [self.tenants[0].pk], 'site': [self.devices[0].site_id],
                                'status': ['non-compliant']})
        self.assertEqual(response.context['device_count'], 1)
        self.assertEqual(response.context['total_count'], 1)
        self.assertEqual(response.context['row_count'], 0)
