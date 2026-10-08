from dcim.models import Platform
from django.test import TestCase
from extras.models import Tag

from netbox_discovery import upgrade_queue as queue
from netbox_discovery.clearpass_cluster import snapshot
from netbox_discovery.upgrade_views import UpgradePlanForm, plan_initial
from . import test_remediation


class ClearPassClusterTest(TestCase):
    def setUp(self):
        test_remediation.RemediationTest.setUp(self)
        self.publisher, self.subscriber = self.devices
        platform = Platform.objects.create(name='ClearPass', slug='aruba-clearpass')
        self.pubtag = Tag.objects.create(name='clearpass-publisher', slug='clearpass-publisher')
        self.subtag = Tag.objects.create(name='clearpass-subscriber', slug='clearpass-subscriber')
        for device, tag in zip(self.devices, (self.pubtag, self.subtag)):
            device.platform = platform
            device.save()
            device.tags.add(tag)
        self.data.update(filters={'id': [self.publisher.pk]},
                         profile={'features': ['ntp'], 'mode': 'add', 'allow_clearpass_cluster_changes': True})

    def test_schedule_pins_global_members_outside_target_filter(self):
        job = queue.schedule(self.user, self.data)[0]
        self.assertEqual(job.standards_snapshot['clearpass_cluster'], snapshot(self.user, self.publisher))
        self.assertEqual(len(job.standards_snapshot['clearpass_cluster']), 2)
        self.assertTrue(plan_initial(job)['allow_clearpass_cluster_changes'])

    def test_missing_approval_and_subscriber_apply_are_blocked(self):
        with self.assertRaisesRegex(queue.QueueError, 'cluster-wide changes'):
            queue.schedule(self.user, {**self.data, 'profile': {'features': ['ntp'], 'mode': 'add'}})
        with self.assertRaisesRegex(queue.QueueError, 'publisher only'):
            queue.schedule(self.user, {**self.data, 'filters': {'id': [self.subscriber.pk]}})

    def test_audit_all_members_does_not_require_approval(self):
        jobs = queue.schedule(self.user, {**self.data, 'filters': {'site': [self.site.slug]},
                                         'operation': 'audit_config',
                                         'profile': {'features': ['ntp'], 'mode': 'add'}})
        self.assertEqual(len(jobs), 2)
        self.assertEqual(jobs[0].standards_snapshot['clearpass_cluster'], jobs[1].standards_snapshot['clearpass_cluster'])

    def test_ambiguous_tags_are_rejected(self):
        self.subscriber.tags.add(self.pubtag)
        with self.assertRaisesRegex(queue.QueueError, 'exactly one'):
            snapshot(self.user, self.publisher)
        self.subscriber.tags.remove(self.subtag)
        with self.assertRaisesRegex(queue.QueueError, 'exactly one'):
            snapshot(self.user, self.publisher)

    def test_ready_gate_requires_current_scope_and_worker_acknowledgment(self):
        queue.schedule(self.user, self.data)
        job = queue.claim(self.user, self.poller, 1, True)[0]
        data = {'claim_token': job.claim_token, 'sequence': 1, 'stage': 'ready', 'message': 'Apply',
                'summary': {'standards_revisions': [{'standard_id': p['standard_id'], 'revision': p['revision']}
                                                   for p in job.standards_snapshot['revisions']]}}
        with self.assertRaisesRegex(queue.QueueError, 'acknowledge'):
            queue.report(self.user, job.pk, data)
        data['summary']['clearpass_cluster'] = job.standards_snapshot['clearpass_cluster']
        ip = self.subscriber.primary_ip4
        ip.address = '192.0.2.99/24'
        ip.save()
        with self.assertRaisesRegex(queue.QueueError, 'scope'):
            queue.report(self.user, job.pk, data)
        ip.address = '192.0.2.21/24'
        ip.save()
        self.publisher.platform = None
        self.publisher.save()
        with self.assertRaisesRegex(queue.QueueError, 'ClearPass devices'):
            queue.report(self.user, job.pk, data)
        self.publisher.platform = self.subscriber.platform
        self.publisher.save()
        self.assertEqual(queue.report(self.user, job.pk, data).status, 'running')

    def test_custom_job_form_and_audit_strip_approval(self):
        data = {**self.data, 'profile_source': 'custom', 'features': ['ntp'], 'remediation_mode': 'add',
                'allow_clearpass_cluster_changes': True}
        data.pop('profile')
        form = UpgradePlanForm(data)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertTrue(form.cleaned_data['profile']['allow_clearpass_cluster_changes'])
        form = UpgradePlanForm({**data, 'operation': 'audit_config'})
        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('allow_clearpass_cluster_changes', form.cleaned_data['profile'])

    def test_permissions_cover_all_cluster_members(self):
        from django.contrib.auth import get_user_model
        from django.contrib.contenttypes.models import ContentType
        from users.models import ObjectPermission
        from dcim.models import Device
        user = get_user_model().objects.create_user('limited', password='test-only')
        permission = ObjectPermission.objects.create(name='View devices', actions=['view'])
        permission.object_types.add(ContentType.objects.get_for_model(Device))
        permission.users.add(user)
        user = get_user_model().objects.get(pk=user.pk)
        self.assertEqual(len(snapshot(user, self.publisher)), 2)
        with self.assertRaisesRegex(queue.QueueError, 'change permission'):
            snapshot(user, self.publisher, apply=True)
