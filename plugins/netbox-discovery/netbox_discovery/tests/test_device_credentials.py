from datetime import timedelta
from django.test import TestCase
from django.test import TransactionTestCase
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
        saved = self.begin().data
        self.assertEqual(saved['profiles'], ['legacy'])
        self.assertTrue(saved['pinned'])

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

    def test_saved_profile_survives_rejections_timeouts_and_cooldowns(self):
        verified_at = timezone.now()
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', selected_profile='legacy', verified_at=verified_at)
        for status in ('rejected', 'unavailable', 'rejected'):
            lease = self.begin().data
            self.assertEqual(lease['profiles'], ['legacy'])
            self.finish(lease['token'], ['legacy'], status=status)
            state = self.state()
            self.assertEqual(state.selected_profile, 'legacy')
            self.assertEqual(state.verified_at, verified_at)
            self.assertIn('cooldown', self.begin().data['error'])
            DeviceCredentialState.objects.update(cooldown_until=timezone.now() - timedelta(seconds=1))
        lease = self.begin().data
        self.finish(lease['token'], ['legacy'], 'legacy', 'authenticated')
        self.assertEqual(self.state().selected_profile, 'legacy')
        self.assertIsNone(self.state().cooldown_until)

    def test_saved_profile_cannot_be_replaced_by_a_different_success_report(self):
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', selected_profile='legacy')
        lease = self.begin().data
        self.assertIn('error', self.finish(lease['token'], ['legacy', 'local'], 'local', 'authenticated').data)
        self.assertEqual(self.state().selected_profile, 'legacy')

    def test_old_fallback_lease_cannot_replace_saved_profile(self):
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', selected_profile='legacy')
        lease = self.begin().data
        DeviceCredentialState.objects.update(candidates=['legacy', 'local'])
        self.assertIn('rediscovery', self.finish(lease['token'], ['legacy', 'local'], 'local', 'authenticated').data['error'])
        self.assertEqual(self.state().selected_profile, 'legacy')

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

    def test_read_only_api_token_cannot_reserve_attempts(self):
        from users.models import Token
        self.client.force_authenticate(self.user, Token(user=self.user, write_enabled=False))
        self.assertEqual(self.begin().status_code, 403)
        self.assertFalse(DeviceCredentialState.objects.exists())

    def test_device_panel_and_preferences_form(self):
        self.client.force_login(self.user)
        url = reverse('plugins:netbox_discovery:device_credentials', args=[self.device.pk])
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'transport': 'ssh', 'assigned_profile': 'legacy'})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.state().assigned_profile, 'legacy')
        self.assertNotContains(self.client.get(url), 'Allow fallback')
        self.assertContains(self.client.get(url), 'Rediscover credentials')
        response = self.client.get(self.device.get_absolute_url())
        self.assertContains(response, 'Device credentials')
        self.assertContains(response, 'legacy')

    def test_explicit_rediscovery_clears_assignment_and_saved_profile(self):
        DeviceCredentialState.objects.create(device=self.device, transport='ssh', assigned_profile='local', selected_profile='legacy')
        self.client.force_login(self.user)
        url = reverse('plugins:netbox_discovery:device_credentials', args=[self.device.pk])
        # Simply saving preferences does not discard the known working account.
        self.client.post(url, {'transport': 'ssh', 'assigned_profile': ''})
        self.assertEqual(self.state().selected_profile, 'legacy')
        response = self.client.post(url, {'transport': 'ssh', 'assigned_profile': 'local', 'forget_selection': True})
        self.assertEqual(response.status_code, 302)
        self.assertEqual(self.state().assigned_profile, '')
        self.assertEqual(self.state().selected_profile, '')
        lease = self.begin().data
        self.assertEqual(lease['profiles'], ['primary', 'legacy', 'local'])
        self.assertFalse(lease['pinned'])
        self.finish(lease['token'], ['primary'], 'primary', 'authenticated')
        self.assertEqual(self.begin().data['profiles'], ['primary'])

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
        self.client.post('/api/plugins/discovery/upgrade-jobs/check-in/', {'name': 'lab'}, format='json')
        self.poller.refresh_from_db()
        self.assertEqual(self.poller.credential_health, {})


class ConcurrentCredentialTest(UpgradeFixture, TransactionTestCase):
    def test_two_workers_cannot_both_reserve_first_contact(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections
        from netbox_discovery.api.credentials import exchange
        self.setup_data()
        barrier = Barrier(2)
        device_id = self.device.pk

        def begin():
            close_old_connections()
            try:
                barrier.wait(timeout=10)
                return exchange(device_id, {'action': 'begin', 'transport': 'ssh',
                                            'profiles': ['primary', 'legacy', 'local'], 'max_attempts': 3})
            finally:
                close_old_connections()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: begin(), range(2)))
        self.assertEqual(sum('token' in result for result in results), 1)
        self.assertEqual(sum('error' in result for result in results), 1)
        self.assertEqual(DeviceCredentialState.objects.filter(device_id=device_id).count(), 1)
