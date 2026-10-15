from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import UpgradeJob
from netbox_discovery.upgrade_wait import waiting_details
from .test_upgrade_groups import GroupFixture


class WaitingDetailsTest(GroupFixture):
    def setUp(self):
        super().setUp()
        self.poller.upgrade_last_seen_at = timezone.now()
        self.poller.save()

    def details(self, job, user=None):
        job.refresh_from_db()
        return waiting_details(job, user or self.user)

    def test_cross_batch_dependency_names_the_active_job_without_mutating_it(self):
        blocker = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.claim()
        job = self.schedule(filters={'name': ['core-a']})['core-a']
        before = list(UpgradeJob.objects.values('pk', 'status', 'last_updated', 'message', 'sequence'))
        self.assertTrue(queue.dependencies_done(job))
        self.assertFalse(queue.exclusion_clear(job))
        details = self.details(job)
        self.assertEqual(details['code'], 'dependency')
        self.assertEqual([j.pk for j in details['jobs']], [blocker.pk])
        self.assertEqual(before, list(UpgradeJob.objects.values('pk', 'status', 'last_updated', 'message', 'sequence')))
        UpgradeJob.objects.filter(pk=blocker.pk).update(status='completed_with_warnings')
        self.assertEqual(self.details(job)['code'], 'pickup')

    def test_same_batch_unfinished_dependency(self):
        jobs = self.schedule()
        details = self.details(jobs['core-a'])
        self.assertEqual(details['code'], 'dependency')
        self.assertEqual({j.device_name for j in details['jobs']}, {'sw3a', 'sw3b'})

    def test_reverse_dependency_exclusion(self):
        blocker = self.schedule(filters={'name': ['core-a']})['core-a']
        self.claim()
        job = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.assertFalse(queue.exclusion_clear(job))
        self.assertEqual([j.pk for j in self.details(job)['jobs']], [blocker.pk])

    def test_group_capacity_includes_group_and_blocking_job(self):
        jobs = self.schedule(filters={'name': ['sw3a', 'sw3b']})
        self.claim()
        details = self.details(jobs['sw3b'])
        self.assertEqual(details['code'], 'group_capacity')
        self.assertIn('closet-3', details['message'])
        self.assertEqual([j.pk for j in details['jobs']], [jobs['sw3a'].pk])
        self.closet.max_concurrent = 0
        self.closet.save()
        self.assertEqual(self.details(jobs['sw3b'])['code'], 'pickup')

    def test_same_device_active_job(self):
        blocker = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.claim()
        job = self.schedule(filters={'name': ['sw3a']})['sw3a']
        details = self.details(job)
        self.assertEqual(details['code'], 'device_busy')
        self.assertEqual([j.pk for j in details['jobs']], [blocker.pk])

    def test_failed_related_job_requires_review(self):
        jobs = self.schedule()
        UpgradeJob.objects.filter(pk=jobs['sw3a'].pk).update(status='failed')
        self.assertEqual(self.details(jobs['core-a'])['code'], 'hold')

    def test_scheduled_start_and_missed_window(self):
        job = self.schedule(filters={'name': ['sw3a']})['sw3a']
        UpgradeJob.objects.filter(pk=job.pk).update(scheduled_at=timezone.now() + timedelta(minutes=30))
        self.assertEqual(self.details(job)['code'], 'scheduled')
        UpgradeJob.objects.filter(pk=job.pk).update(start_before=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.details(job)['code'], 'expired')

    def test_stale_poller_and_eligible_job(self):
        job = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.assertEqual(self.details(job)['code'], 'pickup')
        self.poller.upgrade_last_seen_at = timezone.now() - timedelta(minutes=6)
        self.poller.save()
        self.assertEqual(self.details(job)['code'], 'poller_stale')

    def test_non_pending_jobs_keep_the_worker_message(self):
        job = self.schedule(filters={'name': ['sw3a']})['sw3a']
        for status in ('claimed', 'running', 'held', 'failed', 'completed', 'cancelled', 'recovery_required'):
            with self.subTest(status=status):
                UpgradeJob.objects.filter(pk=job.pk).update(status=status)
                self.assertIsNone(self.details(job))

    def test_hidden_blockers_are_not_disclosed(self):
        self.schedule(filters={'name': ['sw3a']})
        self.claim()
        job = self.schedule(filters={'name': ['core-a']})['core-a']
        limited = get_user_model().objects.create_user('limited-viewer')
        details = self.details(job, limited)
        self.assertEqual(details['code'], 'dependency')
        self.assertEqual(details['jobs'], [])
        self.assertNotIn('sw3a', details['message'])

    def test_list_and_detail_show_linked_live_reason(self):
        blocker = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.claim()
        job = self.schedule(filters={'name': ['core-a']})['core-a']
        browser = Client()
        browser.force_login(self.user)
        for url in (reverse('plugins:netbox_discovery:upgradejob_list'), job.get_absolute_url()):
            with self.subTest(url=url):
                response = browser.get(url)
                self.assertContains(response, 'Waiting for dependency')
                self.assertContains(response, f'sw3a — job #{blocker.pk}')
                self.assertContains(response, f'href="{blocker.get_absolute_url()}"')
        UpgradeJob.objects.filter(pk=blocker.pk).update(status='completed')
        response = browser.get(job.get_absolute_url())
        self.assertNotContains(response, 'Waiting for dependency')
        self.assertContains(response, 'Awaiting pickup')

    def test_blocker_names_are_escaped(self):
        blocker = self.schedule(filters={'name': ['sw3a']})['sw3a']
        self.claim()
        job = self.schedule(filters={'name': ['core-a']})['core-a']
        UpgradeJob.objects.filter(pk=blocker.pk).update(device_name='<script>bad()</script>')
        browser = Client()
        browser.force_login(self.user)
        response = browser.get(job.get_absolute_url())
        self.assertContains(response, '&lt;script&gt;bad()&lt;/script&gt;')
        self.assertNotContains(response, '<script>bad()</script>')
