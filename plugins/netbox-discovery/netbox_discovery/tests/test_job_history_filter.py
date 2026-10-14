from urllib.parse import parse_qs, urlsplit

from django.test import Client, TestCase
from django.urls import reverse

from netbox_discovery.models import UpgradeJob
from netbox_discovery.upgrade_choices import TERMINAL
from .test_upgrades import UpgradeFixture


class JobHistoryFilterTest(UpgradeFixture, TestCase):
    def setUp(self):
        self.setup_data()
        self.pending = self.scheduled()
        self.finished = self.scheduled()
        UpgradeJob.objects.filter(pk=self.finished.pk).update(status='completed')
        self.browser = Client()
        self.browser.force_login(self.user)

    def rows(self, response):
        self.assertEqual(response.status_code, 200)
        return {row.pk for row in response.context['table'].data}

    def test_both_lists_hide_every_terminal_status_and_can_show_history(self):
        for operation, name in (('upgrade', 'upgradejob_list'), ('audit_config', 'standardsjob_list')):
            UpgradeJob.objects.all().update(operation=operation)
            url = reverse(f'plugins:netbox_discovery:{name}')
            for status in TERMINAL:
                with self.subTest(operation=operation, status=status):
                    UpgradeJob.objects.filter(pk=self.finished.pk).update(status=status)
                    response = self.browser.get(url)
                    self.assertEqual(self.rows(response), {self.pending.pk})
                    self.assertContains(response, 'Hide finished jobs: On')
                    response = self.browser.get(url, {'hide_finished': 'false'})
                    self.assertEqual(self.rows(response), {self.pending.pk, self.finished.pk})
                    self.assertContains(response, 'Hide finished jobs: Off')

    def test_active_and_recovery_jobs_stay_visible(self):
        url = reverse('plugins:netbox_discovery:upgradejob_list')
        for status in ('pending', 'held', 'claimed', 'running', 'recovery_required'):
            UpgradeJob.objects.filter(pk=self.pending.pk).update(status=status)
            with self.subTest(status=status):
                self.assertEqual(self.rows(self.browser.get(url)), {self.pending.pk})

    def test_toggle_preserves_filters_and_resets_pagination(self):
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'),
                                    {'poller_id': self.poller.pk, 'sort': 'scheduled_at', 'page': 1})
        params = parse_qs(urlsplit(response.context['toggle_finished_url']).query)
        self.assertEqual(params, {'poller_id': [str(self.poller.pk)], 'sort': ['scheduled_at'],
                                  'hide_finished': ['false']})

    def test_explicit_status_link_and_direct_detail_still_show_finished_job(self):
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'), {'status': 'completed'})
        self.assertEqual(self.rows(response), {self.finished.pk})
        self.assertFalse(response.context['hide_finished'])
        self.assertEqual(self.browser.get(self.finished.get_absolute_url()).status_code, 200)

    def test_htmx_and_select_all_use_the_same_default_filter(self):
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'), HTTP_HX_REQUEST='true')
        self.assertEqual(self.rows(response), {self.pending.pk})
        url = reverse('plugins:netbox_discovery:upgradejob_bulk_cancel')
        response = self.browser.post(url + '?job_scope=upgrade', {'_all': '1'})
        self.assertEqual(response.context['form'].initial['selection'], [self.pending.pk])
        response = self.browser.post(url + '?job_scope=upgrade&hide_finished=false', {'_all': '1'})
        self.assertEqual(set(response.context['form'].initial['selection']), {self.pending.pk, self.finished.pk})

    def test_api_keeps_history_unless_explicitly_filtered(self):
        url = reverse('plugins-api:netbox_discovery-api:upgradejob-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['id'] for row in response.data['results']}, {self.pending.pk, self.finished.pk})
        response = self.client.get(url, {'hide_finished': 'true'})
        self.assertEqual({row['id'] for row in response.data['results']}, {self.pending.pk})
