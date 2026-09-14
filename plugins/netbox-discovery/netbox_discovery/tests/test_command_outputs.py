"""Show-command outputs: uploaded by a poller, stored as files, shown on the device page."""
import tempfile

from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from rest_framework.test import APIClient
from users.models import ObjectPermission

from netbox_discovery.models import CommandOutput

MEDIA = tempfile.mkdtemp(prefix='netbox-discovery-media-')


@override_settings(MEDIA_ROOT=MEDIA)
class CommandOutputTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser('collector', 'c@example.test', 'test-only')
        site = Site.objects.create(name='Collect lab', slug='collect-lab')
        dtype = DeviceType.objects.create(model='EX4300', slug='ex4300', manufacturer=Manufacturer.objects.create(name='Juniper', slug='juniper'))
        self.device = Device.objects.create(name='alp-bofw-b', site=site, role=DeviceRole.objects.create(name='fw', slug='fw'), device_type=dtype)
        self.api = APIClient()
        self.api.force_authenticate(self.user)

    def upload(self, command='show version', content='Junos: 21.4R3\n', **extra):
        payload = {'device': self.device.pk, 'poller': 'checkmk-us', 'platform': 'juniper_junos', 'command': command,
                   'filename': f'alp-bofw-b_{command.replace(" ", "_")}.txt', 'content': content, **extra}
        return self.api.post('/api/plugins/discovery/command-outputs/', payload, format='json')

    def test_upload_stores_a_file_and_replaces_it_on_the_next_collection(self):
        response = self.upload()
        self.assertEqual(response.status_code, 201, response.data)
        output = CommandOutput.objects.get()
        self.assertEqual((output.filename, output.size, output.ok, output.poller.name), ('alp-bofw-b_show_version.txt', 14, True, 'checkmk-us'))
        self.assertTrue(output.file.name.startswith(f'discovery/commands/{self.device.pk}/'))
        with output.file.open('rb') as handle:
            self.assertEqual(handle.read(), b'Junos: 21.4R3\n')
        first_name = output.file.name
        response = self.upload(content='Junos: 22.4R1\n')
        self.assertEqual(response.status_code, 200)
        output.refresh_from_db()
        self.assertEqual(CommandOutput.objects.count(), 1)
        with output.file.open('rb') as handle:
            self.assertEqual(handle.read(), b'Junos: 22.4R1\n')
        self.assertFalse(output.file.storage.exists(first_name) and first_name != output.file.name)

    def test_rejected_commands_and_oversized_outputs(self):
        response = self.upload(command='show dot1x all', content='', ok=False, error='syntax error, expecting <command>')
        self.assertEqual(response.status_code, 201)
        self.assertFalse(CommandOutput.objects.get().ok)
        with override_settings(PLUGINS_CONFIG={'netbox_discovery': {'command_output_max_bytes': 10}}):
            response = self.upload(command='show configuration', content='x' * 11)
        self.assertEqual(response.status_code, 400)

    def test_device_page_panel_view_and_download(self):
        self.upload(content='Junos: 21.4R3\n')
        output = CommandOutput.objects.get()
        browser = Client()
        browser.force_login(self.user)
        response = browser.get(reverse('dcim:device', args=[self.device.pk]))
        self.assertContains(response, 'Device Configuration State')
        self.assertContains(response, 'alp-bofw-b_show_version.txt')
        response = browser.get(reverse('plugins:netbox_discovery:commandoutput_download', args=[output.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertIn('attachment; filename="alp-bofw-b_show_version.txt"', response['Content-Disposition'])
        self.assertEqual(b''.join(response.streaming_content), b'Junos: 21.4R3\n')
        response = browser.get(output.get_absolute_url())
        self.assertContains(response, 'Junos: 21.4R3')
        response = browser.get(reverse('plugins:netbox_discovery:device_command_outputs', args=[self.device.pk]))
        self.assertContains(response, 'alp-bofw-b_show_version.txt')
        response = self.api.get(f'/api/plugins/discovery/command-outputs/?device_id={self.device.pk}')
        self.assertEqual(response.data['count'], 1)
        self.assertNotIn('content', response.data['results'][0])
        self.assertIn('download_url', response.data['results'][0])

    def test_permissions_gate_upload_and_download(self):
        self.upload()
        output = CommandOutput.objects.get()
        viewer = get_user_model().objects.create_user('viewer')
        for model, actions in ((Device, ['view']),):
            permission = ObjectPermission.objects.create(name='view-devices', actions=actions)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(viewer)
        browser = Client()
        browser.force_login(viewer)
        self.assertEqual(browser.get(reverse('plugins:netbox_discovery:commandoutput_download', args=[output.pk])).status_code, 403)
        response = browser.get(reverse('dcim:device', args=[self.device.pk]))
        self.assertNotContains(response, 'Device Configuration State')
        self.api.force_authenticate(viewer)
        self.assertEqual(self.upload(command='show route').status_code, 403)
        permission = ObjectPermission.objects.create(name='see-outputs', actions=['view'])
        permission.object_types.add(ContentType.objects.get_for_model(CommandOutput))
        permission.users.add(viewer)
        self.assertEqual(browser.get(reverse('plugins:netbox_discovery:commandoutput_download', args=[output.pk])).status_code, 200)
