"""Confirmed F5 recovery, including races with the original device reservation."""
from datetime import timedelta
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from django.contrib.auth import get_user_model

from netbox_discovery import f5_job_rollback as rollback, upgrade_queue as queue
from netbox_discovery.models import UpgradeJob
from .test_upgrades import UpgradeFixture, PROFILE

F5_PROFILE = {**PROFILE, 'image': 'BIGIP-17.5.1.8-0.0.19.iso', 'target_version': '17.5.1.8'}
PLAN = {'volume': 'HD1.1', 'from_volume': 'HD1.2', 'previous_version': '17.1.1.3', 'previous_build': '0.0.5'}


class F5RollbackTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()

    def source(self, status='completed'):
        self.scheduled(profile=F5_PROFILE)
        job = self.take()[0]
        self.report(job, 'ready')
        if status != 'running':
            self.report(job, status, 2)
        job.refresh_from_db()
        job.summary = {'rollback_plan': dict(PLAN)}
        job.save()
        return job

    def claim_rollback(self):
        return queue.claim(self.user, self.poller, 3, True, worker_version='0.2.3',
                           supports_cancellation=True, supports_f5_rollback=True)

    def test_confirmation_is_exact_and_does_not_accept_a_target_from_browser(self):
        source = self.source()
        url = reverse('plugins-api:netbox_discovery-api:upgradejob-rollback', args=[source.pk])
        for name in ('', 'wrong', source.device_name.upper(), source.device_name+' '):
            response = self.client.post(url, {'device_name': name}, format='json')
            self.assertIn(response.status_code, (400, 409))
            self.assertEqual(UpgradeJob.objects.count(), 1)
        response = self.client.post(url, {'device_name': source.device_name, 'volume': 'HD1.9'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        job = UpgradeJob.objects.get(pk=response.data['id'])
        self.assertEqual(job.rollback_plan['volume'], 'HD1.1')
        self.assertEqual(job.rollback_of, source)
        self.assertEqual(job.operation, 'f5_rollback')
        self.assertEqual(job.requested_by, self.user)
        self.assertEqual(job.profile, source.profile)
        self.assertEqual(job.poller, source.poller)
        again = self.client.post(url, {'device_name': source.device_name}, format='json')
        self.assertEqual(again.data['id'], job.pk)
        self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_recovery_reservation_transfers_only_on_capable_claim(self):
        source = self.source('recovery_required')
        job = rollback.request(self.user, source.pk, source.device_name)
        source.refresh_from_db()
        self.assertEqual(source.status, 'recovery_required')
        self.assertEqual(self.take(), [])
        claimed = self.claim_rollback()
        self.assertEqual([item.pk for item in claimed], [job.pk])
        source.refresh_from_db()
        self.assertEqual(source.status, 'cancelled')
        self.assertIsNone(source.claim_token)
        self.assertEqual(UpgradeJob.objects.filter(device=self.device, status__in=('claimed', 'running', 'recovery_required')).count(), 1)
        assignment = queue.assignment(claimed[0])
        self.assertEqual(assignment['rollback_plan']['volume'], 'HD1.1')
        self.assertEqual(assignment['rollback_of'], source.pk)
        # Even a failure before ready must retain the transferred recovery lock.
        self.assertEqual(self.report(claimed[0], 'failed').status, 'recovery_required')

    def test_cancelling_pending_rollback_keeps_original_recovery_fence(self):
        source = self.source('recovery_required')
        job = rollback.request(self.user, source.pk, source.device_name)
        queue.cancel(self.user, job.pk)
        source.refresh_from_db()
        self.assertEqual(source.status, 'recovery_required')

    def test_stale_or_cancelled_claimed_rollback_keeps_recovery_fence(self):
        source = self.source()
        job = rollback.request(self.user, source.pk, source.device_name)
        job = self.claim_rollback()[0]
        UpgradeJob.objects.filter(pk=job.pk).update(last_seen_at=timezone.now()-timedelta(minutes=10))
        queue.expire(UpgradeJob.objects.all(), timezone.now())
        job.refresh_from_db()
        self.assertEqual(job.status, 'recovery_required')
        queue.cancel(self.user, job.pk)
        self.assertEqual(self.report(job, 'cancelled').status, 'recovery_required')

    def test_running_source_is_not_rolled_back_under_its_worker(self):
        source = self.source('running')
        with self.assertRaisesMessage(queue.QueueError, 'wait for its worker'):
            rollback.request(self.user, source.pk, source.device_name)

    def test_missing_partition_or_wrong_device_family_is_not_guessed(self):
        source = self.source()
        source.summary = {}
        source.save()
        with self.assertRaisesMessage(queue.QueueError, 'not recorded'):
            rollback.request(self.user, source.pk, source.device_name)
        source.summary = {'rollback_volume': 'HD1.1'}
        source.save()
        self.assertEqual(rollback.plan_for(source)['volume'], 'HD1.1')
        source.profile = PROFILE
        source.save()
        with self.assertRaisesMessage(queue.QueueError, 'only for F5'):
            rollback.request(self.user, source.pk, source.device_name)

    def test_newer_upgrade_or_other_job_blocks_old_rollback(self):
        source = self.source()
        newer = self.scheduled(profile=F5_PROFILE)
        with self.assertRaisesMessage(queue.QueueError, 'Another queued'):
            rollback.request(self.user, source.pk, source.device_name)
        UpgradeJob.objects.filter(pk=newer.pk).update(status='completed', started_at=timezone.now())
        with self.assertRaisesMessage(queue.QueueError, 'newer upgrade'):
            rollback.request(self.user, source.pk, source.device_name)

    def test_identity_is_rechecked_before_claim(self):
        source = self.source('recovery_required')
        job = rollback.request(self.user, source.pk, source.device_name)
        self.device.name = 'renamed'
        self.device.save()
        self.assertEqual(self.claim_rollback(), [])
        source.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(source.status, 'recovery_required')
        self.assertEqual(job.status, 'failed')

    def test_ui_has_confirmation_warning_and_read_only_get(self):
        source = self.source()
        browser = Client()
        browser.force_login(self.user)
        self.assertContains(browser.get(source.get_absolute_url()), 'Roll back F5')
        url = reverse('plugins:netbox_discovery:upgradejob_rollback', args=[source.pk])
        response = browser.get(url)
        for text in ('Immediate reboot. No traffic drain.', 'HD1.1', source.device_name, 'name="device_name"'):
            self.assertContains(response, text)
        self.assertEqual(UpgradeJob.objects.count(), 1)
        response = browser.post(url, {'device_name': 'wrong'})
        self.assertContains(response, 'Type the exact device name')
        self.assertEqual(UpgradeJob.objects.count(), 1)
        response = browser.post(url, {'device_name': source.device_name})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_unprivileged_users_cannot_trigger_rollback(self):
        source = self.source()
        user = get_user_model().objects.create_user('rollback-viewer')
        self.client.force_authenticate(user)
        response = self.client.post(reverse('plugins-api:netbox_discovery-api:upgradejob-rollback', args=[source.pk]),
                                    {'device_name': source.device_name}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(UpgradeJob.objects.count(), 1)

    def test_cannot_bypass_confirmation_by_scheduling_requeue_or_edit(self):
        source = self.source()
        with self.assertRaises(queue.QueueError):
            self.scheduled(operation='f5_rollback', profile=F5_PROFILE)
        job = rollback.request(self.user, source.pk, source.device_name)
        with self.assertRaisesMessage(queue.QueueError, 'cannot be edited'):
            queue.edit_pending(self.user, job.pk, {'operation': 'upgrade'})
        with self.assertRaisesMessage(queue.QueueError, 'cannot be re-queued'):
            queue.requeue(self.user, job.pk)


from concurrent.futures import ThreadPoolExecutor
from django.db import close_old_connections
from django.test import TransactionTestCase


class ConcurrentF5RollbackTest(UpgradeFixture, TransactionTestCase):
    def setUp(self):
        self.setup_data()

    def test_concurrent_confirmations_create_one_job(self):
        self.scheduled(profile=F5_PROFILE)
        source = self.take()[0]
        self.report(source, 'ready')
        self.report(source, 'completed', 2)
        UpgradeJob.objects.filter(pk=source.pk).update(summary={'rollback_plan': PLAN})
        def confirm():
            close_old_connections()
            try:
                user = get_user_model().objects.get(pk=self.user.pk)
                return rollback.request(user, source.pk, source.device_name).pk
            finally:
                close_old_connections()
        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: confirm(), range(2)))
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(UpgradeJob.objects.filter(rollback_of=source).count(), 1)
