"""Upgrade worker contact is independent of pending jobs and SNMP activity."""
import uuid
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.api.serializers import DiscoveryPollerSerializer
from netbox_discovery.models import UpgradeJob
from .test_upgrades import UpgradeFixture


class UpgradePollerTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()

    def check_in(self, **extra):
        return self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/',
                                {'name': 'lab', 'apply': True, **extra}, format='json')

    def test_idle_checkin_updates_poller_without_claiming_future_job(self):
        job = self.scheduled(scheduled_at=timezone.now() + timedelta(hours=1))
        received = timezone.now()
        with patch('netbox_discovery.models.timezone.now', return_value=received):
            response = self.check_in()
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['jobs'], [])
        self.poller.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.poller.upgrade_last_seen_at, received)
        self.assertIsNone(job.last_seen_at)
        self.assertEqual(job.status, 'pending')
        response = self.client.get(reverse('plugins-api:netbox_discovery-api:upgradejob-detail', args=[job.pk]))
        self.assertIsNotNone(response.data['poller_last_seen_at'])
        self.assertIsNone(response.data['last_seen_at'])

    def test_checkin_updates_poller_when_queue_is_completely_empty(self):
        self.assertEqual(self.check_in().data['jobs'], [])
        self.poller.refresh_from_db()
        self.assertIsNotNone(self.poller.upgrade_last_seen_at)
        self.assertFalse(UpgradeJob.objects.exists())

    def test_snmp_checkin_does_not_count_as_upgrade_worker_contact(self):
        self.poller.touch(version='snmp-test', summary='Inventory completed')
        self.poller.refresh_from_db()
        self.assertIsNotNone(self.poller.last_seen_at)
        self.assertIsNone(self.poller.upgrade_last_seen_at)
        self.check_in()
        self.poller.refresh_from_db()
        seen = self.poller.upgrade_last_seen_at
        self.poller.touch(version='snmp-test')
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.upgrade_last_seen_at, seen)

    def test_active_heartbeat_and_progress_update_poller(self):
        self.scheduled()
        job = self.take()[0]
        for data in ({'heartbeat': True}, {'sequence': 1, 'stage': 'precheck', 'message': 'Collecting baseline'}):
            received = timezone.now()
            with patch('netbox_discovery.upgrade_queue.timezone.now', return_value=received):
                queue.report(self.user, job.pk, {'claim_token': job.claim_token, **data})
            self.poller.refresh_from_db()
            job.refresh_from_db()
            self.assertEqual(self.poller.upgrade_last_seen_at, received)
            self.assertIsNone(self.poller.last_seen_at)
            self.assertEqual(job.last_seen_at, received)

    def test_invalid_reports_do_not_mark_worker_alive(self):
        self.scheduled()
        job = self.take()[0]
        for data in ({'claim_token': uuid.uuid4(), 'heartbeat': True},
                     {'claim_token': job.claim_token, 'sequence': 1, 'stage': 'ready', 'message': 'Ready'}):
            # Ready cannot pass after the start window expires.
            UpgradeJob.objects.filter(pk=job.pk).update(start_before=timezone.now() - timedelta(seconds=1))
            with self.assertRaises(queue.QueueError):
                queue.report(self.user, job.pk, data)
            self.poller.refresh_from_db()
            self.assertIsNone(self.poller.upgrade_last_seen_at)

    def test_timestamps_are_distinct_on_list_and_detail_pages(self):
        job = self.scheduled(scheduled_at=timezone.now() + timedelta(hours=1))
        browser = Client()
        browser.force_login(self.user)
        for url in (reverse('plugins:netbox_discovery:upgradejob_list'), job.get_absolute_url()):
            response = browser.get(url)
            self.assertContains(response, 'Worker last seen')
            self.assertContains(response, 'Job last update')
            self.assertContains(response, 'Never checked in')
            self.assertContains(response, 'No job updates yet')
        self.check_in()
        for url in (reverse('plugins:netbox_discovery:upgradejob_list'), job.get_absolute_url()):
            response = browser.get(url)
            self.assertNotContains(response, 'Never checked in')
            self.assertContains(response, 'No job updates yet')

    def test_poller_timestamp_cannot_be_written_through_model_api(self):
        self.assertTrue(DiscoveryPollerSerializer().fields['upgrade_last_seen_at'].read_only)

    def test_worker_version_is_independent_of_scanner_and_visible(self):
        self.poller.touch(version='1.5.0+abc1234')
        self.assertEqual(self.check_in(worker_version='0.2.0').status_code, 200)
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '0.2.0')
        self.assertEqual(self.poller.version, '1.5.0+abc1234')
        self.poller.touch(version='1.5.1+def5678')
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '0.2.0')
        self.assertEqual(DiscoveryPollerSerializer(self.poller, context={'request': None}).data['worker_version'], '0.2.0')
        self.assertTrue(DiscoveryPollerSerializer().fields['worker_version'].read_only)
        browser = Client()
        browser.force_login(self.user)
        for url in (self.poller.get_absolute_url(), reverse('plugins:netbox_discovery:discoverypoller_list')):
            response = browser.get(url)
            self.assertContains(response, 'Worker version')
            self.assertContains(response, '0.2.0')

    def test_old_worker_reports_unknown_instead_of_preserving_new_version(self):
        self.check_in(worker_version='0.2.0')
        self.assertEqual(self.check_in().status_code, 200)
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '')

    def test_job_version_is_frozen_across_deployment_and_progress_replay(self):
        job = self.scheduled()
        response = self.check_in(worker_version='0.2.0')
        self.assertEqual(len(response.data['jobs']), 1)
        job.refresh_from_db()
        self.assertEqual(job.worker_version, '0.2.0')
        self.check_in(worker_version='0.2.1')
        report_url = f'/api/plugins/discovery/upgrade-jobs/{job.pk}/report/'
        response = self.client.post(report_url, {'claim_token': str(job.claim_token),
                                    'heartbeat': True, 'worker_version': '0.2.0'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '0.2.0')
        self.check_in(worker_version='0.2.1')
        response = self.client.post(report_url, {'claim_token': str(job.claim_token),
                                    'sequence': 1, 'stage': 'precheck', 'message': 'Baseline',
                                    'worker_version': '0.2.0'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.poller.refresh_from_db()
        job.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '0.2.1')
        self.assertEqual(job.worker_version, '0.2.0')
        response = self.client.get(reverse('plugins-api:netbox_discovery-api:upgradejob-detail', args=[job.pk]))
        self.assertEqual(response.data['worker_version'], '0.2.0')
        browser = Client()
        browser.force_login(self.user)
        response = browser.get(job.get_absolute_url())
        self.assertContains(response, 'Job worker version')
        self.assertContains(response, '0.2.0')

    def test_old_heartbeat_clears_latest_worker_version(self):
        job = self.scheduled()
        self.check_in(worker_version='0.2.0')
        job.refresh_from_db()
        queue.report(self.user, job.pk, {'claim_token': job.claim_token, 'heartbeat': True})
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '')
        job.refresh_from_db()
        self.assertEqual(job.worker_version, '0.2.0')

    def test_invalid_reports_cannot_change_version(self):
        job = self.scheduled()
        self.check_in(worker_version='0.2.0')
        response = self.client.post(f'/api/plugins/discovery/upgrade-jobs/{job.pk}/report/',
                                    {'claim_token': str(uuid.uuid4()), 'heartbeat': True,
                                     'worker_version': 'invalid-worker'}, format='json')
        self.assertEqual(response.status_code, 409)
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.worker_version, '0.2.0')

    def test_overlong_worker_version_rejected(self):
        self.assertEqual(self.check_in(worker_version='v' * 51).status_code, 400)
        self.poller.refresh_from_db()
        self.assertIsNone(self.poller.upgrade_last_seen_at)
