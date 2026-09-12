"""Upgrade queue regressions, using the real NetBox/PostgreSQL models."""
import copy
import uuid
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone
from dcim.models import Device, DeviceRole, DeviceType, Manufacturer, Site
from extras.models import Tag
from ipam.models import IPAddress
from rest_framework.test import APIClient
from users.models import ObjectPermission

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import DiscoveryPoller, UpgradeJob


PROFILE = {'name': 'validated-c9350', 'models': ['C9350-48P'], 'starting_versions': ['17.18.1'],
           'target_version': '17.18.4', 'image': 'cisco9k_iosxe.17.18.04.SPA.bin',
           'md5': 'a' * 32, 'minimum_free_bytes': 1500000000}


class UpgradeFixture:
    def setup_data(self):
        self.user = get_user_model().objects.create_superuser('upgrade-admin', 'test@example.test', 'test-only')
        self.site = Site.objects.create(name='Upgrade lab', slug='upgrade-lab')
        self.site.tags.add(Tag.objects.create(name='poller-lab', slug='poller-lab'))
        self.role = DeviceRole.objects.create(name='Access', slug='access')
        manufacturer = Manufacturer.objects.create(name='Cisco', slug='cisco')
        dtype = DeviceType.objects.create(model='C9350-48P', slug='c9350-48p', manufacturer=manufacturer)
        self.device = Device.objects.create(name='sw1', site=self.site, role=self.role, device_type=dtype,
                                            primary_ip4=IPAddress.objects.create(address='192.0.2.4/24'))
        self.poller = DiscoveryPoller.objects.create(name='lab')
        now = timezone.now()
        self.data = {'filters': {'site': ['upgrade-lab'], 'role': ['access']}, 'profile': copy.deepcopy(PROFILE),
                     'operation': 'upgrade', 'scheduled_at': now - timedelta(minutes=1),
                     'start_before': now + timedelta(hours=2)}
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def scheduled(self, **overrides):
        return queue.schedule(self.user, {**self.data, **overrides})[0]

    def take(self, apply=True):
        return queue.claim(self.user, self.poller, 3, apply)

    def report(self, job, stage, seq=1):
        return queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'sequence': seq,
                                               'stage': stage, 'message': stage})


class UpgradeQueueTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()

    def test_schedule_freezes_targets_and_profile(self):
        job = self.scheduled()
        self.assertEqual(job.device_id, self.device.pk)
        self.assertEqual(job.address, '192.0.2.4')
        self.assertEqual(job.poller_id, self.poller.pk)
        self.data['profile']['target_version'] = '99.0.0'
        job.refresh_from_db()
        self.assertEqual(job.profile['target_version'], '17.18.4')

    def test_unknown_filters_never_expand_selection(self):
        for filters in ({}, {'siet': 'upgrade-lab'}, {'role': ['missing']}, {'site': []}, {'q': ''}):
            with self.assertRaises(queue.QueueError):
                self.scheduled(filters=filters)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_audit_worker_leaves_mutating_jobs_pending(self):
        self.scheduled()
        self.assertEqual(self.take(apply=False), [])
        audit = self.scheduled(operation='audit')
        self.assertEqual([j.pk for j in self.take(apply=False)], [audit.pk])

    def test_future_jobs_are_not_dispatched(self):
        self.scheduled(scheduled_at=timezone.now() + timedelta(hours=1))
        self.assertEqual(self.take(), [])

    def test_two_schedules_for_one_device_never_run_together(self):
        first, second = self.scheduled(), self.scheduled()
        self.assertEqual([j.pk for j in self.take()], [first.pk])
        self.assertEqual(self.take(), [])
        self.report(UpgradeJob.objects.get(pk=first.pk), 'blocked')
        self.assertEqual([j.pk for j in self.take()], [second.pk])

    def test_expired_start_window_is_not_dispatched(self):
        job = self.scheduled()
        UpgradeJob.objects.filter(pk=job.pk).update(start_before=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.take(), [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'expired')

    def test_window_is_checked_again_after_prechecks(self):
        job = self.scheduled()
        job = self.take()[0]
        UpgradeJob.objects.filter(pk=job.pk).update(start_before=timezone.now() - timedelta(seconds=1))
        with self.assertRaises(queue.QueueError):
            self.report(job, 'ready')
        job.refresh_from_db()
        self.assertIsNone(job.started_at)

    def test_audit_cannot_get_start_authorization(self):
        self.scheduled(operation='audit')
        with self.assertRaises(queue.QueueError):
            self.report(self.take()[0], 'ready')

    def test_claim_token_and_sequence_make_reports_idempotent(self):
        self.scheduled()
        job = self.take()[0]
        with self.assertRaises(queue.QueueError):
            queue.report(self.user, job.pk, {'claim_token': uuid.uuid4(), 'heartbeat': True})
        self.report(job, 'ready', 3)
        self.report(job, 'precheck', 2)
        self.report(job, 'ready', 3)
        job.refresh_from_db()
        self.assertEqual(job.stage, 'ready')
        self.assertEqual(len(job.events), 1)
        self.report(job, 'completed', 4)
        self.report(job, 'completed', 4)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')
        self.assertEqual(len(job.events), 2)

    def test_lost_worker_after_start_keeps_device_fenced(self):
        self.scheduled()
        job = self.take()[0]
        self.report(job, 'ready')
        UpgradeJob.objects.filter(pk=job.pk).update(last_seen_at=timezone.now() - timedelta(minutes=6))
        self.scheduled()
        self.assertEqual(self.take(), [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'recovery_required')
        # An original worker that was merely offline can finish its report.
        self.report(job, 'completed', 2)
        self.assertEqual(len(self.take()), 1)

    def test_lost_precheck_worker_is_not_requeued(self):
        self.scheduled()
        job = self.take()[0]
        UpgradeJob.objects.filter(pk=job.pk).update(last_seen_at=timezone.now() - timedelta(minutes=6))
        self.assertEqual(self.take(), [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')
        with self.assertRaises(queue.QueueError):
            self.report(job, 'ready', 2)

    def test_device_ip_or_ownership_changes_invalidate_schedule(self):
        job = self.scheduled()
        self.device.primary_ip4 = IPAddress.objects.create(address='192.0.2.5/24')
        self.device.save()
        self.assertEqual(self.take(), [])
        job.refresh_from_db()
        self.assertEqual(job.status, 'failed')

    def test_multiple_tags_require_one_explicit_poller(self):
        self.site.tags.add(Tag.objects.create(name='poller-other', slug='poller-other'))
        with self.assertRaises(queue.QueueError):
            self.scheduled()
        self.assertEqual(self.scheduled(poller='lab').poller, self.poller)

    def test_active_work_cannot_be_cancelled_or_silently_retried(self):
        self.scheduled()
        job = self.take()[0]
        with self.assertRaises(queue.QueueError):
            queue.cancel(self.user, job.pk)
        self.report(job, 'ready')
        self.report(job, 'failed', 2)
        job.refresh_from_db()
        self.assertEqual(job.status, 'recovery_required')
        with self.assertRaises(queue.QueueError):
            queue.cancel(self.user, job.pk, recovered=True)
        queue.cancel(self.user, job.pk, reason='Worker stopped and switch verified on original image.', recovered=True)
        with self.assertRaises(queue.QueueError):
            self.report(job, 'completed', 3)

    def test_api_does_not_expose_claim_tokens_or_allow_raw_edits(self):
        job = self.scheduled()
        job = self.take()[0]
        url = reverse('plugins-api:netbox_discovery-api:upgradejob-detail', args=[job.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200, response.data)
        self.assertNotIn('claim_token', response.data)
        self.assertEqual(self.client.patch(url, {'status': 'pending'}, format='json').status_code, 405)

    def test_unprivileged_worker_cannot_claim(self):
        self.scheduled()
        user = get_user_model().objects.create_user('ordinary')
        self.client.force_authenticate(user)
        response = self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/', {'name': 'lab', 'apply': True}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UpgradeJob.objects.get().status, 'pending')

    def test_run_permission_constraints_limit_claims(self):
        self.scheduled()
        user = get_user_model().objects.create_user('worker')
        permission = ObjectPermission.objects.create(name='restricted-upgrade-worker', actions=['run'],
                                                     constraints={'poller__name': 'other'})
        permission.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
        permission.users.add(user)
        self.assertEqual(queue.claim(user, self.poller, 3, True), [])

    def test_ui_list_detail_and_schedule_render_with_a_job(self):
        job = self.scheduled()
        from django.test import Client
        client = Client()
        client.force_login(self.user)
        for url in ('/plugins/discovery/upgrades/', '/plugins/discovery/upgrades/add/', job.get_absolute_url()):
            response = client.get(url)
            self.assertEqual(response.status_code, 200, url)


class UpgradeJobUiTest(UpgradeFixture, TestCase):
    def setUp(self):
        from django.test import Client
        self.setup_data()
        self.client = Client()
        self.client.force_login(self.user)
        self.job = self.scheduled()
        self.edit_url = reverse('plugins:netbox_discovery:upgradejob_edit', args=[self.job.pk])

    def payload(self, **changes):
        import yaml
        return {'operation': 'audit', 'profile': yaml.safe_dump(PROFILE),
                'scheduled_at': self.job.scheduled_at.isoformat(),
                'start_before': self.job.start_before.isoformat(),
                'last_updated': self.job.last_updated.isoformat(), 'description': 'Updated job', **changes}

    def grant(self, user, model, actions, constraints=None):
        permission = ObjectPermission.objects.create(name=str(uuid.uuid4()), actions=actions, constraints=constraints)
        permission.object_types.add(ContentType.objects.get_for_model(model))
        permission.users.add(user)

    def test_list_has_create_control_and_pending_detail_has_only_working_edit_action(self):
        from netbox.object_actions import AddObject, EditObject
        response = self.client.get(reverse('plugins:netbox_discovery:upgradejob_list'))
        self.assertEqual(response.status_code, 200)
        self.assertIn(AddObject, response.context['actions'])
        self.assertContains(response, '/plugins/discovery/upgrades/add/', count=2)
        response = self.client.get(self.job.get_absolute_url())
        self.assertEqual(list(response.context['actions']), [EditObject])
        self.assertContains(response, self.edit_url)
        response = self.client.get(self.edit_url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['form'].initial['operation'], 'upgrade')
        self.assertContains(response, PROFILE['image'])

    def test_edit_saves_plan_for_next_claim_without_changing_device_or_other_jobs(self):
        other = self.scheduled()
        profile = {**PROFILE, 'target_version': '17.18.5', 'image': 'cisco9k_iosxe.17.18.05.SPA.bin'}
        import yaml
        response = self.client.post(self.edit_url, self.payload(profile=yaml.safe_dump(profile), device=999,
                                    poller=999, status='running', claim_token=str(uuid.uuid4())))
        self.assertRedirects(response, self.job.get_absolute_url())
        self.job.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(self.job.profile, profile)
        self.assertEqual(self.job.operation, 'audit')
        self.assertEqual(self.job.description, 'Updated job')
        self.assertEqual(self.job.device_id, self.device.pk)
        self.assertEqual(self.job.poller_id, self.poller.pk)
        self.assertEqual(self.job.status, 'pending')
        self.assertIsNone(self.job.claim_token)
        self.assertEqual(other.profile, PROFILE)
        self.assertEqual(queue.assignment(self.take()[0])['profile'], profile)

    def test_invalid_profile_and_window_do_not_save(self):
        for changes in ({'profile': 'invalid: true'}, {'start_before': self.job.scheduled_at.isoformat()}):
            response = self.client.post(self.edit_url, self.payload(**changes))
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.context['form'].errors)
            self.job.refresh_from_db()
            self.assertEqual(self.job.operation, 'upgrade')

    def test_claim_between_loading_form_and_saving_rejects_edit(self):
        data = self.payload()
        self.take()
        response = self.client.post(self.edit_url, data)
        self.assertContains(response, 'Only pending jobs can be edited')
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'claimed')
        self.assertEqual(self.job.operation, 'upgrade')

    def test_active_and_closed_jobs_do_not_offer_edit_or_allow_direct_edit(self):
        for status in ('claimed', 'running', 'completed', 'cancelled', 'recovery_required'):
            UpgradeJob.objects.filter(pk=self.job.pk).update(status=status)
            response = self.client.get(self.job.get_absolute_url())
            self.assertNotContains(response, self.edit_url)
            self.assertRedirects(self.client.get(self.edit_url), self.job.get_absolute_url())
            response = self.client.post(self.edit_url, self.payload())
            self.assertContains(response, 'Only pending jobs can be edited')

    def test_stale_browser_form_cannot_overwrite_another_edit(self):
        data = self.payload()
        self.assertEqual(self.client.post(self.edit_url, data).status_code, 302)
        response = self.client.post(self.edit_url, {**data, 'description': 'Stale edit'})
        self.assertContains(response, 'changed while the form was open')
        self.job.refresh_from_db()
        self.assertEqual(self.job.description, 'Updated job')

    def test_view_only_user_has_no_create_or_edit_controls(self):
        user = get_user_model().objects.create_user('upgrade-viewer')
        self.grant(user, UpgradeJob, ['view'])
        self.client.force_login(user)
        response = self.client.get(reverse('plugins:netbox_discovery:upgradejob_list'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['actions']), [])
        self.assertEqual(self.client.get(self.edit_url).status_code, 403)
        self.assertEqual(self.client.post(self.edit_url, self.payload()).status_code, 403)

    def test_change_and_apply_object_constraints_are_enforced(self):
        user = get_user_model().objects.create_user('upgrade-editor')
        self.grant(user, Device, ['view'])
        self.grant(user, UpgradeJob, ['view', 'change'])
        self.grant(user, UpgradeJob, ['apply'], {'poller__name': 'other'})
        self.client.force_login(user)
        response = self.client.post(self.edit_url, self.payload())
        self.assertContains(response, 'requires apply permission')
        self.job.refresh_from_db()
        self.assertEqual(self.job.operation, 'upgrade')
        UpgradeJob.objects.filter(pk=self.job.pk).update(operation='audit')
        response = self.client.post(self.edit_url, self.payload(operation='upgrade'))
        self.assertContains(response, 'outside your apply permissions')
        self.job.refresh_from_db()
        self.assertEqual(self.job.operation, 'audit')


class UpgradeClaimConcurrencyTest(UpgradeFixture, TransactionTestCase):
    def setUp(self):
        self.setup_data()

    def test_overlapping_checkins_get_only_one_assignment(self):
        self.scheduled()
        self.scheduled()
        def claim():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                poller = DiscoveryPoller.objects.get(pk=self.poller.pk)
                return [j.pk for j in queue.claim(user, poller, 3, True)]
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: claim(), range(2)))
        self.assertEqual(sum(len(batch) for batch in results), 1)


class UpgradeApiPermissionTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()

    def test_read_only_token_cannot_claim_jobs(self):
        from users.models import Token
        self.scheduled()
        token = Token.objects.create(user=self.user, version=1, plaintext='a' * 40, write_enabled=False)
        self.client.force_authenticate(None)
        self.client.credentials(HTTP_AUTHORIZATION='Token ' + token.plaintext)
        response = self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/', {'name': 'lab', 'apply': True}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UpgradeJob.objects.get().status, 'pending')

    def test_schedule_requires_apply_permission_for_changes(self):
        user = get_user_model().objects.create_user('scheduler')
        add = ObjectPermission.objects.create(name='schedule-audits', actions=['add'])
        add.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
        add.users.add(user)
        view = ObjectPermission.objects.create(name='view-devices', actions=['view'])
        view.object_types.add(ContentType.objects.get_for_model(Device))
        view.users.add(user)
        with self.assertRaises(queue.QueueError):
            queue.schedule(user, self.data)
        job = queue.schedule(user, {**self.data, 'operation': 'audit'})[0]
        self.assertEqual(job.operation, 'audit')

    def test_schedule_respects_add_object_constraints(self):
        user = get_user_model().objects.create_user('restricted-scheduler')
        permission = ObjectPermission.objects.create(name='restricted-schedule', actions=['add'],
                                                     constraints={'poller__name': 'other'})
        permission.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
        permission.users.add(user)
        view = ObjectPermission.objects.create(name='see-devices', actions=['view'])
        view.object_types.add(ContentType.objects.get_for_model(Device))
        view.users.add(user)
        with self.assertRaises(queue.QueueError):
            queue.schedule(user, {**self.data, 'operation': 'audit'})
        self.assertFalse(UpgradeJob.objects.exists())

    def test_worker_with_only_scoped_run_permission_can_execute_the_rest_protocol(self):
        job = self.scheduled()
        user = get_user_model().objects.create_user('scoped-worker')
        permission = ObjectPermission.objects.create(name='lab-worker', actions=['run'], constraints={'poller__name': 'lab'})
        permission.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
        permission.users.add(user)
        self.client.force_authenticate(user)
        response = self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/',
                                    {'name': 'lab', 'limit': 1, 'apply': True}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        assignment = response.data['jobs'][0]
        self.assertEqual(assignment['id'], job.pk)
        path = f'/api/plugins/discovery/upgrade-jobs/{job.pk}/report/'
        for seq, stage in enumerate(('precheck', 'ready', 'completed'), 1):
            result = self.client.post(path, {'claim_token': assignment['claim_token'], 'sequence': seq,
                                            'stage': stage, 'message': stage}, format='json')
            self.assertEqual(result.status_code, 200, result.data)
        job.refresh_from_db()
        self.assertEqual(job.status, 'completed')

    def test_api_schedule_preview_and_create_match(self):
        payload = {**self.data, 'scheduled_at': self.data['scheduled_at'].isoformat(),
                   'start_before': self.data['start_before'].isoformat(), 'preview': True}
        path = '/api/plugins/discovery/upgrade-jobs/schedule/'
        response = self.client.post(path, payload, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['devices'][0]['id'], self.device.pk)
        self.assertFalse(UpgradeJob.objects.exists())
        payload['preview'] = False
        response = self.client.post(path, payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(len(response.data['jobs']), 1)

    def test_stale_running_job_can_be_recovered_without_waiting_for_a_dead_poller(self):
        self.scheduled()
        job = self.take()[0]
        self.report(job, 'ready')
        UpgradeJob.objects.filter(pk=job.pk).update(last_seen_at=timezone.now() - timedelta(minutes=6))
        job.refresh_from_db()
        self.assertTrue(job.heartbeat_stale)
        self.assertTrue(job.needs_recovery)
        queue.cancel(self.user, job.pk, 'Remote stopped, image and configuration verified.', recovered=True)
        job.refresh_from_db()
        self.assertEqual(job.status, 'cancelled')
