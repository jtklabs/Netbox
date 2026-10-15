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

    def test_standards_list_shows_history_without_toggle(self):
        for operation in ('audit_config', 'remediate'):
            UpgradeJob.objects.all().update(operation=operation)
            url = reverse('plugins:netbox_discovery:standardsjob_list')
            for status in TERMINAL:
                with self.subTest(operation=operation, status=status):
                    UpgradeJob.objects.filter(pk=self.finished.pk).update(status=status)
                    for params in ({}, {'hide_finished': 'true'}, {'hide_finished': 'false'}, {'hide_finished': ''}):
                        response = self.browser.get(url, params)
                        self.assertEqual(self.rows(response), {self.pending.pk, self.finished.pk})
                        self.assertNotContains(response, 'Hide finished jobs')
                        self.assertNotIn('hide_finished', response.wsgi_request.GET)
                        self.assertNotIn('hide_finished', response.context['filter_form'].fields)

    def test_active_and_recovery_jobs_stay_visible(self):
        url = reverse('plugins:netbox_discovery:upgradejob_list')
        for status in ('pending', 'held', 'claimed', 'running', 'recovery_required'):
            UpgradeJob.objects.filter(pk=self.pending.pk).update(status=status)
            with self.subTest(status=status):
                self.assertEqual(self.rows(self.browser.get(url)), {self.pending.pk, self.finished.pk})

    def test_explicit_status_link_and_direct_detail_still_show_finished_job(self):
        response = self.browser.get(reverse('plugins:netbox_discovery:upgradejob_list'), {'status': 'completed'})
        self.assertEqual(self.rows(response), {self.finished.pk})
        self.assertNotContains(response, 'Hide finished jobs')
        self.assertEqual(self.browser.get(self.finished.get_absolute_url()).status_code, 200)

    def test_htmx_and_select_all_include_history_by_default(self):
        for scope, operation in (('upgrade', 'upgrade'), ('standards', 'audit_config')):
            UpgradeJob.objects.all().update(operation=operation)
            response = self.browser.get(reverse(f'plugins:netbox_discovery:{scope}job_list'), HTTP_HX_REQUEST='true')
            self.assertEqual(self.rows(response), {self.pending.pk, self.finished.pk})
            url = reverse('plugins:netbox_discovery:upgradejob_bulk_cancel')
            for suffix in ('', '&hide_finished=true'):
                response = self.browser.post(url + f'?job_scope={scope}' + suffix, {'_all': '1'})
                self.assertEqual(set(response.context['form'].initial['selection']), {self.pending.pk, self.finished.pk})

    def test_api_keeps_history_unless_explicitly_filtered(self):
        url = reverse('plugins-api:netbox_discovery-api:upgradejob-list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual({row['id'] for row in response.data['results']}, {self.pending.pk, self.finished.pk})
        response = self.client.get(url, {'hide_finished': 'true'})
        self.assertEqual({row['id'] for row in response.data['results']}, {self.pending.pk})

    def test_upgrade_list_has_no_default_filter_or_history_toggle(self):
        url = reverse('plugins:netbox_discovery:upgradejob_list')
        for params in ({}, {'hide_finished': 'true'}, {'hide_finished': 'false'}):
            with self.subTest(params=params):
                response = self.browser.get(url, params)
                self.assertEqual(self.rows(response), {self.pending.pk, self.finished.pk})
                self.assertNotContains(response, 'Hide finished jobs')
                self.assertNotIn('hide_finished', response.wsgi_request.GET)
                self.assertNotIn('hide_finished', response.context['filter_form'].fields)

    def test_explicit_status_filter_applies_to_select_all(self):
        url = reverse('plugins:netbox_discovery:upgradejob_bulk_cancel')
        response = self.browser.post(url + '?job_scope=upgrade&status=pending', {'_all': '1'})
        self.assertEqual(response.context['form'].initial['selection'], [self.pending.pk])
