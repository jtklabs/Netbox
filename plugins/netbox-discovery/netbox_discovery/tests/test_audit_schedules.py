from datetime import datetime, time, timedelta, timezone as dt_timezone
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, SimpleTestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from netbox_discovery import audit_scheduling, upgrade_queue as queue
from netbox_discovery.models import AuditSchedule, AuditRun, UpgradeJob, JobProfile, DeviceTypeProfile


class RecurrenceTest(SimpleTestCase):
    def test_daily_utc(self):
        schedule = AuditSchedule(local_time=time(2), time_zone='UTC')
        after = datetime(2026, 1, 2, 2, tzinfo=dt_timezone.utc)
        self.assertEqual(audit_scheduling.next_occurrence(schedule, after), after + timedelta(days=1))

    def test_weekly_new_york(self):
        schedule = AuditSchedule(frequency='weekly', weekday=0, local_time=time(2), time_zone='America/New_York')
        self.assertEqual(audit_scheduling.next_occurrence(schedule, datetime(2026, 10, 8, tzinfo=dt_timezone.utc)),
                         datetime(2026, 10, 12, 6, tzinfo=dt_timezone.utc))

    def test_spring_gap_shifts_forward(self):
        schedule = AuditSchedule(local_time=time(2, 30), time_zone='America/New_York')
        self.assertEqual(audit_scheduling.next_occurrence(schedule, datetime(2026, 3, 8, tzinfo=dt_timezone.utc)),
                         datetime(2026, 3, 8, 7, 30, tzinfo=dt_timezone.utc))

    def test_fall_fold_runs_only_once(self):
        schedule = AuditSchedule(local_time=time(1, 30), time_zone='America/New_York')
        first = datetime(2026, 11, 1, 5, 30, tzinfo=dt_timezone.utc)
        self.assertEqual(audit_scheduling.next_occurrence(schedule, first - timedelta(minutes=1)), first)
        self.assertEqual(audit_scheduling.next_occurrence(schedule, first),
                         datetime(2026, 11, 2, 6, 30, tzinfo=dt_timezone.utc))


class AuditScheduleTest(TestCase):
    def setUp(self):
        from .test_remediation import RemediationTest
        RemediationTest.setUp(self)
        self.schedule = AuditSchedule.objects.create(
            name='Daily NTP', run_as=self.user, filters={'site_id': [self.site.pk], 'status': ['active']},
            profile={'features': ['ntp'], 'mode': 'replace'})
        self.now = timezone.now()
        self.make_due()

    def make_due(self, when=None):
        AuditSchedule.objects.filter(pk=self.schedule.pk).update(next_run_at=when or self.now - timedelta(minutes=1))
        self.schedule.refresh_from_db()

    def dispatch(self):
        return audit_scheduling.dispatch(self.schedule.pk, self.now)

    def test_run_is_read_only_and_idempotent(self):
        run = self.dispatch()
        self.assertEqual((run.outcome, run.job_count), ('queued', 2))
        self.assertEqual(set(run.jobs.values_list('operation', flat=True)), {'audit_config'})
        self.assertIsNone(self.dispatch())
        self.assertEqual(AuditRun.objects.count(), 1)
        self.assertEqual(UpgradeJob.objects.count(), 2)
        self.schedule.refresh_from_db()
        self.assertGreater(self.schedule.next_run_at, self.now)

    def test_occurrence_has_unique_constraint(self):
        run = self.dispatch()
        with self.assertRaises(IntegrityError), transaction.atomic():
            AuditRun.objects.create(schedule=self.schedule, scheduled_for=run.scheduled_for, outcome='failed')

    def test_overlap_is_recorded_and_skipped(self):
        self.dispatch()
        self.make_due(self.now)
        run = self.dispatch()
        self.assertEqual(run.outcome, 'skipped')
        self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_offline_pollers_expire_without_permanent_block(self):
        first = self.dispatch()
        UpgradeJob.objects.filter(audit_run=first).update(start_before=self.now - timedelta(seconds=1))
        self.make_due(self.now)
        second = self.dispatch()
        self.assertEqual(second.outcome, 'queued')
        self.assertEqual(set(first.jobs.values_list('status', flat=True)), {'expired'})

    def test_active_work_is_never_expired_to_allow_overlap(self):
        first = self.dispatch()
        first.jobs.update(status='running', start_before=self.now - timedelta(seconds=1))
        self.make_due(self.now)
        self.assertEqual(self.dispatch().outcome, 'skipped')

    def test_downtime_produces_only_one_catchup_batch(self):
        self.make_due(self.now - timedelta(days=15))
        self.dispatch()
        self.assertIsNone(self.dispatch())
        self.assertEqual(AuditRun.objects.count(), 1)

    def test_pause_keeps_existing_jobs_and_history(self):
        self.dispatch()
        self.schedule.enabled = False
        self.schedule.save()
        self.assertIsNone(self.dispatch())
        self.assertIsNone(self.schedule.next_run_at)
        self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_stale_edit_does_not_restore_consumed_due_time(self):
        self.dispatch()
        self.schedule.description = 'Edited after dispatch'
        self.schedule.save()
        self.assertGreater(self.schedule.next_run_at, self.now)
        self.assertEqual(self.schedule.last_run_at, self.now)

    def test_latest_standards_and_profiles_resolved_each_run(self):
        from netbox_compliance.models import ConfigStandard
        profile = JobProfile.objects.create(name='Audit profile', kind='remediate',
                                             plan={'features': ['ntp'], 'mode': 'replace'})
        DeviceTypeProfile.objects.create(device_type=self.devices[0].device_type, remediation_profile=profile)
        self.schedule.profile_source = 'model'
        self.schedule.profile = {}
        self.schedule.save()
        first = self.dispatch()
        first.jobs.update(status='completed')
        standard = ConfigStandard.objects.get(name='NTP')
        standard.definition_yaml = 'ntp:\n  servers: [192.0.2.99]'
        standard.save()
        profile.plan = {'features': ['ntp', 'syslog'], 'mode': 'replace'}
        profile.save()
        self.make_due(self.now)
        second = self.dispatch()
        old = first.jobs.first()
        new = second.jobs.first()
        self.assertEqual(old.standards_snapshot['document']['ntp']['servers'], ['192.0.2.10'])
        self.assertEqual(new.standards_snapshot['document']['ntp']['servers'], ['192.0.2.99'])
        self.assertEqual(new.profile['features'], ['ntp', 'syslog'])

    def test_missing_run_as_fails_without_jobs(self):
        self.schedule.run_as = None
        self.schedule.save()
        self.assertEqual(self.dispatch().outcome, 'failed')
        self.assertFalse(UpgradeJob.objects.exists())

    def test_inactive_run_as_fails_without_jobs(self):
        self.user.is_active = False
        self.user.save()
        self.assertEqual(self.dispatch().outcome, 'failed')
        self.assertFalse(UpgradeJob.objects.exists())

    def test_permission_revocation_fails_closed(self):
        self.user.is_superuser = False
        self.user.save()
        self.assertEqual(self.dispatch().outcome, 'failed')
        self.assertFalse(UpgradeJob.objects.exists())

    def test_empty_selection_is_skipped_and_advances(self):
        self.schedule.filters = {'id': [99999999]}
        self.schedule.save()
        self.assertEqual(self.dispatch().outcome, 'skipped')
        self.schedule.refresh_from_db()
        self.assertGreater(self.schedule.next_run_at, self.now)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_partial_batch_rolls_back(self):
        original = UpgradeJob.save
        calls = []

        def fail_second(job, *args, **kwargs):
            calls.append(job)
            if len(calls) == 2:
                raise ValidationError('Rejected second job')
            return original(job, *args, **kwargs)

        with patch.object(UpgradeJob, 'save', fail_second):
            self.assertEqual(self.dispatch().outcome, 'failed')
        self.assertFalse(UpgradeJob.objects.exists())

    def test_recurring_job_cannot_be_changed_into_remediation(self):
        job = self.dispatch().jobs.first()
        with self.assertRaisesRegex(queue.QueueError, 'retain their audit or remediation mode'):
            queue.edit_pending(self.user, job.pk, {**self.data, 'last_updated': job.last_updated})

    def test_dispatch_registration(self):
        from netbox.registry import registry
        from netbox_discovery.jobs import AuditScheduleJob
        self.assertEqual(registry['system_jobs'][AuditScheduleJob], {'interval': 1})

    def test_worker_recovers_dispatcher_lost_from_redis(self):
        from core.models import Job
        from core.choices import JobStatusChoices
        from netbox_discovery.jobs import AuditScheduleJob
        orphan = Job.objects.create(name=AuditScheduleJob.name, interval=1, job_id=uuid.uuid4(),
                                    status=JobStatusChoices.ENQUEUED_STATE_CHOICES[0])
        with patch('django_rq.get_queue') as get_queue, patch.object(AuditScheduleJob, 'enqueue') as enqueue:
            get_queue.return_value.fetch_job.return_value = None
            AuditScheduleJob.enqueue_once(interval=1)
        orphan.refresh_from_db()
        self.assertEqual(orphan.status, JobStatusChoices.STATUS_ERRORED)
        enqueue.assert_called_once()

    def test_worker_preserves_existing_redis_dispatcher(self):
        from core.models import Job
        from core.choices import JobStatusChoices
        from netbox_discovery.jobs import AuditScheduleJob
        existing = Job.objects.create(name=AuditScheduleJob.name, interval=1, job_id=uuid.uuid4(),
                                      status=JobStatusChoices.ENQUEUED_STATE_CHOICES[0])
        with patch('django_rq.get_queue'), patch.object(AuditScheduleJob, 'enqueue') as enqueue:
            result = AuditScheduleJob.enqueue_once(interval=1)
        self.assertEqual(result.pk, existing.pk)
        enqueue.assert_not_called()

    def test_model_rejects_invalid_configuration(self):
        for field, value in [('time_zone', 'bad-zone'), ('filters', {}), ('filters', {'site_id': []}),
                             ('filters', {'unknown': [1]}), ('window_hours', 0), ('profile', {})]:
            schedule = AuditSchedule(name='Other', filters={'site_id': [self.site.pk]},
                                     profile={'features': ['ntp'], 'mode': 'replace'})
            setattr(schedule, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(ValidationError):
                schedule.full_clean()

    def test_ui_create_and_views(self):
        client = Client()
        client.force_login(self.user)
        url = reverse('plugins:netbox_discovery:auditschedule_add')
        self.assertEqual(client.get(url).status_code, 200)
        response = client.post(url, {'name': 'UI audit', 'enabled': True, 'frequency': 'daily', 'weekday': 0,
                                     'local_time': '03:00', 'time_zone': 'America/New_York', 'window_hours': 4,
                                     'sites': [self.site.pk], 'profile_source': 'custom',
                                     'features': ['ntp'], 'comparison': 'replace'})
        self.assertEqual(response.status_code, 302, getattr(response, 'context', None) and response.context['form'].errors)
        schedule = AuditSchedule.objects.get(name='UI audit')
        self.assertEqual(schedule.run_as, self.user)
        self.assertEqual(schedule.filters, {'status': ['active'], 'site_id': [self.site.pk]})
        run = self.dispatch()
        for target in (schedule.get_absolute_url(), run.get_absolute_url(),
                       reverse('plugins:netbox_discovery:auditschedule_list'),
                       reverse('plugins:netbox_discovery:auditrun_list')):
            self.assertEqual(client.get(target).status_code, 200, target)

    def test_api_create_pause_and_history(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse('plugins-api:netbox_discovery-api:auditschedule-list')
        response = client.post(url, {'name': 'API audit', 'filters': {'site_id': [self.site.pk]},
                                     'profile': {'features': ['ntp'], 'mode': 'replace'},
                                     'run_as': 999, 'next_run_at': self.now.isoformat()}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        schedule = AuditSchedule.objects.get(pk=response.data['id'])
        self.assertEqual(schedule.run_as, self.user)
        self.assertGreater(schedule.next_run_at, self.now)
        response = client.patch(f'{url}{schedule.pk}/', {'enabled': False}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['next_run_at'])
        run = self.dispatch()
        response = client.get(reverse('plugins-api:netbox_discovery-api:auditrun-detail', args=[run.pk]))
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['job_status_counts'], {'pending': 2})
        response = client.get(reverse('plugins-api:netbox_discovery-api:upgradejob-list'), {'audit_run_id': run.pk})
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['results'][0]['audit_run'], run.pk)

    def test_api_rejects_empty_scope(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(reverse('plugins-api:netbox_discovery-api:auditschedule-list'),
                               {'name': 'Unsafe', 'filters': {}, 'profile': {'features': ['ntp'], 'mode': 'replace'}},
                               format='json')
        self.assertEqual(response.status_code, 400)

    def test_virtual_chassis_members_do_not_break_fleet_scope(self):
        from dcim.models import VirtualChassis
        vc = VirtualChassis.objects.create(name='Audit stack', master=self.devices[0])
        for index, device in enumerate(self.devices):
            device.virtual_chassis = vc
            device.vc_position = index + 1
            if index:
                device.primary_ip4 = None
                device.primary_ip6 = None
            device.save()
        # A scope matching only a member must still audit its master once.
        self.schedule.filters = {'id': [self.devices[1].pk]}
        self.schedule.save()
        run = self.dispatch()
        self.assertEqual(run.outcome, 'queued', run.message)
        self.assertEqual(list(run.jobs.values_list('device_id', flat=True)), [self.devices[0].pk])

    def test_missing_ip_does_not_block_other_targets(self):
        self.devices[0].primary_ip4 = None
        self.devices[0].primary_ip6 = None
        self.devices[0].save()
        run = self.dispatch()
        self.assertEqual((run.outcome, run.job_count), ('queued', 1), run.message)
        self.assertEqual(list(run.jobs.values_list('device_id', flat=True)), [self.devices[1].pk])
        self.assertIn('1 execution devices without a primary management IP excluded', run.message)

    def test_all_unaddressed_targets_are_skipped_without_jobs(self):
        from dcim.models import Device
        Device.objects.filter(pk__in=[device.pk for device in self.devices]).update(primary_ip4=None, primary_ip6=None)
        run = self.dispatch()
        self.assertEqual((run.outcome, run.job_count), ('skipped', 0), run.message)
        self.assertIn('2 execution devices without a primary management IP excluded', run.message)
        self.assertFalse(run.jobs.exists())

    def test_unaddressed_stack_master_is_skipped_without_blocking_standalone(self):
        from dcim.models import Device, VirtualChassis
        master = self.devices[0]
        stack = VirtualChassis.objects.create(name='Unaddressed stack', master=master)
        master.virtual_chassis = stack
        master.vc_position = 1
        master.primary_ip4 = master.primary_ip6 = None
        master.save()
        Device.objects.create(name='Unaddressed member', site=master.site, role=master.role,
                              device_type=master.device_type, virtual_chassis=stack, vc_position=2)
        run = self.dispatch()
        self.assertEqual(list(run.jobs.values_list('device_id', flat=True)), [self.devices[1].pk])
        self.assertIn('1 execution devices without a primary management IP excluded', run.message)

    def test_primary_ip_added_is_included_on_next_occurrence(self):
        device = self.devices[0]
        original = device.primary_ip4
        device.primary_ip4 = None
        device.save()
        first = self.dispatch()
        self.assertEqual(first.job_count, 1)
        first.jobs.update(status='completed')
        device.primary_ip4 = original
        device.save()
        self.make_due(self.now)
        second = self.dispatch()
        self.assertEqual((second.outcome, second.job_count), ('queued', 2), second.message)

    def test_audit_only_operator_needs_no_apply_permission(self):
        from dcim.models import Device
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission
        from netbox_compliance.models import ConfigStandard
        user = get_user_model().objects.create_user('recurring-auditor')
        for model, actions in ((Device, ['view']), (ConfigStandard, ['view']),
                               (AuditSchedule, ['view', 'add', 'change']),
                               (UpgradeJob, ['add', 'view'])):
            permission = ObjectPermission.objects.create(name=f'recurring-{model.__name__}', actions=actions)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        self.schedule.run_as = user
        self.schedule.save()
        self.assertFalse(user.has_perm('netbox_discovery.apply_upgradejob'))
        run = self.dispatch()
        self.assertEqual(run.outcome, 'queued', run.message)

    def test_object_permissions_roll_back_entire_batch(self):
        from dcim.models import Device
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission
        from netbox_compliance.models import ConfigStandard
        user = get_user_model().objects.create_user('limited-auditor')
        for model in (Device, ConfigStandard, AuditSchedule):
            permission = ObjectPermission.objects.create(name=f'limited-{model.__name__}', actions=['view'])
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        permission = ObjectPermission.objects.create(name='limited-jobs', actions=['add'],
                                                      constraints={'device_id': self.devices[0].pk})
        permission.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
        permission.users.add(user)
        self.schedule.run_as = user
        self.schedule.save()
        run = self.dispatch()
        self.assertEqual(run.outcome, 'failed', run.message)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_unexpected_failure_retries_without_half_a_batch(self):
        with patch.object(queue, 'schedule', side_effect=RuntimeError('Temporary failure')):
            with self.assertRaises(RuntimeError):
                self.dispatch()
        self.assertFalse(AuditRun.objects.exists())
        self.assertEqual(self.dispatch().outcome, 'queued')

    def test_disabled_schedule_can_be_saved_with_no_current_matches(self):
        client = APIClient()
        client.force_authenticate(self.user)
        url = reverse('plugins-api:netbox_discovery-api:auditschedule-detail', args=[self.schedule.pk])
        self.devices[0].status = 'offline'
        self.devices[0].save()
        response = client.patch(url, {'enabled': False, 'filters': {'id': [self.devices[0].pk]}}, format='json')
        self.assertEqual(response.status_code, 200, response.data)

    def test_paused_schedules_still_expire_past_due_unclaimed_jobs(self):
        run = self.dispatch()
        self.schedule.enabled = False
        self.schedule.save()
        run.jobs.update(start_before=timezone.now() - timedelta(hours=1))
        self.assertEqual(audit_scheduling.run_due(), 0)
        self.assertEqual(set(run.jobs.values_list('status', flat=True)), {'expired'})

    def test_scope_filters_intersect_and_reevaluate_membership(self):
        from tenancy.models import Tenant
        tenant = Tenant.objects.create(name='Audit tenant', slug='audit-tenant')
        self.devices[0].tenant = tenant
        self.devices[0].save()
        self.schedule.filters['tenant_id'] = [tenant.pk]
        self.schedule.save()
        first = self.dispatch()
        self.assertEqual(first.job_count, 1)
        first.jobs.update(status='completed')
        self.devices[1].tenant = tenant
        self.devices[1].save()
        self.make_due(self.now)
        self.assertEqual(self.dispatch().job_count, 2)


class RegionalAuditTest(TestCase):
    """Region targeting and profile eligibility, including scopes larger than a batch."""

    make_due = AuditScheduleTest.make_due
    dispatch = AuditScheduleTest.dispatch

    def setUp(self):
        AuditScheduleTest.setUp(self)
        from dcim.models import Region, Site
        self.region = Region.objects.create(name='East', slug='east')
        self.child = Region.objects.create(name='Metro', slug='metro', parent=self.region)
        self.site.region = self.child
        self.site.save()
        self.outside = Site.objects.create(name='Outside', slug='outside',
                                           region=Region.objects.create(name='West', slug='west'))
        self.schedule.filters = {'region_id': [self.region.pk]}
        self.schedule.profile_source = 'model'
        self.schedule.profile = {}
        self.schedule.save()

    def assign_profile(self):
        profile = JobProfile.objects.create(name='Regional NTP', kind='remediate',
                                             plan={'features': ['ntp'], 'mode': 'replace'})
        DeviceTypeProfile.objects.create(device_type=self.devices[0].device_type, remediation_profile=profile)
        return profile

    def test_parent_region_includes_children_but_not_other_regions(self):
        self.assign_profile()
        self.devices[1].site = self.outside
        self.devices[1].save()
        run = self.dispatch()
        self.assertEqual(run.outcome, 'queued', run.message)
        self.assertEqual(list(run.jobs.values_list('device_id', flat=True)), [self.devices[0].pk])

    def test_model_defaults_exclude_unassigned_models(self):
        from dcim.models import DeviceType
        self.assign_profile()
        self.devices[1].device_type = DeviceType.objects.create(model='Unassigned', slug='unassigned',
            manufacturer=self.devices[0].device_type.manufacturer)
        self.devices[1].save()
        run = self.dispatch()
        self.assertEqual(run.outcome, 'queued', run.message)
        self.assertEqual(run.job_count, 1)
        self.assertIn('1 devices without a matching accessible model or platform profile excluded', run.message)

    def test_empty_match_skips_then_picks_up_new_assignments(self):
        run = self.dispatch()
        self.assertEqual(run.outcome, 'skipped', run.message)
        self.assertFalse(run.jobs.exists())
        self.assign_profile()
        self.make_due(self.now)
        self.assertEqual(self.dispatch().job_count, 2)

    def test_saved_profile_only_targets_assigned_models(self):
        from dcim.models import DeviceType
        profile = self.assign_profile()
        other = JobProfile.objects.create(name='Other profile', kind='remediate',
                                           plan={'features': ['syslog'], 'mode': 'replace'})
        dtype = DeviceType.objects.create(model='Other model', slug='other-model',
                                          manufacturer=self.devices[0].device_type.manufacturer)
        DeviceTypeProfile.objects.create(device_type=dtype, remediation_profile=other)
        self.devices[1].device_type = dtype
        self.devices[1].save()
        self.schedule.profile_source = 'saved'
        self.schedule.saved_profile = profile
        self.schedule.save()
        run = self.dispatch()
        self.assertEqual(run.job_count, 1, run.message)
        self.assertEqual(run.jobs.get().device_id, self.devices[0].pk)

    def test_ui_can_save_region_before_devices_or_profiles_exist(self):
        from dcim.models import Region
        region = Region.objects.create(name='New region', slug='new-region')
        client = Client()
        client.force_login(self.user)
        response = client.post(reverse('plugins:netbox_discovery:auditschedule_add'), {
            'name': 'Future region', 'enabled': True, 'frequency': 'daily', 'weekday': 0,
            'local_time': '03:00', 'time_zone': 'America/New_York', 'window_hours': 4,
            'regions': [region.pk], 'profile_source': 'model', 'comparison': 'replace'})
        self.assertEqual(response.status_code, 302,
                         response.context and response.context['form'].errors)
        saved = AuditSchedule.objects.get(name='Future region')
        self.assertEqual(saved.filters, {'status': ['active'], 'region_id': [region.pk]})
        response = client.get(reverse('plugins:netbox_discovery:auditschedule_edit', args=[saved.pk]))
        self.assertEqual(response.context['form'].initial['regions'], [region.pk])
        self.assertContains(client.get(saved.get_absolute_url()), 'New region')

    def test_api_can_save_region_without_matching_profiles(self):
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.post(reverse('plugins-api:netbox_discovery-api:auditschedule-list'), {
            'name': 'API region', 'filters': {'region_id': [self.region.pk]}, 'profile_source': 'model'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_invalid_region_is_rejected(self):
        self.schedule.filters = {'region_id': [99999999]}
        with self.assertRaises(ValidationError):
            self.schedule.full_clean()

    def test_region_filters_intersect_with_site_filters(self):
        self.assign_profile()
        self.schedule.filters['site_id'] = [self.outside.pk]
        self.schedule.save()
        run = self.dispatch()
        self.assertEqual(run.outcome, 'skipped')
        self.assertFalse(run.jobs.exists())

    def test_matching_profiles_must_be_accessible(self):
        from dcim.models import Device
        from django.contrib.contenttypes.models import ContentType
        from netbox_compliance.models import ConfigStandard
        from users.models import ObjectPermission
        self.assign_profile()
        user = get_user_model().objects.create_user('regional-auditor')
        for model, actions in ((Device, ['view']), (ConfigStandard, ['view']),
                               (DeviceTypeProfile, ['view']), (AuditSchedule, ['view']),
                               (UpgradeJob, ['add', 'view'])):
            permission = ObjectPermission.objects.create(name=f'regional-{model.__name__}', actions=actions)
            permission.object_types.add(ContentType.objects.get_for_model(model))
            permission.users.add(user)
        self.schedule.run_as = user
        self.schedule.save()
        run = self.dispatch()
        self.assertEqual(run.outcome, 'skipped', run.message)
        self.assertFalse(run.jobs.exists())

    def test_saving_does_not_validate_live_inventory(self):
        client = APIClient()
        client.force_authenticate(self.user)
        with patch.object(queue, 'prepare', side_effect=AssertionError('Must not prepare devices when saving')):
            response = client.post(reverse('plugins-api:netbox_discovery-api:auditschedule-list'), {
                'name': 'Future custom audit', 'filters': {'region_id': [self.region.pk]},
                'profile_source': 'custom', 'profile': {'features': ['ntp'], 'mode': 'replace'}}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def add_large_inventory(self):
        from dcim.models import Device
        from ipam.models import IPAddress
        addresses = [IPAddress.objects.create(address=f'198.18.{i // 254}.{i % 254 + 1}/24')
                     for i in range(999)]
        Device.objects.bulk_create([
            Device(name=f'region-device-{i}', site=self.site, role=self.devices[0].role,
                   device_type=self.devices[0].device_type, primary_ip4=address)
            for i, address in enumerate(addresses)])
        self.assign_profile()

    def test_large_region_uses_multiple_queue_batches_in_one_run(self):
        self.add_large_inventory()

        def enqueue(user, data):
            self.assertLessEqual(len(data['filters']['id']), 1000)
            return UpgradeJob.objects.bulk_create([
                UpgradeJob(device_id=pk, device_name=f'device-{pk}', poller=self.poller,
                           address='192.0.2.1', operation=data['operation'], profile={'features': ['ntp'], 'mode': 'replace'},
                           scheduled_at=data['scheduled_at'], start_before=data['start_before'])
                for pk in data['filters']['id']])

        with patch.object(queue, 'schedule', side_effect=enqueue) as mocked:
            run = self.dispatch()
        self.assertEqual(run.job_count, 1001, run.message)
        self.assertEqual(run.jobs.count(), 1001)
        self.assertEqual([len(call.args[1]['filters']['id']) for call in mocked.call_args_list], [1000, 1])

    def test_later_batch_failure_rolls_back_earlier_jobs(self):
        self.add_large_inventory()
        original = queue.schedule
        calls = []

        def enqueue(user, data):
            calls.append(data)
            if len(calls) == 2:
                raise queue.QueueError('Second batch rejected')
            return original(user, {**data, 'filters': {'id': [self.devices[0].pk]}})

        with patch.object(queue, 'schedule', side_effect=enqueue):
            run = self.dispatch()
        self.assertEqual(run.outcome, 'failed')
        self.assertEqual(run.job_count, 0)
        self.assertFalse(UpgradeJob.objects.exists())


class ConcurrentAuditTest(TransactionTestCase):
    def test_two_dispatchers_create_one_batch(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from .test_remediation import RemediationTest
        RemediationTest.setUp(self)
        schedule = AuditSchedule.objects.create(name='Concurrent', run_as=self.user,
                                                 filters={'site_id': [self.site.pk]},
                                                 profile={'features': ['ntp'], 'mode': 'replace'})
        now = timezone.now()
        AuditSchedule.objects.filter(pk=schedule.pk).update(next_run_at=now - timedelta(minutes=1))
        barrier = Barrier(2)

        def dispatch():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                result = audit_scheduling.dispatch(schedule.pk, now)
                return result.pk if result else None
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: dispatch(), range(2)))
        self.assertEqual(sum(result is not None for result in results), 1)
        self.assertEqual(AuditRun.objects.count(), 1)
        self.assertEqual(UpgradeJob.objects.count(), 2)
