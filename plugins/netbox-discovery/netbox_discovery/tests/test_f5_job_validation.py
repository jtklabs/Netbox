"""Manual validation authorization, stale forms and worker delivery."""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from users.models import ObjectPermission

from netbox_discovery import f5_job_validation as validation, upgrade_queue as queue
from netbox_discovery.models import UpgradeJob
from .test_upgrades import UpgradeFixture
from .test_f5_job_rollback import F5_PROFILE


class F5ValidationTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.scheduled(profile=F5_PROFILE)
        self.job = queue.claim(self.user, self.poller, 1, True,
                               worker_version='0.2.4', supports_cancellation=True)[0]
        self.report(self.job, 'ready')
        self.progress(8, 2)
        self.url = reverse('plugins-api:netbox_discovery-api:upgradejob-force-validation', args=[self.job.pk])

    def progress(self, step, sequence):
        self.job = queue.report(self.user, self.job.pk, {
            'claim_token': self.job.claim_token, 'sequence': sequence,
            'stage': 'validating' if step == 8 else 'waf_requests', 'message': 'Waiting',
            'summary': {'manual_validation_step': step, 'phase': {'step': step}},
        })

    def test_each_step_is_independent_and_worker_must_complete(self):
        response = self.client.post(self.url, {'step': 8, 'reason': 'Checked nodes'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        self.job.refresh_from_db()
        self.assertEqual(self.job.status, 'running')
        self.assertIsNone(self.job.completed_at)
        self.assertEqual(self.job.sequence, 2)
        record = self.job.validation_overrides['8']
        self.assertEqual(record['username'], self.user.username)
        self.assertEqual(record['user_id'], self.user.pk)
        self.assertEqual(record['reason'], 'Checked nodes')
        heartbeat = self.client.post(
            reverse('plugins-api:netbox_discovery-api:upgradejob-report', args=[self.job.pk]),
            {'claim_token': str(self.job.claim_token), 'heartbeat': True}, format='json')
        self.assertTrue(heartbeat.data['supports_validation_overrides'])
        self.assertEqual(heartbeat.data['validation_overrides'], {'8': record})
        self.assertEqual(self.client.post(self.url, {'step': 9, 'reason': 'Too early'}, format='json').status_code, 409)
        self.progress(9, 3)
        self.assertEqual(self.job.validation_overrides, {'8': record})
        self.assertEqual(self.client.post(self.url, {'step': 8, 'reason': 'Stale form'}, format='json').status_code, 409)
        self.assertEqual(self.client.post(self.url, {'step': 9, 'reason': 'Quiet traffic'}, format='json').status_code, 200)
        finished = self.report(self.job, 'completed_with_warnings', 4)
        self.assertEqual(finished.status, 'completed_with_warnings')
        self.assertEqual(set(finished.validation_overrides), {'8', '9'})

    def test_duplicate_cannot_replace_original_audit_record(self):
        first = validation.force_pass(self.user, self.job.pk, 8, 'Original reason')
        with self.assertRaises(queue.QueueError):
            validation.force_pass(self.user, self.job.pk, 8, 'Changed reason')
        self.job.refresh_from_db()
        self.assertEqual(self.job.validation_overrides, first.validation_overrides)

    def test_wrong_step_missing_reason_or_ended_job_rejected(self):
        for body in ({'step': 7, 'reason': 'Invalid'}, {'step': 8, 'reason': ' '},
                     {'step': 9, 'reason': 'Premature'}):
            self.assertIn(self.client.post(self.url, body, format='json').status_code, (400, 409))
        self.report(self.job, 'validation_failed', 3)
        with self.assertRaises(queue.QueueError):
            validation.force_pass(self.user, self.job.pk, 8, 'Already ended')

    def test_cancel_request_wins(self):
        queue.cancel(self.user, self.job.pk)
        with self.assertRaises(queue.QueueError):
            validation.force_pass(self.user, self.job.pk, 8, 'Cancelled')

    def test_stale_legacy_non_f5_and_other_operations_rejected(self):
        for changes in (
            {'last_seen_at': timezone.now() - timedelta(minutes=6)},
            {'summary': {'phase': {'step': 8}}},
            {'profile': {'image': 'image.bin'}},
            {'operation': 'audit'},
            {'supports_cancellation': False},
            {'stage': 'postcheck'},
        ):
            with self.subTest(changes=changes):
                original = {key: getattr(self.job, key) for key in changes}
                UpgradeJob.objects.filter(pk=self.job.pk).update(**changes)
                with self.assertRaises(queue.QueueError):
                    validation.force_pass(self.user, self.job.pk, 8, 'Not eligible')
                UpgradeJob.objects.filter(pk=self.job.pk).update(**original)

    def test_permissions_and_object_scope(self):
        user = get_user_model().objects.create_user('validation-viewer')
        self.client.force_authenticate(user)
        self.assertEqual(self.client.post(self.url, {'step': 8, 'reason': 'No permission'}, format='json').status_code, 403)
        permission = ObjectPermission.objects.create(name='Other jobs only', actions=['view', 'change', 'apply'],
                                                     constraints={'pk': self.job.pk + 100})
        permission.users.add(user)
        permission.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
        # Reload to discard Django's cached permissions.
        self.client.force_authenticate(get_user_model().objects.get(pk=user.pk))
        self.assertEqual(self.client.post(self.url, {'step': 8, 'reason': 'Outside scope'}, format='json').status_code, 404)
        self.job.refresh_from_db()
        self.assertEqual(self.job.validation_overrides, {})

    def test_detail_controls_and_audit(self):
        browser = Client()
        browser.force_login(self.user)
        page = browser.get(self.job.get_absolute_url())
        self.assertContains(page, 'Force pass step 8')
        self.assertNotContains(page, 'Force pass step 9')
        response = browser.post(reverse('plugins:netbox_discovery:upgradejob_force_validation', args=[self.job.pk]),
                                {'step': '8', 'reason': 'Verified manually'}, follow=True)
        self.assertContains(response, 'Manual validation requested')
        self.assertContains(response, 'Verified manually')
        self.assertNotContains(response, '>Force pass step 8<')
