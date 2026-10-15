"""Standalone, read-only job wallboard. Python standard library only."""
import argparse
import json
import os
import ssl
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

TERMINAL = {'completed', 'completed_with_warnings', 'failed', 'expired', 'cancelled'}
ACTIVE = {'claimed', 'running', 'recovery_required'}
STATIC = Path(__file__).parent / 'static'


def now():
    return datetime.now(timezone.utc).isoformat()


def choice(value):
    return value.get('value', '') if isinstance(value, dict) else value


def object_id(value):
    return value.get('id') if isinstance(value, dict) else value


def related_label(value):
    if isinstance(value, dict):
        return value.get('name') or value.get('display') or f'Worker #{value.get("id")}'
    return f'Worker #{value}' if value is not None else ''


def api_token():
    """Read an environment token or a systemd credential supplied in memory."""
    token = os.getenv('NETBOX_TOKEN', '')
    if not token and os.getenv('CREDENTIALS_DIRECTORY'):
        token = (Path(os.environ['CREDENTIALS_DIRECTORY']) / 'netbox-token').read_text().strip()
    return token


class UpstreamError(Exception):
    """Safe to display; never includes upstream response bodies or credentials."""


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise UpstreamError('NetBox redirected the API request. Check NETBOX_URL and API authentication.')


class NetBox:
    def __init__(self, url, token, scheme='Token', ca_file=None):
        parsed = urlsplit(url)
        if parsed.scheme not in {'http', 'https'} or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError('NETBOX_URL must be an HTTP(S) base URL without credentials, query, or fragment.')
        if not token or scheme not in {'Token', 'Bearer'}:
            raise ValueError('Set NETBOX_TOKEN and use Token or Bearer for NETBOX_AUTH_SCHEME.')
        self.root = url.rstrip('/') + '/'
        self.api = self.root + 'api/'
        self.authorization = f'{scheme} {token}'
        self.opener = build_opener(NoRedirect(), HTTPSHandler(context=ssl.create_default_context(cafile=ca_file)))

    def get(self, url):
        # Do not forward the token to another origin or a login page through pagination.
        if not url.startswith(self.api) or urlsplit(url).fragment:
            raise UpstreamError('NetBox returned an unexpected pagination URL.')
        request = Request(url, headers={'Authorization': self.authorization, 'Accept': 'application/json'})
        try:
            with self.opener.open(request, timeout=20) as response:
                return json.load(response)
        except HTTPError as exc:
            raise UpstreamError(f'NetBox API returned HTTP {exc.code}. Check the URL and read permissions.') from None
        except (URLError, TimeoutError, OSError):
            raise UpstreamError('Cannot reach NetBox. Check connectivity and TLS trust.') from None
        except (ValueError, UnicodeError):
            raise UpstreamError('NetBox returned an invalid API response.') from None

    def records(self, path, params=None, first_page=False):
        url = self.api + path + '?' + urlencode({'limit': 200, **(params or {})}, doseq=True)
        rows, seen = [], set()
        while url:
            if url in seen:
                raise UpstreamError('NetBox returned a pagination loop.')
            seen.add(url)
            data = self.get(url)
            if not isinstance(data, dict) or not isinstance(data.get('results'), list):
                raise UpstreamError('NetBox returned an unexpected list response.')
            rows.extend(data['results'])
            if first_page:
                break
            url = urljoin(url, data['next']) if data.get('next') else None
        return rows

    def collect(self, recent_batches):
        path = 'plugins/discovery/upgrade-jobs/'
        # Keep every unfinished batch, even if newer history pushes it off page one.
        active = self.records(path, {'hide_finished': 'true'})
        recent = self.records(path, {'limit': 100, 'ordering': '-scheduled_at,-id', 'hide_finished': 'false'}, first_page=True)
        batch_ids = list(dict.fromkeys(job['batch_id'] for job in recent))[:recent_batches]
        batch_ids = list(dict.fromkeys(batch_ids + [job['batch_id'] for job in active]))
        jobs = {}
        for batch_id in batch_ids:
            for job in self.records(path, {'batch_id': batch_id, 'hide_finished': 'false'}):
                jobs[job['id']] = job
        # Preserve a newly-created unfinished job if the API changed between calls.
        for job in active:
            jobs.setdefault(job['id'], job)
        devices = {}
        ids = sorted({object_id(job['device']) for job in jobs.values()})
        for offset in range(0, len(ids), 100):
            for device in self.records('dcim/devices/', {'id': ids[offset:offset + 100]}):
                devices[device['id']] = device
        groups = self.records('plugins/discovery/upgrade-groups/')
        return list(jobs.values()), devices, groups


def normalize(jobs, devices, groups, tiers, root):
    """Keep frozen job ordering; live inventory supplies only role/site labels."""
    tier_rank = {slug: i for i, slug in enumerate(reversed(tiers))}
    group_map = {g['name']: g for g in groups}
    names = {object_id(j['device']): j['device_name'] for j in jobs}
    result = []
    fields = ('id', 'batch_id', 'device_name', 'scheduled_at', 'start_before', 'started_at',
              'completed_at', 'last_seen_at', 'poller_last_seen_at', 'heartbeat_stale', 'needs_recovery',
              'stage', 'message', 'held_reason', 'planned_wave', 'description')
    for job in jobs:
        row = {key: job.get(key) for key in fields}
        # Only expose the current event's allowlisted timing metadata. A merged
        # job summary can retain an older phase after the stage has changed.
        latest_event = (job.get('events') or [{}])[-1]
        event_summary = latest_event.get('summary') or {}
        phase = event_summary.get('phase') if isinstance(event_summary, dict) else None
        elapsed = phase.get('phase_elapsed_seconds') if isinstance(phase, dict) else None
        row['step_timing'] = None
        if (latest_event.get('stage') == job.get('stage') and
                type(elapsed) in (int, float) and 0 <= elapsed < 315360000):
            row['step_timing'] = {'name': str(phase.get('name') or ''),
                                  'elapsed_seconds': int(elapsed),
                                  'reported_at': latest_event.get('timestamp')}
            total_elapsed = phase.get('elapsed_seconds')
            if type(total_elapsed) in (int, float) and 0 <= total_elapsed < 315360000:
                row['step_timing']['total_elapsed_seconds'] = int(total_elapsed)
        device_id = object_id(job['device'])
        device = devices.get(device_id, {})
        role = device.get('role') or device.get('device_role') or {}
        site = device.get('site') or {}
        row.update(status=choice(job['status']), operation=choice(job['operation']),
                   device_id=device_id, role=role.get('name', 'Unknown tier'), role_slug=role.get('slug', ''),
                   tier_rank=tier_rank.get(role.get('slug'), len(tiers)), site=site.get('name', 'Unknown site'),
                   poller=related_label(job.get('poller')),
                   url=f'{root}plugins/discovery/upgrades/{job["id"]}/' if root else '',
                   groups=sorted(job.get('groups') or []),
                   waits_for=[{'id': pk, 'name': names.get(pk, f'Device #{pk}')} for pk in job.get('waits_for', [])],
                   events=[{key: event.get(key) for key in ('timestamp', 'stage', 'message')} for event in (job.get('events') or [])[-12:]])
        result.append(row)
    # Wave is authoritative. Tier/group breaks ties; the scheduler may run eligible
    # jobs concurrently, so this is a plan, not a promise of serial execution.
    result.sort(key=lambda j: (j['scheduled_at'] or '', j['batch_id'], j['planned_wave'] or 1,
                              j['tier_rank'], j['groups'], j['id']))
    used = sorted({name for job in result for name in job['groups']})
    return {'jobs': result, 'groups': [{'name': name, 'max_concurrent': group_map.get(name, {}).get('max_concurrent', 1),
                                      'missing': name not in group_map, 'stale': group_map.get(name, {}).get('stale', False)} for name in used],
            'tiers': list(reversed(tiers))}


class Monitor:
    def __init__(self, client, interval=5, recent_batches=8, tiers=None, demo=False):
        self.client, self.interval, self.recent_batches = client, interval, recent_batches
        self.tiers = tiers or ['core', 'distribution', 'access']
        self.demo = demo
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.data = {'jobs': [], 'groups': [], 'tiers': [], 'updated_at': None}
        self.error = 'Connecting to NetBox…'
        self.last_success = None

    def refresh(self):
        try:
            if self.demo:
                from demo import sample
                jobs, devices, groups = sample()
                root = ''
            else:
                jobs, devices, groups = self.client.collect(self.recent_batches)
                root = self.client.root
            data = normalize(jobs, devices, groups, self.tiers, root)
            data['updated_at'] = now()
            with self.lock:
                self.data, self.error = data, None
                self.last_success = time.monotonic()
        except UpstreamError as exc:
            with self.lock:
                self.error = str(exc)
        except Exception:
            # Never expose raw API data or tokens in a traceback or browser response.
            with self.lock:
                self.error = 'Unable to read the job data. Check API compatibility and configuration.'

    def run(self):
        while not self.stop.is_set():
            self.refresh()
            self.stop.wait(self.interval)

    def snapshot(self):
        with self.lock:
            error = self.error
            if not error and self.last_success is not None and time.monotonic() - self.last_success > max(30, self.interval * 3):
                error = 'The latest NetBox snapshot is overdue.'
            return {**self.data, 'error': error, 'demo': self.demo, 'poll_seconds': self.interval,
                    'server_time': now(), 'recent_batches': self.recent_batches}


def handler_for(monitor):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == '/api/status':
                return self.respond(json.dumps(monitor.snapshot()).encode(), 'application/json')
            if path == '/healthz':
                snapshot = monitor.snapshot()
                return self.respond(json.dumps({'ok': not snapshot['error']}).encode(), 'application/json', 503 if snapshot['error'] else 200)
            files = {'/': ('index.html', 'text/html; charset=utf-8'),
                     '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                     '/style.css': ('style.css', 'text/css; charset=utf-8')}
            if path not in files:
                return self.respond(b'Not found', 'text/plain', 404)
            name, mime = files[path]
            self.respond((STATIC / name).read_bytes(), mime)

        def respond(self, data, mime, status=200):
            self.send_response(status)
            self.send_header('Content-Type', mime)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; object-src 'none'; frame-ancestors 'self'; base-uri 'none'")
            self.end_headers()
            try:
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *_args):
            pass
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--demo', action='store_true', help='Serve clearly labeled, simulated jobs without NetBox access')
    parser.add_argument('--host', default=os.getenv('TRACKER_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.getenv('TRACKER_PORT', '8090')))
    args = parser.parse_args()
    try:
        interval = int(os.getenv('TRACKER_POLL_SECONDS', '5'))
        recent = int(os.getenv('TRACKER_RECENT_BATCHES', '8'))
        if not 2 <= interval <= 300 or not 1 <= recent <= 50:
            raise ValueError('Poll interval must be 2–300 seconds and recent batches 1–50.')
        client = None if args.demo else NetBox(os.getenv('NETBOX_URL', ''), api_token(),
                                             os.getenv('NETBOX_AUTH_SCHEME', 'Token'), os.getenv('NETBOX_CA_FILE') or None)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    tiers = [s.strip() for s in os.getenv('TRACKER_TIER_ROLES', 'core,distribution,access').split(',') if s.strip()]
    monitor = Monitor(client, interval, recent, tiers, args.demo)
    server = ThreadingHTTPServer((args.host, args.port), handler_for(monitor))
    server.daemon_threads = True
    threading.Thread(target=monitor.run, daemon=True).start()
    print(f'Job tracker: http://{args.host}:{args.port}' + (' (DEMO)' if args.demo else ''), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        monitor.stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
