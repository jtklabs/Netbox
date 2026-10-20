"""Cancellation handshake, authorization races and per-device fencing."""
from datetime import timedelta
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import UpgradeJob
from .test_upgrades import UpgradeFixture


class JobCancellationTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()

    def claimed(self):
        self.scheduled()
        return queue.claim(self.user, self.poller, 1, True, worker_version='0.2.2', supports_cancellation=True)[0]

    def test_request_preserves_claim_and_device_lock_until_ack(self):
        job = self.claimed()
        requested = queue.cancel(self.user, job.pk, 'Stop this one')
        self.assertEqual(requested.status, 'claimed')
        self.assertEqual(requested.claim_token, job.claim_token)
        self.assertTrue(requested.cancellation_pending)
        self.assertEqual(requested.cancel_requested_by, self.user)
        again = queue.cancel(self.user, job.pk, 'duplicate')
        self.assertEqual(again.cancel_requested_at, requested.cancel_requested_at)
        self.assertEqual(again.cancel_reason, 'Stop this one')
        self.scheduled()
        self.assertEqual(self.take(), [])
        stopped = self.report(job, 'cancelled')
        self.assertEqual(stopped.status, 'cancelled')
        self.assertIsNotNone(stopped.cancel_acknowledged_at)
        self.assertFalse(stopped.cancellation_pending)
        self.assertEqual(len(self.take()), 1)
        self.assertEqual(self.report(job, 'cancelled').status, 'cancelled')

    def test_cancellation_wins_ready_and_completion_races(self):
        job = self.claimed()
        queue.cancel(self.user, job.pk)
        for seq, stage in enumerate(('queued', 'ready', 'completed', 'failed'), 1):
            response = self.client.post(f'/api/plugins/discovery/upgrade-jobs/{job.pk}/report/',
                {'claim_token': str(job.claim_token), 'sequence': seq, 'stage': stage, 'message': stage}, format='json')
            self.assertEqual(response.status_code, 200, response.data)
            self.assertTrue(response.data['cancel_requested'])
            self.assertEqual(response.data['status'], 'claimed')
            self.assertEqual(response.data['sequence'], seq)
        job.refresh_from_db()
        self.assertIsNone(job.started_at)
        self.assertIsNone(job.completed_at)
        self.assertEqual(self.report(job, 'cancelled', 5).status, 'cancelled')

    def test_cancel_after_ready_requires_manual_recovery(self):
        job = self.claimed()
        self.report(job, 'ready')
        queue.cancel(self.user, job.pk)
        self.report(job, 'completed', 2)
        stopped = self.report(job, 'cancelled', 3)
        self.assertEqual(stopped.status, 'recovery_required')
        self.assertFalse(stopped.can_request_cancel)
        self.assertFalse(stopped.cancellation_pending)
        self.assertEqual(self.report(job, 'cancelled', 3).status, 'recovery_required')
        with self.assertRaises(queue.QueueError):
            self.report(job, 'completed', 4)
        self.scheduled()
        self.assertEqual(self.take(), [])
        with self.assertRaises(queue.QueueError):
            queue.cancel(self.user, job.pk, recovered=True)
        released = queue.cancel(self.user, job.pk, reason='Worker stopped; device verified', recovered=True)
        self.assertEqual(released.status, 'cancelled')
        self.assertIsNone(released.claim_token)

    def test_completed_job_cannot_be_cancelled(self):
        job = self.claimed()
        self.report(job, 'completed')
        with self.assertRaises(queue.QueueError):
            queue.cancel(self.user, job.pk)

    def test_no_request_no_stop_ack(self):
        job = self.claimed()
        with self.assertRaises(queue.QueueError):
            self.report(job, 'cancelled')

    def test_legacy_worker_does_not_claim_cancellation_capability(self):
        self.scheduled()
        response = self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/',
                                    {'name': 'lab', 'apply': True}, format='json')
        self.assertEqual(response.status_code, 200)
        job = UpgradeJob.objects.get(pk=response.data['jobs'][0]['id'])
        self.assertFalse(job.supports_cancellation)
        with self.assertRaisesMessage(queue.QueueError, 'does not support'):
            queue.cancel(self.user, job.pk)

    def test_capability_is_frozen_at_claim_and_heartbeat_delivers_request(self):
        self.scheduled()
        response = self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/',
            {'name': 'lab', 'apply': True, 'supports_cancellation': True, 'worker_version': '0.2.2'}, format='json')
        job = UpgradeJob.objects.get(pk=response.data['jobs'][0]['id'])
        self.assertTrue(job.supports_cancellation)
        queue.cancel(self.user, job.pk)
        response = self.client.post(f'/api/plugins/discovery/upgrade-jobs/{job.pk}/report/',
            {'claim_token': str(job.claim_token), 'heartbeat': True}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.data['cancel_requested'])

    def test_stale_request_does_not_release_device_and_can_still_be_acknowledged(self):
        job = self.claimed()
        queue.cancel(self.user, job.pk)
        UpgradeJob.objects.filter(pk=job.pk).update(last_seen_at=timezone.now()-timedelta(minutes=10),
                                                   start_before=timezone.now()-timedelta(minutes=1))
        queue.expire(UpgradeJob.objects.all(), timezone.now())
        job.refresh_from_db()
        self.assertEqual(job.status, 'recovery_required')
        self.assertTrue(job.cancellation_pending)
        self.assertEqual(self.report(job, 'queued').status, 'recovery_required')
        self.assertEqual(self.report(job, 'cancelled', 2).status, 'cancelled')

    def test_cancel_requires_change_permission_and_reports_require_claim_token(self):
        job = self.claimed()
        from django.contrib.auth import get_user_model
        user = get_user_model().objects.create_user('viewer')
        self.client.force_authenticate(user)
        response = self.client.post(f'/api/plugins/discovery/upgrade-jobs/{job.pk}/cancel/', {}, format='json')
        self.assertEqual(response.status_code, 403)
        job.refresh_from_db()
        self.assertIsNone(job.cancel_requested_at)
        self.client.force_authenticate(self.user)
        queue.cancel(self.user, job.pk)
        response = self.client.post(f'/api/plugins/discovery/upgrade-jobs/{job.pk}/report/',
            {'claim_token': '00000000-0000-0000-0000-000000000000', 'heartbeat': True}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_detail_button_and_request_confirmation(self):
        job = self.claimed()
        browser = Client()
        browser.force_login(self.user)
        self.assertContains(browser.get(job.get_absolute_url()), 'Cancel running job')
        response = browser.post(reverse('plugins:netbox_discovery:upgradejob_cancel', args=[job.pk]),
                                {'reason': 'Stop this job'}, follow=True)
        self.assertContains(response, 'Cancellation requested.')
        self.assertNotContains(response, 'Cancel running job')
        self.report(job, 'cancelled')
        self.assertContains(browser.get(job.get_absolute_url()), 'Worker stopped at')
