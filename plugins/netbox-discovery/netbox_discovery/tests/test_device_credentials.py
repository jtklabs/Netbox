from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.urls import reverse

from netbox_discovery.models import DeviceCredentialState
from .test_upgrades import UpgradeFixture


class DeviceCredentialTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.url = f'/api/plugins/discovery/device-credentials/{self.device.pk}/'

    def post(self, **data):
        return self.client.post(self.url, {'transport': 'ssh', **data}, format='json')

    def begin(self, profiles=None, **extra):
        return self.post(action='begin', profiles=profiles or ['primary', 'legacy', 'local'], **extra)

    def finish(self, token, attempted, profile='', status='rejected'):
        return self.post(action='finish', token=token, attempted=attempted, profile=profile, status=status)

    def state(self):
        return DeviceCredentialState.objects.get(device=self.device, transport='ssh')

    def test_remembers_success_across_workers_and_does_not_expose_lease_in_ui(self):
        lease = self.begin().data
        self.assertEqual(lease['profiles'], ['primary', 'legacy', 'local'])
        self.assertEqual(self.finish(lease['token'], ['primary', 'legacy'], 'legacy', 'authenticated').status_code, 200)
        self.assertEqual(self.state().selected_profile, 'legacy')
        self.assertIsNotNone(self.state().verified_at)
        self.assertEqual(self.begin().data['profiles'], ['legacy', 'primary', 'local'])

    def test_excludes_parallel_workers_and_cools_down_failed_discovery(self):
        lease = self.begin().data
        self.assertIn('already running', self.begin().data['error'])
        self.finish(lease['token'], ['primary', 'legacy', 'local'])
        self.assertIn('cooldown', self.begin().data['error'])
        self.assertIsNone(self.state().verified_at)
        DeviceCredentialState.objects.update(cooldown_until=timezone.now() - timedelta(seconds=1))
        self.assertIn('token', self.begin().data)

    def test_expired_crash_lease_can_be_replaced_but_stale_result_cannot_win(self):
        old = self.begin().data
        DeviceCredentialState.objects.update(lease_until=timezone.now() - timedelta(seconds=1))
        new = self.begin().data
        self.assertNotEqual(old['token'], new['token'])
        self.assertIn('error', self.finish(old['token'], ['primary'], 'primary', 'authenticated').data)
        self.assertEqual(str(self.state().lease_token), new['token'])

    def test_explicit_profile_stops_without_fallback(self):
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', assigned_profile='local')
        self.assertEqual(self.begin().data['profiles'], ['local'])

    def test_explicit_profile_can_opt_into_fallback(self):
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', assigned_profile='local', allow_fallback=True)
        self.assertEqual(self.begin(max_attempts=2).data['profiles'], ['local', 'primary'])

    def test_missing_saved_profile_is_deployment_failure_not_guessing(self):
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', selected_profile='local')
        self.assertIn('missing on this poller', self.begin(['primary', 'legacy']).data['error'])
        self.assertIsNone(self.state().lease_token)

    def test_rejects_forged_order_or_success(self):
        token = self.begin().data['token']
        self.assertIn('error', self.finish(token, ['legacy'], 'legacy', 'authenticated').data)
        self.assertIn('error', self.finish(token, ['primary'], 'legacy', 'authenticated').data)
        self.assertIsNone(self.state().verified_at)

    def test_transports_have_separate_selections(self):
        ssh = self.begin().data
        self.finish(ssh['token'], ['primary'], 'primary', 'authenticated')
        https = self.post(action='begin', transport='https', profiles=['legacy', 'primary'])
        self.assertEqual(https.data['profiles'], ['legacy', 'primary'])

    def test_permissions_and_invalid_input(self):
        for payload in ({'max_attempts': 4}, {'profiles': ['primary', 'primary']}, {'profiles': ['bad-name']}):
            self.assertEqual(self.begin(**payload).status_code, 400)
        user = get_user_model().objects.create_user(username='no-access')
        self.client.force_authenticate(user)
        self.assertEqual(self.begin().status_code, 403)

    def test_device_panel_and_preferences_form(self):
        self.client.force_login(self.user)
        url = reverse('plugins:netbox_discovery:device_credentials', args=[self.device.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'transport': 'ssh', 'assigned_profile': 'legacy'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.state().assigned_profile, 'legacy')
        self.assertFalse(self.state().allow_fallback)
        response = self.client.get(self.device.get_absolute_url())
        self.assertContains(response, 'Device credentials')
        self.assertContains(response, 'legacy')

    def test_preferences_cannot_bypass_active_selection_or_cooldown(self):
        self.client.force_login(self.user)
        lease = self.begin().data
        url = reverse('plugins:netbox_discovery:device_credentials', args=[self.device.pk])
        response = self.client.post(url, {'transport': 'ssh', 'assigned_profile': 'legacy'})
        self.assertContains(response, 'verification is running')
        self.finish(lease['token'], ['primary'])
        self.client.post(url, {'transport': 'ssh', 'forget_selection': True})
        self.assertIn('cooldown', self.begin().data['error'])

    def test_idle_checkin_records_profile_readiness(self):
        health = {'profiles': ['primary', 'legacy', 'local'], 'status': 'ready', 'message': 'Profiles present'}
        response = self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/',
                                    {'name': 'lab', 'credential_health': health}, format='json')
        self.assertEqual(response.status_code, 200)
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.credential_health, health)
        self.client.force_login(self.user)
        response = self.client.get(self.poller.get_absolute_url())
        self.assertContains(response, 'primary, legacy, local')
        self.assertContains(response, 'ready')
