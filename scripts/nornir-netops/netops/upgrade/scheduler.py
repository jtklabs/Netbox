"""One cron tick: replay progress, claim a bounded batch, run it, then exit."""
import copy
import fcntl
import hashlib
import ipaddress
import json
import os
import tempfile
import threading
import time
from pathlib import Path
from uuid import UUID
from contextlib import contextmanager

from .. import archive
from ..netbox import Client, NetBoxError
from .profile import Profile
from .progress import Reporter, settings_from_env
from .workflow import upgrade_device

ENDPOINT = 'plugins/discovery/upgrade-jobs/'


@contextmanager
def tick_lock(root):
    with (root / 'tick.lock').open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


class QueueClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.local = threading.local()

    def post(self, path, payload):
        import requests
        if not hasattr(self.local, 'session'):
            self.local.session = requests.Session()
            self.local.session.headers.update(Authorization=f'Token {self.token}', Accept='application/json')
        try:
            response = self.local.session.post(f'{self.url}/api/{ENDPOINT}{path}', json=payload,
                                               timeout=self.timeout, verify=self.verify_tls,
                                               allow_redirects=False)
        except requests.RequestException as exc:
            raise NetBoxError('Upgrade queue could not reach NetBox') from exc
        if response.status_code != 200:
            # Never include token-bearing request data or arbitrary response text.
            raise NetBoxError(f'Upgrade queue returned HTTP {response.status_code}')
        try:
            result = response.json()
        except ValueError as exc:
            raise NetBoxError('Upgrade queue returned invalid JSON') from exc
        if not isinstance(result, dict):
            raise NetBoxError('Upgrade queue returned an invalid response')
        return result


class Outbox:
    """Private claim-bearing progress spool. Never replay a start authorization."""
    def __init__(self, root, client):
        self.root, self.client = Path(root), client
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def persist(self, job_id, body):
        path = self.root / f'{job_id}-{body["sequence"]:010d}.json'
        fd, temporary = tempfile.mkstemp(prefix='.event-', dir=self.root)
        try:
            with os.fdopen(fd, 'w') as handle:
                json.dump({'job_id': job_id, 'body': body}, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return path

    def send(self, job_id, body, retry=True):
        for attempt in range(3 if retry else 1):
            try:
                result = self.client.post(f'{job_id}/report/', body)
                if result.get('id') != job_id or result.get('sequence', 0) < body['sequence']:
                    raise NetBoxError('NetBox did not acknowledge the progress sequence')
                if body['stage'] == 'ready' and result.get('status') != 'running':
                    raise NetBoxError('NetBox did not authorize device changes')
                return True
            except NetBoxError:
                if retry and attempt < 2:
                    time.sleep(2 ** attempt)
        return False

    def replay(self):
        for path in sorted(self.root.glob('*.json')):
            data = json.loads(path.read_text())
            body = data['body']
            if body.get('stage') == 'ready':
                raise ValueError('Outbox contains a start authorization; inspect it manually')
            if self.send(data['job_id'], body, retry=False):
                path.unlink()
            else:
                return False
        return True

    def sink(self, job):
        def deliver(event):
            body = {key: event[key] for key in ('sequence', 'stage', 'message', 'run_id', 'summary') if key in event}
            body['message'] = body['message'][:1000]
            body['claim_token'] = job['claim_token']
            # Persist before HTTP, so a crash after device success is recoverable.
            # A ready event is a gate, never a deferred action.
            path = None if body['stage'] == 'ready' else self.persist(job['id'], body)
            delivered = self.send(job['id'], body)
            if delivered and path:
                path.unlink()
            return delivered
        return deliver


class Heartbeat:
    def __init__(self, client, job):
        self.client, self.job = client, job
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while not self.stop.wait(30):
            try:
                self.client.post(f'{self.job["id"]}/report/',
                                 {'claim_token': self.job['claim_token'], 'heartbeat': True})
            except NetBoxError:
                # Before changes, the ready gate must still succeed. Afterwards,
                # finish recovery and retain result events for the next tick.
                pass

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.stop.set()
        self.thread.join(timeout=35)


def validate_assignment(job, apply):
    if type(job.get('id')) is not int or job['id'] <= 0 or type(job.get('device_id')) is not int:
        raise ValueError('Upgrade queue returned invalid job/device IDs')
    UUID(job['claim_token'])
    ipaddress.ip_address(job['hostname'])
    if job.get('operation') not in {'audit', 'stage', 'upgrade'}:
        raise ValueError('Upgrade queue returned an unknown operation')
    if job['operation'] != 'audit' and not apply:
        raise ValueError('Upgrade queue returned a mutating job without --apply')
    if not isinstance(job.get('device'), str) or not job['device'].strip():
        raise ValueError('Upgrade queue returned an invalid device name')
    return Profile.from_mapping(job['profile'])


def execute_job(task, args, client, outbox):
    from nornir.core.task import Result
    from ..cli import PROJECT_ROOT
    from ..debuglog import redact

    job = task.host.data['upgrade_job']
    options = copy.copy(args)
    options.command = 'upgrade'
    options.apply, options.stage_only = job['operation'] != 'audit', job['operation'] == 'stage'
    options.lock_dir = PROJECT_ROOT / '.upgrade-locks'
    options.profile = None
    # Independent archives prevent mixed audit/apply jobs from misreporting mode.
    run = archive.Run(['upgrade', '--scheduled-job', str(job['id'])] +
                      (['--apply'] if options.apply else []) +
                      (['--report-dir', args.report_dir] if args.report_dir else []), PROJECT_ROOT)
    run.args = options
    code = 1
    with Heartbeat(client, job):
        run.prepare()
        run.document.update(feature='upgrade', dry_run=not options.apply,
                            schedule={'job_id': job['id'], 'batch_id': job.get('batch_id'),
                                      'poller': args.poller})
        reporter = Reporter(run, settings_from_env(), event_sink=outbox.sink(job))
        try:
            profile = validate_assignment(job, args.apply)
            reporter.emit(task.host, 'queued', 'Scheduled job claimed from NetBox')
            result = upgrade_device(task, profile, options, reporter)
            code = 1 if result.failed or reporter.delivery_failed else 0
            return Result(host=task.host, result=result.result, failed=bool(code), changed=result.changed)
        except Exception as exc:
            reporter.emit(task.host, 'failed', redact(str(exc)) if isinstance(exc, ValueError) else type(exc).__name__)
            return Result(host=task.host, result='Scheduled job could not run', failed=True)
        finally:
            run.finish(code)
            print(f'{task.host.name}: report written to {run.path}', flush=True)


def run(args, style):
    from ..cli import PROJECT_ROOT, _login
    from ..credentials import fetch_json_secret
    from ..inventory import init_from_records, missing_credentials
    from ..netbox import settings_from
    from ..poller import poller_tag
    from ..standards import load as load_standards

    if not 1 <= args.workers <= 20:
        raise ValueError('--workers must be between 1 and 20')
    if args.csv or args.ip or args.limit or args.filter or args.netbox_filter or args.profile or args.stage_only:
        raise ValueError('upgrade-poll takes its targets, operation and profile from NetBox schedules')
    args.poller = poller_tag(args.poller or os.environ.get('NETOPS_POLLER')).removeprefix('poller-')
    args.netbox_autofilter = False  # Server already checks and freezes ownership.
    args.standards = load_standards(args.standards, PROJECT_ROOT)
    settings = settings_from(args.standards, args)
    if args.netbox_secret:
        document = fetch_json_secret(args.netbox_secret, args.aws_region)
        settings['token'] = settings['token'] or document.get('token')
        settings['url'] = settings['url'] or document.get('url')
    client = QueueClient(settings['url'], settings['token'], settings['verify_tls'], timeout=15)
    identity = hashlib.sha256((client.url + '/' + args.poller).encode()).hexdigest()
    root = Path(args.queue_state_dir or PROJECT_ROOT / '.upgrade-queue') / identity
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # This includes outbox replay: no overlapping cron tick can replay the
    # previous worker's in-flight events or claim past local worker capacity.
    try:
        with tick_lock(root):
            outbox = Outbox(root / 'outbox', client)
            if not outbox.replay():
                print('Some saved NetBox progress is still undelivered; no new jobs claimed.')
                return 1
            response = client.post('check-in/', {'name': args.poller, 'limit': args.workers, 'apply': args.apply})
            jobs = response.get('jobs')
            if not isinstance(jobs, list) or len(jobs) > args.workers or any(not isinstance(j, dict) for j in jobs):
                raise NetBoxError('Upgrade queue returned an invalid batch')
            if not jobs:
                return 0
            # No credentials or SSH work on an idle tick.
            credentials, code = _login(args, style)
            if credentials is None:
                # Claimed jobs expire without ever authorizing device changes.
                return code
            from ..debuglog import protect
            protect([credentials.password, credentials.secret] + [job.get('claim_token') for job in jobs])
            records = {}
            for job in jobs:
                UUID(job['claim_token'])
                ipaddress.ip_address(job['hostname'])
                key = f'{job["device"]} [job {job["id"]}]'
                if key in records:
                    raise NetBoxError('Upgrade queue returned a duplicate job')
                records[key] = {'hostname': job['hostname'], 'platform': 'cisco_ios'}
            nr = init_from_records(records, credentials.username, credentials.password, credentials.secret,
                                   args.key_file, args.port, args.workers, args.conn_timeout)
            for host, job in zip(nr.inventory.hosts.values(), jobs):
                host.data.update(upgrade_job=job, netbox_id=job['device_id'])
            if missing_credentials(nr, args.key_file):
                raise ValueError('Device credentials are incomplete')
            try:
                result = nr.run(task=execute_job, args=args, client=client, outbox=outbox)
                return 1 if result.failed else 0
            finally:
                nr.close_connections()
    except BlockingIOError:
        return 0


def main(argv):
    from ..cli import PROJECT_ROOT, bootstrap_env, bootstrap_log, build_parser, Style
    from ..debuglog import redact
    from ..errors import summarize
    import sys
    log = None
    try:
        bootstrap_env(argv)
        log = bootstrap_log(argv)
        args = build_parser().parse_args(argv)
        if args.report:
            raise ValueError('upgrade-poll writes one archive per job; use --report-dir instead of --report')
        return run(args, Style(sys.stdout.isatty() and not os.environ.get('NO_COLOR')))
    except KeyboardInterrupt:
        print('Interrupted; check NetBox for jobs requiring recovery.', file=sys.stderr)
        return 130
    except Exception as exc:
        print('error: ' + redact(summarize(exc)), file=sys.stderr)
        if log:
            log.failure('upgrade-poll', redact(summarize(exc)), exc)
        return 1
