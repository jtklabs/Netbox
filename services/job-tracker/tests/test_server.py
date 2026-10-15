import copy
import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from demo import sample
from server import Monitor, NetBox, NoRedirect, UpstreamError, normalize


class FakeClient(NetBox):
    def __init__(self, responses):
        self.api = 'https://netbox.test/netbox/api/'
        self.responses = iter(responses)
        self.urls = []

    def get(self, url):
        self.urls.append(url)
        return next(self.responses)


class PaginationTests(unittest.TestCase):
    def test_follows_all_pages_including_relative_next(self):
        client = FakeClient([{'results':[{'id':1}],'next':'?limit=1&offset=1'}, {'results':[{'id':2}],'next':None}])
        self.assertEqual(client.records('jobs/'), [{'id':1},{'id':2}])
        self.assertEqual(client.urls[1], 'https://netbox.test/netbox/api/jobs/?limit=1&offset=1')

    def test_cycles_and_invalid_shapes_fail(self):
        for response in [{'results':[], 'next':'?limit=200'}, {'detail':'No access'}]:
            with self.subTest(response=response), self.assertRaises(UpstreamError):
                FakeClient([response]).records('jobs/')

    def test_recent_discovery_does_not_scan_entire_history(self):
        client = FakeClient([{'results':[{'id':1}],'next':'?offset=200'}])
        self.assertEqual(client.records('jobs/', first_page=True), [{'id':1}])
        self.assertEqual(len(client.urls), 1)


class SecurityTests(unittest.TestCase):
    def test_token_stays_on_api_origin_and_subpath(self):
        client = NetBox('https://netbox.test/netbox', 'private-token')
        for url in ['https://other.test/api/jobs/', 'https://netbox.test/login/', 'http://netbox.test/netbox/api/jobs/']:
            with self.subTest(url=url), self.assertRaises(UpstreamError):
                client.get(url)

    def test_redirects_never_receive_authorization(self):
        with self.assertRaises(UpstreamError):
            NoRedirect().redirect_request(None,None,302,'Found',{},'https://other.test')

    def test_auth_header_and_timeout(self):
        client = NetBox('https://netbox.test/netbox', 'private-token', 'Bearer')
        with patch.object(client.opener, 'open', return_value=io.BytesIO(b'{"results": []}')) as opened:
            client.get(client.api + 'jobs/')
        request = opened.call_args.args[0]
        self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')
        self.assertEqual(request.get_method(), 'GET')
        self.assertEqual(opened.call_args.kwargs['timeout'], 20)

    def test_profiles_and_summaries_are_not_in_browser_payload(self):
        jobs, devices, groups = sample()
        jobs[0]['profile'] = {'password':'secret-never-render'}
        jobs[0]['summary'] = {'secret':'secret-never-render'}
        jobs[0]['events'][0]['summary'] = {'secret':'secret-never-render'}
        payload = normalize(jobs, devices, groups, ['core','distribution','access'], 'https://netbox.test/netbox/')
        self.assertNotIn('secret-never-render', json.dumps(payload))
        self.assertEqual(payload['jobs'][0]['url'], 'https://netbox.test/netbox/plugins/discovery/upgrades/4101/')


class StepTimingTests(unittest.TestCase):
    def test_current_phase_timing_is_allowlisted_and_stale_phase_is_not_used(self):
        jobs, devices, groups = sample()
        job = jobs[0]
        job['stage'] = 'reconnecting'
        phase = {'name':'Reconnect', 'phase_elapsed_seconds':123, 'elapsed_seconds':456, 'private':'secret-never-render'}
        job['summary'] = {'phase': phase}
        job['events'] = [{'stage':'reconnecting','timestamp':'2026-10-15T02:00:00Z','summary':{'phase':phase}}]
        def normalized():
            return normalize(jobs[:1],devices,groups,[], '')['jobs'][0]
        self.assertEqual(normalized()['step_timing'], {'name':'Reconnect','elapsed_seconds':123,'total_elapsed_seconds':456,'reported_at':'2026-10-15T02:00:00Z'})
        self.assertNotIn('secret-never-render',json.dumps(normalized()))
        job['stage'] = 'validating'
        self.assertIsNone(normalized()['step_timing'])
        job['stage'] = 'reconnecting'
        phase['phase_elapsed_seconds'] = -1
        self.assertIsNone(normalized()['step_timing'])


class CollectionTests(unittest.TestCase):
    def test_old_active_batches_and_finished_siblings_are_included(self):
        client = NetBox('https://netbox.test', 'token')
        jobs, _, _ = sample()
        old = {**jobs[0], 'id':1, 'batch_id':'old', 'status':'held'}
        sibling = {**old, 'id':2, 'status':'completed'}
        recent = {**jobs[1], 'batch_id':'new'}
        def records(path, params=None, first_page=False):
            if path == 'dcim/devices/': return []
            if path.endswith('upgrade-groups/'): return []
            if params.get('hide_finished') == 'true': return [old]
            if first_page: return [recent]
            return [old,sibling] if params['batch_id'] == 'old' else [recent]
        with patch.object(client, 'records', side_effect=records):
            collected, _, _ = client.collect(1)
        self.assertEqual({j['id'] for j in collected}, {1,2,recent['id']})

    def test_missing_inventory_and_group_definition_do_not_drop_job(self):
        jobs, _, _ = sample()
        data = normalize(jobs[:1], {}, [], ['core','distribution','access'], '')
        self.assertEqual(data['jobs'][0]['role'], 'Unknown tier')
        self.assertTrue(data['groups'][0]['missing'])
        self.assertEqual(data['groups'][0]['max_concurrent'], 1)

    def test_job_snapshots_preserved_and_choice_objects_normalized(self):
        jobs, devices, groups = sample()
        before = copy.deepcopy(jobs)
        result = normalize(jobs,devices,groups,['core','distribution','access'],'')
        self.assertEqual(jobs, before)
        self.assertEqual(result['tiers'], ['access','distribution','core'])
        self.assertEqual(result['jobs'][0]['status'], 'completed')
        self.assertEqual(result['jobs'][4]['waits_for'][0]['name'], 'nyc-access-03')
        self.assertEqual(result['jobs'][0]['events'][0]['timestamp'], jobs[0]['events'][0]['timestamp'])

    def test_scalar_foreign_keys_from_model_serializer(self):
        jobs, devices, groups = sample()
        jobs[0]['poller'] = 7
        jobs[0]['device'] = 1
        row = normalize(jobs[:1], devices, groups, ['access'], '')['jobs'][0]
        self.assertEqual(row['poller'], 'Worker #7')
        self.assertEqual(row['role'], 'Access')


class MonitorTests(unittest.TestCase):
    def test_failure_retains_last_complete_snapshot_then_recovers(self):
        client = NetBox('https://netbox.test', 'token')
        monitor = Monitor(client)
        with patch.object(client, 'collect', side_effect=[sample(), UpstreamError('Unavailable'), sample()]):
            monitor.refresh()
            first = monitor.snapshot()
            monitor.refresh()
            stale = monitor.snapshot()
            self.assertEqual(stale['jobs'],first['jobs'])
            self.assertEqual(stale['updated_at'],first['updated_at'])
            self.assertEqual(stale['error'],'Unavailable')
            monitor.refresh()
            self.assertIsNone(monitor.snapshot()['error'])

    def test_unexpected_errors_are_sanitized(self):
        client = NetBox('https://netbox.test', 'token')
        with patch.object(client, 'collect', side_effect=ValueError('sensitive-response')):
            monitor = Monitor(client)
            monitor.refresh()
        self.assertNotIn('sensitive-response', json.dumps(monitor.snapshot()))

    def test_stalled_refresh_is_unhealthy(self):
        monitor = Monitor(None, demo=True)
        with patch('server.time.monotonic', return_value=100):
            monitor.refresh()
        with patch('server.time.monotonic', return_value=131):
            self.assertIn('overdue', monitor.snapshot()['error'])


if __name__ == '__main__':
    unittest.main()
