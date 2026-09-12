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

    def check_in(self):
        return self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/',
                                {'name': 'lab', 'apply': True}, format='json')

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
            self.assertContains(response, 'Upgrade poller last seen')
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
