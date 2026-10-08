import json

from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone
from users.models import ObjectPermission

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.models import UpgradeJob
from .test_upgrades import UpgradeFixture


class BulkJobTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.jobs = [self.scheduled(), self.scheduled()]
        self.pks = [job.pk for job in self.jobs]
        self.browser = Client()
        self.browser.force_login(self.user)

    def url(self, action):
        return reverse(f'plugins:netbox_discovery:upgradejob_bulk_{action}')

    def test_actions_and_checkboxes_are_shown(self):
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'))
        for label in ('Hold selected', 'Cancel selected', 'Delete selected', 'name="pk"'):
            self.assertContains(response, label)
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'), HTTP_HX_REQUEST='true')
        self.assertContains(response, 'Hold selected')

    def test_confirmation_then_hold_cancel_delete(self):
        for action, status in (('hold', 'held'), ('cancel', 'cancelled'), ('delete', None)):
            response = self.browser.post(self.url(action), {'pk': self.pks})
            self.assertContains(response, f'Confirm {action}')
            self.assertEqual(UpgradeJob.objects.count(), 2)
            response = self.browser.post(self.url(action), {
                'selection': json.dumps(self.pks), 'confirm': 'yes', 'reason': 'Maintenance postponed',
            })
            self.assertEqual(response.status_code, 302)
            if status:
                self.assertEqual(set(UpgradeJob.objects.values_list('status', flat=True)), {status})
            else:
                self.assertFalse(UpgradeJob.objects.exists())

    def test_hold_requires_reason(self):
        with self.assertRaisesRegex(queue.QueueError, 'reason'):
            queue.bulk_action(self.user, self.pks, 'hold')
        self.assertEqual(UpgradeJob.objects.filter(status='pending').count(), 2)

    def test_mixed_selection_is_atomic_for_every_action(self):
        UpgradeJob.objects.filter(pk=self.pks[-1]).update(status='claimed')
        for action in ('hold', 'cancel', 'delete'):
            with self.assertRaises(queue.QueueError):
                queue.bulk_action(self.user, self.pks, action, 'Pause')
            self.jobs[0].refresh_from_db()
            self.assertEqual(self.jobs[0].status, 'pending')
            self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_deletion_preserves_previously_executed_cancelled_job(self):
        UpgradeJob.objects.filter(pk=self.pks[-1]).update(status='cancelled', claimed_at=timezone.now())
        with self.assertRaises(queue.QueueError):
            queue.bulk_action(self.user, self.pks, 'delete')
        self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_all_selection_uses_filters_and_freezes_confirmation(self):
        UpgradeJob.objects.filter(pk=self.pks[-1]).update(status='held')
        response = self.browser.post(self.url('cancel') + '?status=pending', {'_all': '1'})
        self.assertEqual(response.context['form'].initial['selection'], [self.pks[0]])
        response = self.browser.post(self.url('cancel') + '?status=not-real', {'_all': '1'})
        self.assertEqual(response.status_code, 302)

    def test_missing_selection_and_invalid_confirmation_never_change_jobs(self):
        self.assertEqual(self.browser.post(self.url('delete'), {}).status_code, 302)
        for selection in ('null', '{}', '[true]', '[-1]', '"all"'):
            response = self.browser.post(self.url('delete'), {'confirm': 'yes', 'selection': selection})
            self.assertEqual(response.status_code, 200)
        self.assertEqual(UpgradeJob.objects.count(), 2)

    def test_object_permissions_are_enforced_for_entire_selection(self):
        user = get_user_model().objects.create_user('limited-bulk')
        for action in ('view', 'change', 'delete'):
            permission = ObjectPermission.objects.create(name=f'bulk-{action}', actions=[action],
                                                         constraints={'id': self.pks[0]})
            permission.object_types.add(ContentType.objects.get_for_model(UpgradeJob))
            permission.users.add(user)
        for action in ('hold', 'cancel', 'delete'):
            with self.assertRaises(queue.QueueError):
                queue.bulk_action(user, self.pks, action, 'Postponed')
        self.assertEqual(UpgradeJob.objects.filter(status='pending').count(), 2)
        self.assertEqual(queue.bulk_action(user, [self.pks[0]], 'hold', 'Postponed'), 1)

    def test_no_permission_or_get_cannot_mutate(self):
        self.assertEqual(self.browser.get(self.url('delete')).status_code, 405)
        self.browser.force_login(get_user_model().objects.create_user('no-bulk'))
        self.assertEqual(self.browser.post(self.url('delete'), {'pk': self.pks}).status_code, 403)
