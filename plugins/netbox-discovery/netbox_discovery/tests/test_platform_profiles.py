from datetime import timedelta

from dcim.models import Device, DeviceType, Platform, Region
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from users.models import ObjectPermission

from netbox_discovery import audit_scheduling, upgrade_queue as queue
from netbox_discovery.models import AuditSchedule, DeviceTypeProfile, JobProfile, PlatformProfile, UpgradeJob
from netbox_discovery.profile_assignments import default_profile_ids
from netbox_discovery.profile_views import PlatformProfileForm
from .test_upgrades import PROFILE


class PlatformProfileTest(TestCase):
    def setUp(self):
        from .test_remediation import RemediationTest
        RemediationTest.setUp(self)
        self.platform = Platform.objects.create(name='Cisco IOS', slug='cisco-ios')
        dtype = DeviceType.objects.create(manufacturer=self.devices[0].device_type.manufacturer,
                                         model='C9500', slug='c9500')
        self.devices[1].device_type = dtype
        for device in self.devices:
            device.platform = self.platform
            device.save()
        self.profile = JobProfile.objects.create(name='IOS NTP', kind='remediate',
                                                  plan={'features': ['ntp'], 'mode': 'replace'})
        self.override = JobProfile.objects.create(name='Model exception', kind='remediate',
                                                   plan={'features': ['syslog'], 'mode': 'add'})
        self.assignment = PlatformProfile.objects.create(platform=self.platform, remediation_profile=self.profile)
        self.data = {**self.data, 'profile_source': 'model', 'profile': None}
        self.api = APIClient()
        self.api.force_authenticate(self.user)
        self.browser = Client()
        self.browser.force_login(self.user)

    def test_platform_applies_to_multiple_models_for_audit_and_remediation(self):
        for operation in ('audit_config', 'remediate'):
            jobs = queue.schedule(self.user, {**self.data, 'operation': operation})
            self.assertEqual(len(jobs), 2)
            self.assertTrue(all(job.profile == self.profile.plan for job in jobs))
            self.assertTrue(all(job.profile_name == self.profile.name for job in jobs))

    def test_model_specific_profile_wins_and_upgrade_only_model_falls_back(self):
        DeviceTypeProfile.objects.create(device_type=self.devices[0].device_type, remediation_profile=self.override)
        upgrade = JobProfile.objects.create(name='Upgrade', kind='upgrade', plan=PROFILE)
        DeviceTypeProfile.objects.create(device_type=self.devices[1].device_type, upgrade_profile=upgrade)
        result = default_profile_ids(self.user, self.devices, 'remediate')
        self.assertEqual(result, {self.devices[0].pk: self.override.pk, self.devices[1].pk: self.profile.pk})

    def test_no_platform_or_different_platform_never_matches(self):
        other = Platform.objects.create(name='Arista EOS', slug='arista-eos')
        for platform in (None, other):
            self.devices[1].platform = platform
            self.devices[1].save()
            self.assertEqual(default_profile_ids(self.user, self.devices, 'remediate'),
                             {self.devices[0].pk: self.profile.pk})
            with self.assertRaisesRegex(queue.QueueError, 'no accessible'):
                queue.schedule(self.user, self.data)

    def test_devices_of_same_model_can_resolve_different_platform_defaults(self):
        other = Platform.objects.create(name='Other IOS version', slug='other-ios')
        PlatformProfile.objects.create(platform=other, remediation_profile=self.override)
        self.devices[1].device_type = self.devices[0].device_type
        self.devices[1].platform = other
        self.devices[1].save()
        jobs = queue.schedule(self.user, self.data)
        self.assertEqual({job.device_id: job.profile_name for job in jobs},
                         {self.devices[0].pk: self.profile.name, self.devices[1].pk: self.override.name})

    def test_platform_does_not_expand_upgrade_targets(self):
        with self.assertRaisesRegex(queue.QueueError, 'no accessible'):
            queue.schedule(self.user, {**self.data, 'operation': 'upgrade'})
        upgrade = JobProfile.objects.create(name='Upgrade', kind='upgrade', plan=PROFILE)
        self.assignment.remediation_profile = upgrade
        with self.assertRaises(ValidationError):
            self.assignment.full_clean()

    def test_profile_kind_cannot_change_while_assigned(self):
        self.profile.kind, self.profile.plan = 'upgrade', PROFILE
        with self.assertRaisesRegex(ValidationError, 'platform assignments'):
            self.profile.full_clean()

    def test_scheduled_jobs_keep_original_profile(self):
        job = queue.schedule(self.user, self.data)[0]
        self.assignment.remediation_profile = self.override
        self.assignment.save()
        job.refresh_from_db()
        self.assertEqual(job.profile, self.profile.plan)
        self.assertEqual(queue.schedule(self.user, self.data)[0].profile, self.override.plan)

    def schedule(self, source='model'):
        parent = Region.objects.create(name='Americas', slug='americas')
        child = Region.objects.create(name='East', slug='east', parent=parent)
        self.site.region = child
        self.site.save()
        return AuditSchedule.objects.create(name='Regional NTP', run_as=self.user, filters={'region_id': [parent.pk]},
                                            profile_source=source, profile={},
                                            saved_profile=self.profile if source == 'saved' else None)

    def test_region_audit_dispatches_platform_defaults(self):
        schedule = self.schedule()
        now = timezone.now()
        AuditSchedule.objects.filter(pk=schedule.pk).update(next_run_at=now - timedelta(minutes=1))
        run = audit_scheduling.dispatch(schedule.pk, now)
        self.assertEqual((run.outcome, run.job_count), ('queued', 2), run.message)
        self.assertEqual(set(run.jobs.values_list('profile_name', flat=True)), {self.profile.name})
        self.assertEqual(set(run.jobs.values_list('operation', flat=True)), {'audit_config'})

    def test_platform_scope_queues_all_addressed_devices_and_excludes_unaddressed(self):
        unaddressed = Device.objects.create(name='Unaddressed IOS', site=self.site, role=self.devices[0].role,
                                           device_type=self.devices[0].device_type, platform=self.platform)
        other = Platform.objects.create(name='Unselected platform', slug='unselected-platform')
        Device.objects.create(name='Outside scope', site=self.site, role=unaddressed.role,
                              device_type=unaddressed.device_type, platform=other)
        for source in ('model', 'saved', 'custom'):
            for remediate in (False, True):
                with self.subTest(source=source, remediate=remediate):
                    schedule = AuditSchedule.objects.create(
                        name=f'Platform {source} {remediate}', run_as=self.user, remediate=remediate,
                        filters={'platform_id': [self.platform.pk]}, profile_source=source,
                        saved_profile=self.profile if source == 'saved' else None,
                        profile=self.profile.plan if source == 'custom' else {})
                    now = timezone.now()
                    AuditSchedule.objects.filter(pk=schedule.pk).update(next_run_at=now - timedelta(minutes=1))
                    run = audit_scheduling.dispatch(schedule.pk, now)
                    self.assertEqual((run.outcome, run.job_count), ('queued', 2), run.message)
                    self.assertEqual(set(run.jobs.values_list('device_id', flat=True)), {d.pk for d in self.devices})
                    self.assertIn('1 execution devices without a primary management IP excluded', run.message)
                    self.assertFalse(run.jobs.filter(address='').exists())
        self.assertFalse(UpgradeJob.objects.filter(device=unaddressed).exists())

    def test_ui_now_platform_scope_does_not_collapse_to_one_device(self):
        Device.objects.create(name='No IP', site=self.site, role=self.devices[0].role,
                              device_type=self.devices[0].device_type, platform=self.platform)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.browser.post(reverse('plugins:netbox_discovery:auditschedule_add'), {
                'name': 'Platform now', 'enabled': True, 'frequency': 'now', 'window_hours': 4,
                'platforms': [self.platform.pk], 'profile_source': 'model',
            })
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors)
        schedule = AuditSchedule.objects.get(name='Platform now')
        self.assertEqual(schedule.filters, {'status': ['active'], 'platform_id': [self.platform.pk]})
        run = schedule.runs.get()
        self.assertEqual((run.outcome, run.job_count), ('queued', 2), run.message)
        self.assertEqual(set(run.jobs.values_list('device_id', flat=True)), {d.pk for d in self.devices})

    def test_missing_profile_and_missing_ip_have_distinct_counts(self):
        Device.objects.create(name='No IP or profile', site=self.site, role=self.devices[0].role,
                              device_type=self.devices[0].device_type)
        Device.objects.filter(pk=self.devices[1].pk).update(platform=None)
        schedule = self.schedule()
        reasons = {}
        self.assertEqual(audit_scheduling.eligible_devices(schedule, self.user, exclusions=reasons),
                         ([self.devices[0].pk], 2))
        self.assertEqual(reasons, {'missing_ip': 1, 'missing_profile': 1})

    def test_direct_queue_still_rejects_an_unaddressed_device(self):
        Device.objects.filter(pk=self.devices[0].pk).update(primary_ip4=None, primary_ip6=None)
        with self.assertRaisesMessage(queue.QueueError, 'no primary management IP'):
            queue.schedule(self.user, self.data)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_saved_schedule_respects_effective_model_override(self):
        schedule = self.schedule('saved')
        DeviceTypeProfile.objects.create(device_type=self.devices[0].device_type, remediation_profile=self.override)
        ids, excluded = audit_scheduling.eligible_devices(schedule, self.user)
        self.assertEqual(ids, [self.devices[1].pk])
        self.assertEqual(excluded, 1)

    def test_future_device_membership_is_resolved_each_time(self):
        schedule = self.schedule()
        self.assertEqual(len(audit_scheduling.eligible_devices(schedule, self.user)[0]), 2)
        Device.objects.filter(pk=self.devices[1].pk).update(platform=None)
        self.assertEqual(audit_scheduling.eligible_devices(schedule, self.user), ([self.devices[0].pk], 1))

    def restricted_user(self, profile_ids):
        user = get_user_model().objects.create_user('platform-reader')
        for model, constraints in ((Device, {}), (PlatformProfile, {}), (JobProfile, {'pk__in': profile_ids})):
            permission = ObjectPermission.objects.create(name=f'platform-{model.__name__}', actions=['view'],
                                                         constraints=constraints)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        return user

    def test_hidden_model_override_does_not_fall_back_to_platform(self):
        user = self.restricted_user([self.profile.pk, self.override.pk])
        DeviceTypeProfile.objects.create(device_type=self.devices[0].device_type, remediation_profile=self.override)
        self.assertEqual(default_profile_ids(user, self.devices, 'remediate'), {self.devices[1].pk: self.profile.pk})

    def test_profile_and_assignment_visibility_are_both_required(self):
        user = self.restricted_user([self.override.pk])
        self.assertEqual(default_profile_ids(user, self.devices, 'remediate'), {})
        other = get_user_model().objects.create_user('no-platform-visibility')
        permission = ObjectPermission.objects.create(name='only-job-profile', actions=['view'])
        permission.object_types.add(ContentType.objects.get_for_model(JobProfile))
        permission.users.add(other)
        self.assertEqual(default_profile_ids(other, self.devices, 'remediate'), {})

    def test_api_crud_and_replacement_confirmation(self):
        url = reverse('plugins-api:netbox_discovery-api:platformprofile-detail', args=[self.assignment.pk])
        response = self.api.patch(url, {'remediation_profile': self.override.pk}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        response = self.api.patch(url, {'remediation_profile': self.override.pk, 'replace_existing': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['remediation_profile']['id'], self.override.pk)
        self.assertEqual(self.api.delete(url).status_code, 204)
        response = self.api.post(reverse('plugins-api:netbox_discovery-api:platformprofile-list'),
                                 {'platform': self.platform.pk, 'remediation_profile': self.profile.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        response = self.api.post(reverse('plugins-api:netbox_discovery-api:platformprofile-list'),
                                 {'platform': self.platform.pk, 'remediation_profile': self.profile.pk}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_form_replacement_confirmation(self):
        data = {'platform': self.platform.pk, 'remediation_profile': self.override.pk}
        form = PlatformProfileForm(data, instance=self.assignment)
        self.assertFalse(form.is_valid())
        form = PlatformProfileForm({**data, 'replace_existing': True}, instance=self.assignment)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.save().remediation_profile, self.override)

    def test_pages_and_profile_assignment_link(self):
        for name, args in (('platformprofile_list', []), ('platformprofile_add', []),
                           ('platformprofile', [self.assignment.pk]), ('platformprofile_edit', [self.assignment.pk])):
            response = self.browser.get(reverse(f'plugins:netbox_discovery:{name}', args=args))
            self.assertEqual(response.status_code, 200)
        response = self.browser.get(self.profile.get_absolute_url())
        self.assertContains(response, 'Assigned platforms')
        self.assertContains(response, 'Assign to platform')
        self.assertContains(response, self.platform.name)
        response = self.browser.get(self.platform.get_absolute_url())
        self.assertContains(response, self.assignment.get_absolute_url())

    def test_ui_can_create_and_edit_assignment(self):
        self.assignment.delete()
        response = self.browser.post(reverse('plugins:netbox_discovery:platformprofile_add'),
                                     {'platform': self.platform.pk, 'remediation_profile': self.profile.pk})
        self.assertEqual(response.status_code, 302)
        assignment = PlatformProfile.objects.get(platform=self.platform)
        url = reverse('plugins:netbox_discovery:platformprofile_edit', args=[assignment.pk])
        data = {'platform': self.platform.pk, 'remediation_profile': self.override.pk}
        self.assertContains(self.browser.post(url, data), 'Replace existing assignments')
        assignment.refresh_from_db()
        self.assertEqual(assignment.remediation_profile, self.profile)
        response = self.browser.post(url, {**data, 'replace_existing': True})
        self.assertEqual(response.status_code, 302)
        assignment.refresh_from_db()
        self.assertEqual(assignment.remediation_profile, self.override)

    def test_assignment_changes_require_permission(self):
        user = get_user_model().objects.create_user('no-platform-writes')
        self.api.force_authenticate(user)
        response = self.api.post(reverse('plugins-api:netbox_discovery-api:platformprofile-list'),
                                 {'platform': self.platform.pk, 'remediation_profile': self.profile.pk}, format='json')
        self.assertEqual(response.status_code, 403)
