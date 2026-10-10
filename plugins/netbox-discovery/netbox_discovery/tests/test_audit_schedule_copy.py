from datetime import time

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from extras.models import Tag
from users.models import ObjectPermission

from netbox_discovery import audit_scheduling
from netbox_discovery.models import AuditRun, AuditSchedule, JobProfile, UpgradeJob


class AuditScheduleCopyTest(TestCase):
    def setUp(self):
        from .test_remediation import RemediationTest
        RemediationTest.setUp(self)
        self.client.force_login(self.user)
        self.source = AuditSchedule.objects.create(
            name='One-time NTP audit', enabled=False, frequency='now', run_as=self.user,
            filters={'status': ['active'], 'site_id': [self.site.pk]},
            profile={'features': ['ntp'], 'mode': 'replace'},
            description='Original scope', comments='Original notes')
        self.finished = timezone.now()
        AuditSchedule.objects.filter(pk=self.source.pk).update(last_run_at=self.finished)
        self.run = AuditRun.objects.create(schedule=self.source, scheduled_for=self.finished, outcome='queued')
        self.tag = Tag.objects.create(name='Audit test', slug='audit-test')
        self.source.tags.add(self.tag)
        self.url = reverse('plugins:netbox_discovery:auditschedule_add') + f'?from_schedule={self.source.pk}'

    def test_copy_prefills_without_saving_or_replaying_history(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        form = response.context['form']
        self.assertEqual(form['name'].value(), 'One-time NTP audit (copy)')
        self.assertTrue(form['enabled'].value())
        self.assertEqual(form['frequency'].value(), 'now')
        self.assertEqual(form['sites'].value(), [self.site.pk])
        self.assertEqual(form['features'].value(), ['ntp'])
        self.assertEqual(form['comparison'].value(), 'replace')
        self.assertEqual(form['tags'].value(), [self.tag.pk])
        self.assertEqual(form['comments'].value(), 'Original notes')
        self.assertIsNone(form.instance.pk)
        self.assertIsNone(form.instance.last_run_at)
        self.assertIsNone(form.instance.next_run_at)
        self.assertEqual(AuditSchedule.objects.count(), 1)
        self.assertEqual(AuditRun.objects.count(), 1)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_copy_can_submit_one_time_remediation_with_same_scope(self):
        operator = get_user_model().objects.create_superuser('copy-operator', '', 'test-only')
        self.client.force_login(operator)
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(self.url, {
                'name': 'One-time NTP remediation', 'enabled': True, 'remediate': True,
                'frequency': 'now', 'window_hours': 4, 'sites': [self.site.pk],
                'profile_source': 'custom', 'features': ['ntp'], 'comparison': 'replace',
            })
        self.assertEqual(response.status_code, 302)
        self.assertFalse(UpgradeJob.objects.exists())
        audit_scheduling.run_due()
        copied = AuditSchedule.objects.get(name='One-time NTP remediation')
        self.assertEqual(copied.filters, self.source.filters)
        self.assertEqual(copied.run_as, operator)
        self.assertEqual(copied.runs.count(), 1)
        self.assertEqual(set(copied.runs.get().jobs.values_list('operation', flat=True)), {'remediate'})
        self.assertEqual(copied.runs.get().job_count, 2)
        self.source.refresh_from_db()
        self.assertFalse(self.source.remediate)
        self.assertFalse(self.source.enabled)
        self.assertEqual(self.source.last_run_at, self.finished)
        self.assertEqual(list(self.source.runs.all()), [self.run])
        self.assertIsNone(audit_scheduling.dispatch(self.source.pk, timezone.now()))

    def test_saved_profile_and_recurring_timing_are_preserved(self):
        profile = JobProfile.objects.create(name='NTP profile', kind='remediate',
                                            plan={'features': ['ntp'], 'mode': 'add'})
        AuditSchedule.objects.filter(pk=self.source.pk).update(
            frequency='weekly', weekday=3, local_time=time(14, 30), time_zone='America/New_York',
            window_hours=8, profile_source='saved', saved_profile=profile, profile={})
        form = self.client.get(self.url).context['form']
        for key, value in {'frequency': 'weekly', 'weekday': 3, 'local_time': time(14, 30),
                           'time_zone': 'America/New_York', 'window_hours': 8,
                           'profile_source': 'saved', 'saved_profile': profile.pk}.items():
            self.assertEqual(form[key].value(), value, key)

    def test_every_scope_field_is_prefilled(self):
        from dcim.models import Platform, Region
        from tenancy.models import Tenant
        from netbox_discovery.audit_views import SCOPE_FIELDS
        region = Region.objects.create(name='Region', slug='region')
        tenant = Tenant.objects.create(name='Tenant', slug='tenant')
        platform = Platform.objects.create(name='IOS', slug='ios')
        values = {'regions': region.pk, 'tenants': tenant.pk, 'sites': self.site.pk,
                  'platforms': platform.pk, 'models': self.devices[0].device_type_id,
                  'device_tags': self.tag.pk, 'devices': self.devices[0].pk}
        filters = {'status': ['active'], **{SCOPE_FIELDS[k]: [v] for k, v in values.items()}}
        AuditSchedule.objects.filter(pk=self.source.pk).update(filters=filters, profile_source='model', profile={})
        form = self.client.get(self.url).context['form']
        for field, value in values.items():
            self.assertEqual(form[field].value(), [value], field)
        self.assertEqual(form['profile_source'].value(), 'model')
        self.assertFalse(form['all_active'].value())

    def test_all_active_scope_is_preserved(self):
        AuditSchedule.objects.filter(pk=self.source.pk).update(filters={'status': ['active']})
        self.assertTrue(self.client.get(self.url).context['form']['all_active'].value())

    def test_copy_controls_on_list_and_detail(self):
        for url in (self.source.get_absolute_url(), reverse('plugins:netbox_discovery:auditschedule_list')):
            self.assertContains(self.client.get(url), f'from_schedule={self.source.pk}')

    def grant(self, user, model, actions, constraints=None):
        permission = ObjectPermission.objects.create(name=f'copy-{model.__name__}', actions=actions,
                                                      constraints=constraints)
        permission.object_types.add(ContentType.objects.get_for_model(model))
        permission.users.add(user)

    def test_cannot_copy_an_invisible_schedule(self):
        user = get_user_model().objects.create_user('restricted-copy')
        self.grant(user, AuditSchedule, ['view', 'add'], {'name': 'Another schedule'})
        self.grant(user, UpgradeJob, ['add'])
        self.client.force_login(user)
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_copy_requires_schedule_and_job_creation_permissions(self):
        for model in (AuditSchedule, UpgradeJob):
            user = get_user_model().objects.create_user(f'no-{model.__name__}')
            self.grant(user, AuditSchedule, ['view'] if model == AuditSchedule else ['view', 'add'])
            if model != UpgradeJob:
                self.grant(user, UpgradeJob, ['add'])
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url).status_code, 403)
            self.assertNotContains(self.client.get(self.source.get_absolute_url()), 'title="Copy schedule"')

    def test_invalid_or_deleted_source_returns_not_found(self):
        add = reverse('plugins:netbox_discovery:auditschedule_add')
        for source in ('invalid', '999999999', ''):
            self.assertEqual(self.client.get(add, {'from_schedule': source}).status_code, 404)
