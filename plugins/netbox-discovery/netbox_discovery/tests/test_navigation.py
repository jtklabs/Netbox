from django.apps import apps
from django.test import SimpleTestCase
from django.urls import reverse

from netbox_discovery.navigation import menu


class DeviceOperationsNamingTest(SimpleTestCase):
    def test_display_name_covers_the_full_plugin(self):
        self.assertEqual(apps.get_app_config('netbox_discovery').verbose_name, 'Device Operations')
        self.assertEqual(menu.label, 'Device Operations')

    def test_existing_integration_names_and_urls_are_preserved(self):
        config = apps.get_app_config('netbox_discovery')
        self.assertEqual(config.name, 'netbox_discovery')
        self.assertEqual(config.base_url, 'discovery')
        self.assertEqual(reverse('plugins:netbox_discovery:jobprofile_list'), '/plugins/discovery/job-profiles/')
