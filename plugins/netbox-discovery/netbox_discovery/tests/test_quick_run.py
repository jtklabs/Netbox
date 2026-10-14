from dcim.models import VirtualChassis
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from netbox_compliance.models import ConfigStandard
from users.models import ObjectPermission

from netbox_discovery.models import DeviceTypeProfile, JobProfile, UpgradeJob
from .test_remediation import RemediationTest


class QuickRunTest(TestCase):
    def setUp(self):
        RemediationTest.setUp(self)
        self.device = self.devices[0]
        self.standard = ConfigStandard.objects.get(name='NTP')
        self.profile = JobProfile.objects.create(name='Device standards', kind='remediate',
                                                 plan={'features': ['ntp', 'syslog'], 'mode': 'add'})
        DeviceTypeProfile.objects.create(device_type=self.device.device_type, remediation_profile=self.profile)
        self.browser = Client()
        self.browser.force_login(self.user)
        self.url = reverse('plugins:netbox_discovery:device_run_standards', args=[self.device.pk])

    def run_now(self, **changes):
        return self.browser.post(self.url, {'selection': f'standard:{self.standard.pk}', 'action': 'auto', **changes})

    def test_device_page_offers_single_standard_and_whole_profile(self):
        response = self.browser.get(self.device.get_absolute_url())
        for text in ('Run standards now', 'Run now', 'Audit only', 'Add missing', 'Add and remove',
                     f'standard:{self.standard.pk}', f'profile:{self.profile.pk}'):
            self.assertContains(response, text)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_one_click_queues_only_ntp_with_profile_mode_and_current_snapshot(self):
        response = self.run_now()
        job = UpgradeJob.objects.get()
        self.assertRedirects(response, job.get_absolute_url())
        self.assertEqual(job.profile, {'features': ['ntp'], 'mode': 'add'})
        self.assertEqual(job.operation, 'remediate')
        self.assertEqual(job.device, self.device)
        self.assertEqual(job.poller, self.poller)
        self.assertEqual(job.profile_name, self.profile.name)
        self.assertEqual(job.requested_by, self.user)
        self.assertLessEqual(job.scheduled_at, timezone.now())
        self.assertEqual(set(job.standards_snapshot['document']), {'ntp'})
        self.assertIsNone(job.audit_run_id)

    def test_whole_profile_and_explicit_add_remove(self):
        self.run_now(selection=f'profile:{self.profile.pk}', action='replace')
        job = UpgradeJob.objects.get()
        self.assertEqual(job.profile, {'features': ['ntp', 'syslog'], 'mode': 'replace'})
        self.assertEqual(set(job.standards_snapshot['document']), {'ntp', 'syslog'})

    def test_read_only_standard_defaults_to_audit(self):
        self.standard.auto_remediable = False
        self.standard.save()
        self.run_now()
        self.assertEqual(UpgradeJob.objects.get().operation, 'audit_config')

    def test_audit_dropdown_always_queues_read_only(self):
        self.run_now(action='audit')
        self.assertEqual(UpgradeJob.objects.get().operation, 'audit_config')

    def test_standard_without_profile_defaults_to_add(self):
        DeviceTypeProfile.objects.all().delete()
        self.run_now()
        self.assertEqual(UpgradeJob.objects.get().profile['mode'], 'add')

    def test_enforce_disabled_defaults_to_add_but_explicit_replace_is_rejected(self):
        self.standard.allow_enforce = False
        self.standard.save()
        self.profile.plan['mode'] = 'replace'
        self.profile.save()
        self.run_now(action='replace')
        self.assertFalse(UpgradeJob.objects.exists())
        self.run_now()
        self.assertEqual(UpgradeJob.objects.get().profile['mode'], 'add')

    def test_duplicate_click_does_not_duplicate_pending_work(self):
        self.run_now()
        self.run_now()
        self.assertEqual(UpgradeJob.objects.count(), 1)

    def test_invalid_selection_or_action_never_creates_work(self):
        for changes in ({'selection': 'standard:999999'}, {'selection': 'profile:999999'},
                        {'action': 'delete'}, {'selection': ''}):
            with self.subTest(changes=changes):
                self.assertRedirects(self.run_now(**changes), self.device.get_absolute_url())
                self.assertFalse(UpgradeJob.objects.exists())

    def test_get_and_unprivileged_post_cannot_execute(self):
        self.assertEqual(self.browser.get(self.url).status_code, 405)
        self.browser.force_login(get_user_model().objects.create_user('no-quick-run'))
        self.assertEqual(self.run_now().status_code, 403)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_csrf_is_required(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.user)
        self.assertEqual(client.post(self.url, {'selection': f'standard:{self.standard.pk}'}).status_code, 403)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_auditor_cannot_apply_or_run_an_inaccessible_device(self):
        user = get_user_model().objects.create_user('quick-auditor')
        for model, actions, constraints in (
                (type(self.device), ['view'], {'pk': self.device.pk}),
                (ConfigStandard, ['view'], {}),
                (UpgradeJob, ['add', 'view'], {})):
            permission = ObjectPermission.objects.create(name=f'quick-{model.__name__}', actions=actions,
                                                         constraints=constraints)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        self.browser.force_login(user)
        self.run_now(action='replace')
        self.assertFalse(UpgradeJob.objects.exists())
        self.run_now(action='audit')
        self.assertEqual(UpgradeJob.objects.get().operation, 'audit_config')
        other_url = reverse('plugins:netbox_discovery:device_run_standards', args=[self.devices[1].pk])
        response = self.browser.post(other_url, {'selection': f'standard:{self.standard.pk}', 'action': 'audit'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual(UpgradeJob.objects.count(), 1)

    def test_stack_member_runs_on_master(self):
        chassis = VirtualChassis.objects.create(name='stack')
        for position, device in enumerate(self.devices, 1):
            device.virtual_chassis = chassis
            device.vc_position = position
            device.save()
        chassis.master = self.devices[1]
        chassis.save()
        self.run_now()
        self.assertEqual(UpgradeJob.objects.get().device_id, self.devices[1].pk)
