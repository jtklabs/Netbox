import tempfile
from copy import deepcopy
from pathlib import Path

from dcim.models import Device
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client
from django.urls import reverse
from users.models import ObjectPermission

from netbox_discovery.models import UpgradeJob
from netbox_discovery.upgrade_backups import backup_context
from .test_upgrade_groups import GroupFixture


class UCSDownloadTest(GroupFixture):
    def setUp(self):
        super().setUp()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        config = deepcopy(settings.PLUGINS_CONFIG)
        config['netbox_discovery']['ucs_download_roots'] = {str(self.poller.pk): str(self.root)}
        self.enable = self.settings(PLUGINS_CONFIG=config)
        self.enable.enable()
        self.addCleanup(self.enable.disable)
        self.job = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.file = self.root / 'device-job-pre.ucs'
        self.file.write_bytes(b'test-backup')
        self.job.summary = {'ucs_backup': {'name': self.file.name, 'bytes': 11, 'md5_verified': True,
                                         'local_path': '/untrusted/path/device-job-pre.ucs'}}
        self.job.save()
        self.url = reverse('plugins:netbox_discovery:upgradejob_ucs', args=[self.job.pk])
        self.browser = Client()
        self.browser.force_login(self.user)

    def grant(self, user, model, actions, constraints=None):
        permission = ObjectPermission.objects.create(name=str(model)+str(actions), actions=actions, constraints=constraints)
        permission.object_types.add(ContentType.objects.get_for_model(model))
        permission.users.add(user)

    def test_authenticated_download_is_attachment_not_cacheable(self):
        response = self.browser.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(b''.join(response.streaming_content), b'test-backup')
        self.assertIn('attachment;', response['Content-Disposition'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_device_and_job_pages_link_to_backup(self):
        for url in (self.job.get_absolute_url(), self.job.device.get_absolute_url()):
            response = self.browser.get(url)
            self.assertContains(response, f'href="{self.url}"')

    def test_anonymous_cannot_download(self):
        self.assertIn(Client().get(self.url).status_code, (302, 403))

    def test_read_only_user_cannot_download(self):
        user = get_user_model().objects.create_user('ucs-reader')
        self.grant(user, UpgradeJob, ['view'])
        self.grant(user, Device, ['view'])
        self.browser.force_login(user)
        self.assertEqual(self.browser.get(self.url).status_code, 403)
        self.assertIsNone(backup_context(self.job, user))

    def test_apply_permission_object_constraints_are_enforced(self):
        user = get_user_model().objects.create_user('ucs-applier')
        self.grant(user, UpgradeJob, ['view'])
        self.grant(user, UpgradeJob, ['apply'], {'pk': self.job.pk + 100})
        self.grant(user, Device, ['view'])
        self.browser.force_login(user)
        self.assertEqual(self.browser.get(self.url).status_code, 404)

    def test_hidden_device_cannot_download(self):
        user = get_user_model().objects.create_user('ucs-hidden-device')
        self.grant(user, UpgradeJob, ['view', 'apply'])
        self.grant(user, Device, ['view'], {'pk': self.job.device_id + 100})
        self.browser.force_login(user)
        self.assertEqual(self.browser.get(self.url).status_code, 404)

    def test_missing_backup_is_not_linked(self):
        self.file.unlink()
        self.assertEqual(self.browser.get(self.url).status_code, 404)
        response = self.browser.get(self.job.get_absolute_url())
        self.assertContains(response, 'Not available on this NetBox server')
        self.assertNotContains(response, f'href="{self.url}"')

    def test_incomplete_file_is_rejected(self):
        self.file.write_bytes(b'partial')
        self.assertEqual(self.browser.get(self.url).status_code, 404)

    def test_symlink_is_rejected(self):
        other = self.root / 'other'
        self.file.rename(other)
        self.file.symlink_to(other)
        self.assertEqual(self.browser.get(self.url).status_code, 404)

    def test_directory_and_fifo_are_rejected(self):
        import os
        self.file.unlink()
        self.file.mkdir()
        self.assertEqual(self.browser.get(self.url).status_code, 404)
        self.file.rmdir()
        os.mkfifo(self.file)
        self.assertEqual(self.browser.get(self.url).status_code, 404)

    def test_unverified_or_unsafe_metadata_is_rejected(self):
        for patch in ({'name': '../device-job-pre.ucs'}, {'name': '/etc/passwd'},
                      {'name': 'other.txt'}, {'md5_verified': False}, {'bytes': '11'}):
            with self.subTest(patch=patch):
                original = deepcopy(self.job.summary)
                self.job.summary['ucs_backup'].update(patch)
                self.job.save()
                self.assertEqual(self.browser.get(self.url).status_code, 404)
                self.job.summary = original

    def test_poller_without_configured_root_cannot_access_another_pollers_backup(self):
        config = deepcopy(settings.PLUGINS_CONFIG)
        config['netbox_discovery']['ucs_download_roots'] = {str(self.poller.pk + 1): str(self.root)}
        with self.settings(PLUGINS_CONFIG=config):
            self.assertEqual(self.browser.get(self.url).status_code, 404)
