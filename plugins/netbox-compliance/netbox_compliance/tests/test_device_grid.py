"""Device grid: one row per device, a column per check, code and configuration together."""
from datetime import timedelta

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Platform, Site
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from netbox_discovery.models import DiscoveryPoller, UpgradeJob
from netbox_refresh.models import DeviceSoftware, SoftwareStandard, SoftwareVersion

from netbox_compliance import grid
from netbox_compliance.models import ConfigCompliance, ConfigStandard


class GridTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('grid-admin', 'g@example.test', 'test-only')
        site = Site.objects.create(name='Grid lab', slug='grid-lab')
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        dtype = DeviceType.objects.create(model='C9300-48P', slug='c9300-48p', manufacturer=manufacturer)
        self.ios = Platform.objects.create(name='IOS XE', slug='ios-xe')
        other = Platform.objects.create(name='EOS', slug='eos')
        role = DeviceRole.objects.create(name='Access', slug='access')
        self.devices = {name: Device.objects.create(name=name, site=site, role=role, device_type=dtype,
                                                    platform=self.ios if name != 'eos1' else other)
                        for name in ('sw1', 'sw2', 'sw3', 'eos1')}
        self.ntp = ConfigStandard.objects.create(name='NTP servers', match_pattern='^ntp server',
                                                 expected_entries=['ntp server 192.0.2.1'])
        self.ntp.platforms.set([self.ios])
        self.syslog = ConfigStandard.objects.create(name='Syslog host', match_pattern='^logging host',
                                                    expected_entries=['logging host 192.0.2.2'])
        now = timezone.now()
        for name, standard, result in (('sw1', self.ntp, 'compliant'), ('sw1', self.syslog, 'compliant'),
                                       ('sw2', self.ntp, 'non-compliant'), ('sw2', self.syslog, 'error')):
            ConfigCompliance.objects.create(device=self.devices[name], standard=standard, result=result,
                                            last_checked=now)

        old = SoftwareVersion.objects.create(platform=self.ios, version='17.9.4a')
        self.target = SoftwareVersion.objects.create(
            platform=self.ios, version='17.12.4', image_filename='cat9k_iosxe.17.12.04.SPA.bin',
            image_url='http://images.example.test/cat9k_iosxe.17.12.04.SPA.bin', checksum_type='md5',
            checksum='a' * 32)
        standard = SoftwareStandard.objects.create(preferred_version=self.target)
        standard.platforms.set([self.ios])
        standard.approved_versions.set([self.target])
        DeviceSoftware.objects.create(device=self.devices['sw1'], software_version=self.target)
        DeviceSoftware.objects.create(device=self.devices['sw2'], software_version=old)
        DeviceSoftware.objects.create(device=self.devices['sw3'], software_version=old)
        poller = DiscoveryPoller.objects.create(name='lab')
        for name, status in (('sw2', 'failed'), ('sw3', 'completed')):
            UpgradeJob.objects.create(device=self.devices[name], device_name=name, address='192.0.2.9', poller=poller,
                                      profile={'image': self.target.image_filename}, operation='stage',
                                      status=status, scheduled_at=now, start_before=now + timedelta(hours=1))

    def grid(self, **kwargs):
        columns, rows = grid.build(Device.objects.order_by('name'), **kwargs)
        labels = [column['label'] for column in columns]
        return columns, {row['device'].name: dict(zip(labels, row['cells'])) for row in rows}, rows

    def test_every_device_gets_a_row_with_code_and_config_columns(self):
        columns, cells, _ = self.grid()
        self.assertEqual([c['label'] for c in columns], ['Running code', 'Code staged', 'NTP servers', 'Syslog host'])
        self.assertEqual(set(cells), {'sw1', 'sw2', 'sw3', 'eos1'})

    def test_cells_show_pass_fail_and_not_checked(self):
        _, cells, _ = self.grid()
        self.assertIs(cells['sw1']['Running code']['ok'], True)
        self.assertEqual(cells['sw1']['Code staged']['label'], 'Running')
        self.assertIs(cells['sw1']['NTP servers']['ok'], True)
        self.assertIs(cells['sw2']['Running code']['ok'], False)
        self.assertEqual((cells['sw2']['Code staged']['label'], cells['sw2']['Code staged']['ok']), ('Failed', False))
        self.assertIs(cells['sw2']['NTP servers']['ok'], False)
        self.assertEqual((cells['sw2']['Syslog host']['label'], cells['sw2']['Syslog host']['ok']), ('Check failed', False))
        self.assertEqual(cells['sw3']['Code staged']['label'], 'Staged')
        # Never checked is shown, and is not a pass.
        self.assertEqual((cells['sw3']['NTP servers']['label'], cells['sw3']['NTP servers']['ok']), ('Not checked', None))

    def test_checks_that_do_not_apply_are_empty(self):
        _, cells, _ = self.grid()
        self.assertIsNone(cells['eos1']['NTP servers'])
        self.assertIsNone(cells['eos1']['Running code'])
        self.assertIsNone(cells['eos1']['Code staged'])
        self.assertIsNotNone(cells['eos1']['Syslog host'])

    def test_counts_per_column_and_failures_per_device(self):
        columns, _, rows = self.grid()
        ntp = next(c for c in columns if c['label'] == 'NTP servers')
        self.assertEqual((ntp['passed'], ntp['failed'], ntp['other']), (1, 1, 1))
        self.assertEqual({row['device'].name: row['problems'] for row in rows},
                         {'sw1': 0, 'sw2': 4, 'sw3': 1, 'eos1': 0})

    def test_standard_filter_limits_config_columns(self):
        columns, _, _ = self.grid(standards=[self.syslog])
        self.assertEqual([c['label'] for c in columns], ['Running code', 'Code staged', 'Syslog host'])

    def test_page_and_csv(self):
        client = Client()
        client.force_login(self.user)
        url = reverse('plugins:netbox_compliance:device_grid')
        response = client.get(url, {'problems_only': 'on'})
        self.assertContains(response, 'sw2')
        self.assertNotContains(response, '>sw1<')
        response = client.get(url, {'export': 'csv'})
        lines = response.content.decode().splitlines()
        self.assertEqual(lines[0], 'Device,Site,Role,Platform,Failures,Running code,Code staged,NTP servers,Syslog host')
        self.assertIn('eos1,Grid lab,Access,EOS,0,n/a,n/a,n/a,Not checked', lines)
