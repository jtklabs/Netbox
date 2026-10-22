"""One NetBox job per member, ordered and fenced as a two-member rolling upgrade.

Development driver: applying requires an explicit poller opt-in until the lab
matrix is complete. Durable receipts bind the second member to a validated first
member. A failed or interrupted mutation never becomes an automatic retry.
"""
import hashlib
import html
import json
import os
import tempfile
import time
from contextlib import ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

from nornir.core.task import Result

from .. import cancellation
from .images import local_image
from .panos_api import Client, ApiError, configuration_digest
from .panos_profile import IMAGE, require, release
from . import panos_validation as validation
from .workflow import device_lock


def private_json(path, document):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix='.panos-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as handle:
            json.dump(document, handle, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def wait_for(action, timeout, interval, emit, message, *, observations=2):
    deadline, consecutive, last = time.monotonic() + timeout, 0, 'no observations'
    while time.monotonic() < deadline:
        cancellation.checkpoint()
        try:
            value = action()
            consecutive += 1
            if consecutive >= observations:
                return value
            last = 'first healthy observation; waiting for confirmation'
        except (ApiError, ValueError) as exc:
            consecutive, last = 0, str(exc)
        emit('validating', f'{message}: {last}')
        cancellation.sleep(min(interval, max(0, deadline - time.monotonic())))
    raise ValueError(f'{message} timed out: {last}')


def wait_install(client, job, options, emit, *, device_serial=None):
    def check():
        row = client.job(job)
        if row['status'] == 'FIN' and row['result'] != 'OK':
            # Terminal failures must not be polled until timeout or rebooted.
            raise RuntimeError(f'PAN-OS install job {job} failed')
        if device_serial is None and row.get('type') != 'SWInstall':
            raise RuntimeError(f'PAN-OS job {job} is not a software install')
        require(row['status'] == 'FIN' and row['result'] == 'OK', f'install job {job} remains {row["status"]}')
        if device_serial:
            rows = row.get('devices', [])
            require(len(rows) == 1 and rows[0].get('serial-no') == device_serial and rows[0].get('result') == 'OK',
                    'Panorama distribution lacks successful per-device confirmation')
        return row
    return wait_for(check, options.install_timeout, options.poll_interval, emit, 'Waiting for install', observations=1)


def backup(client, profile, root, job_id):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key = os.environ.get('NETOPS_PANOS_BACKUP_KEY', '')
    try:
        key = bytes.fromhex(key)
    except ValueError:
        key = b''
    require(len(key) == 32, 'NETOPS_PANOS_BACKUP_KEY must supply a 256-bit encryption key at runtime')
    raw = client.configuration()
    nonce = os.urandom(12)
    name = f'{client.member["serial"]}-{job_id}-pre-{profile.target_version}.xml.aesgcm'
    path = root / name
    blob = nonce + AESGCM(key).encrypt(nonce, raw, name.encode())
    require(AESGCM(key).decrypt(blob[:12], blob[12:], name.encode()) == raw, 'configuration backup encryption verification failed')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(blob)
        handle.flush()
        os.fsync(handle.fileno())
    return {'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'configuration_sha256': configuration_digest(raw), 'encrypted': True}


def report(before, after, failures):
    # Never include raw configuration, license activation codes or API requests.
    record = {'pre': before, 'post': after, 'failures': failures,
              'informational_only': ['session counts', 'throughput', 'ARP entries', 'dynamic route counts']}
    text = json.dumps(record, indent=2, sort_keys=True)
    return {'report': {'format': 'text/html', 'html': '<!doctype html><meta charset="utf-8"><title>PAN-OS upgrade validation</title>'
                      '<h1>PAN-OS upgrade validation</h1><pre>' + html.escape(text) + '</pre>',
                      'markdown': '```json\n' + text + '\n```'}}


def upgrade_device(task, profile, options, reporter):
    changed, before, after, journal = False, {}, {}, {}
    monitor = None
    host, settings = task.host, profile.panos
    stage_only = getattr(options, 'stage_only', False)
    plan = {'blockers': [], 'target_version': profile.target_version, 'approved_profile': asdict(profile)}
    journal_path = None

    def emit(stage, message, payload=None, attachment=None):
        if stage in {'precheck_complete', 'validating', 'blocked', 'recovery_required', 'completed', 'dry_run_complete', 'staged'}:
            payload = dict(payload or {}, progress_summary={
                'target_version': profile.target_version, 'panos': {
                    'ha_mode': settings['ha_mode'], 'pre': validation.summary(before), 'post': validation.summary(after),
                    'blockers': [str(item)[:500] for item in plan['blockers'][:30]],
                    'traffic_continuity': plan.get('traffic_continuity')}})
        return reporter.emit(host, stage, message, payload, changed=changed, attachment=attachment)

    def persist(phase, **extra):
        journal.update(phase=phase, updated=time.time(), **extra)
        private_json(journal_path, journal)
        emit(phase, f'PAN-OS phase: {phase}', {'panos_journal': journal})

    try:
        members = settings['members']
        index = next((n for n, member in enumerate(members) if member['address'] == host.hostname), None)
        require(index is not None, 'job address is not in the approved PAN-OS member list')
        member = members[index]
        pair = settings['ha_mode'] != 'standalone'
        fingerprint = hashlib.sha256(json.dumps(asdict(profile), sort_keys=True).encode()).hexdigest()
        state_root = Path(options.lock_dir) / 'panos'
        pair_id = hashlib.sha256('|'.join(sorted(m['serial'] for m in members)).encode()).hexdigest()
        receipt_path = state_root / f'{pair_id}-receipt.json'
        job_id = str(host.data.get('upgrade_job', {}).get('id') or int(time.time() * 1000000))
        journal_path = state_root / f'{member["serial"]}-{job_id}.json'
        with ExitStack() as stack:
            # Same locks for either peer/order and direct/Panorama addressing.
            for address in sorted(m['address'] for m in members):
                stack.enter_context(device_lock(options.lock_dir, f'panos:{address}'))
            client = stack.enter_context(Client(host, settings, member))
            peer = stack.enter_context(Client(host, settings, members[1 - index])) if pair and not stage_only else None
            before = validation.collect(client, profile, emit)
            failures = [] if stage_only else validation.readiness(before, profile) + validation.required_invariants(before, profile)
            require(before['facts'].get('system', {}).get('serial') == member['serial'], 'API returned a different firewall serial')
            require(before['facts']['system']['model'] in profile.models, 'device model does not match approved image')
            require('jobs' not in before['errors'] and before.get('jobs') and all(j.get('status') == 'FIN' for j in before['jobs'].values()),
                    'pending or unreadable device jobs')
            peer_before, receipt = None, None
            central = None
            if settings.get('panorama'):
                central = stack.enter_context(Client(host, settings, member, central=True))
                if not stage_only:
                    require(release(central.system()['sw-version']) >= release(profile.target_version),
                            'upgrade Panorama to the target release or newer first')
                from panos_upgrade_assurance.firewall_proxy import FirewallProxy
                central_jobs = FirewallProxy(firewall=central).get_jobs()
                require(central_jobs and all(job.get('status') == 'FIN' for job in central_jobs.values()), 'Panorama has pending or unreadable jobs/pushes')
            if pair and not stage_only:
                peer_before = validation.collect(peer, profile, emit)
                require(peer_before['facts'].get('system', {}).get('serial') == members[1 - index]['serial'],
                        'peer API returned a different serial')
                failures += ['peer: ' + f for f in validation.readiness(peer_before, profile)]
                if index == 1:
                    require(receipt_path.is_file(), 'second member requires successful first-member evidence on this poller')
                    receipt = json.loads(receipt_path.read_text())
                    require(receipt.get('fingerprint') == fingerprint and receipt.get('phase') == 'first_validated'
                            and 0 <= time.time() - receipt.get('updated', 0) < 86400,
                            'first-member evidence is missing, stale or belongs to another profile')
                    require(peer_before['facts']['system']['sw-version'] == profile.target_version,
                            'first member is no longer running the target')
                    require(before['configuration_sha256'] == receipt['second_before']['configuration_sha256']
                            and peer_before['configuration_sha256'] == receipt['first_after']['configuration_sha256'],
                            'configuration changed since first-member validation')
                    failures += ['peer: ' + f for f in validation.compare(receipt['first_after'], peer_before, profile)]
                else:
                    if receipt_path.exists():
                        previous = json.loads(receipt_path.read_text())
                        require(previous.get('phase') == 'complete', 'unfinished pair receipt requires recovery review')
                    require(before['facts']['system']['sw-version'] == peer_before['facts']['system']['sw-version'],
                            'first-member upgrade requires matching starting releases')
                validation.pair_health(before, peer_before, settings, mixed=index == 1)
                local_state = before['ha']['group']['local-info']['state']
                if index == 0:
                    require(local_state == ('passive' if settings['ha_mode'] == 'active-passive' else 'active-secondary'),
                            'first approved member must be passive or active-secondary')
            elif not pair and not stage_only:
                require(before.get('ha', {}).get('enabled') == 'no', 'standalone profile cannot upgrade an HA member')
            info = before['facts']['system']
            if info.get('model') == 'PA-VM' and release(profile.target_version) >= (12, 2, 2, 0):
                require(int(info.get('vm-mem', 0)) >= 14 * 1024 * 1024, 'PAN-OS 12.2.2+ needs at least 14 GiB RAM')
            records = [*settings.get('base_images', []), {k: getattr(profile, k) for k in ('image', 'sha256', 'image_source')}]
            paths = [local_image(SimpleNamespace(**record, md5=''), options, emit) for record in records]
            free = before.get('disk_free_mb', {}).get('/opt/panrepo')
            require(free is not None and free * 1024 * 1024 >= sum(p.stat().st_size for p in paths) + profile.minimum_free_bytes,
                    'insufficient or unknown PAN-OS image repository free space')
            plan['blockers'] = failures
            plan['starting_versions'] = [info['sw-version']]
            emit('precheck_complete', 'PAN-OS baseline collected', {'pre': before, 'upgrade_plan': plan}, report(before, None, failures))
            if failures:
                emit('blocked', '; '.join(failures), {'upgrade_plan': plan})
                return Result(host=host, result=plan, failed=True)
            if not options.apply:
                emit('dry_run_complete', 'PAN-OS checks passed; no device changes made')
                return Result(host=host, result=plan)
            require(os.environ.get('NETOPS_PANOS_APPLY_ENABLED') == 'true',
                    'PAN-OS apply is disabled until the lab acceptance matrix is complete')
            require(stage_only or info['sw-version'] != profile.target_version, 'device already runs the target; audit it instead of reinstalling')
            require(emit('ready', 'PAN-OS checks passed; requesting authorization to stage and upgrade', {'upgrade_plan': plan}),
                    'NetBox did not acknowledge apply authorization')
            journal = {'job_id': job_id, 'fingerprint': fingerprint, 'serial': member['serial'], 'pre': before,
                       'target': profile.target_version, 'peer_before': peer_before}
            persist('authorized')
            state_root.mkdir(parents=True, exist_ok=True, mode=0o700)
            if not stage_only:
                backup_record = backup(client, profile, state_root, job_id)
                require(backup_record['configuration_sha256'] == before['configuration_sha256'], 'configuration changed before backup')
                persist('backup_complete', backup=backup_record)
            for record, path in zip(records, paths):
                version = IMAGE.fullmatch(record['image'])[2]
                # A fresh verified upload establishes provenance for this job.
                persist('upload_pending', image=record['image'])
                changed = True
                if central:
                    with device_lock(options.lock_dir, f'panorama-image:{settings["panorama"]}:{record["sha256"]}'):
                        marker = state_root / f'panorama-{hashlib.sha256(settings["panorama"].encode()).hexdigest()}-{record["sha256"]}.json'
                        available = central.installed_images(batch=True)
                        if not marker.exists() or available.get(version, {}).get('downloaded') != 'yes':
                            central.upload(path)
                            require(central.installed_images(batch=True).get(version, {}).get('downloaded') == 'yes',
                                    'image import is not confirmed in Panorama software inventory')
                            private_json(marker, {'sha256': record['sha256'], 'image': record['image']})
                        job = central.distribute(record['image'], member['serial'])
                        persist('distributing', distribution_job=job, image=record['image'])
                        wait_install(central, job, options, emit, device_serial=member['serial'])
                else:
                    client.upload(path)
                inventory = client.installed_images()
                require(inventory.get(version, {}).get('downloaded') == 'yes', 'uploaded image is not ready on device')
                persist('image_staged', image=record['image'])
            if stage_only:
                emit('staged', 'PAN-OS images staged; no install or reboot')
                return Result(host=host, result=plan, changed=changed)
            if pair:
                monitor = stack.enter_context(validation.ProbeMonitor(settings))
                if index == 0:
                    receipt = {'fingerprint': fingerprint, 'phase': 'in_progress', 'updated': time.time(), 'second_before': peer_before}
                else:
                    receipt['phase'] = 'second_in_progress'
                private_json(receipt_path, receipt)
                # Re-read immediately before surrendering traffic ownership.
                fresh, peer_fresh = validation.collect(client, profile), validation.collect(peer, profile)
                validation.pair_health(fresh, peer_fresh, settings, mixed=index == 1)
                require(fresh['configuration_sha256'] == before['configuration_sha256'] and
                        peer_fresh['configuration_sha256'] == peer_before['configuration_sha256'], 'configuration changed before suspension')
                persist('suspend_pending')
                changed = True
                client.ha_state('suspend')

                def handoff():
                    local, other = validation.collect(client, profile), validation.collect(peer, profile)
                    validation.pair_health(local, other, settings, suspended=True, mixed=index == 1)
                    errors = validation.readiness(other, profile) + validation.required_invariants(other, profile)
                    require(not errors, 'surviving peer is unhealthy: ' + '; '.join(errors))
                    return other

                wait_for(handoff, profile.validation_timeout or options.validation_timeout,
                         options.poll_interval, emit, 'Verifying surviving peer and application traffic')
                persist('handoff_verified')
            persist('install_pending')
            changed = True
            job = client.install(profile.target_version)
            persist('installing', install_job=job)
            wait_install(client, job, options, emit)
            persist('reboot_pending')
            try:
                client.reboot()
            except ApiError:
                # Reconcile target/uptime/readiness; never send a second reboot.
                emit('reconnecting', 'Reboot acknowledgment was lost; checking device state')

            def booted():
                observed = client.system()
                require(observed['serial'] == member['serial'] and observed['sw-version'] == profile.target_version,
                        'waiting for correct device and target release')
                jobs = client.result('show jobs all').findall('job')
                startup = [job for job in jobs if job.findtext('type') == 'AutoCom'
                           and job.findtext('id') not in before.get('jobs', {})]
                require(startup and all(job.findtext('status') == 'FIN' and job.findtext('result') == 'OK' for job in startup),
                        'waiting for a successful new startup auto-commit')
                if not profile.image.startswith('Panorama_'):
                    require((client.result('show chassis-ready').text or '').strip() == 'yes', 'waiting for dataplane readiness')
                return observed

            wait_for(booted, options.reload_timeout, options.poll_interval, emit, 'Waiting for target release')
            if pair:
                persist('resume_pending')
                client.ha_state('functional')

            def converged():
                nonlocal after
                current = validation.collect(client, profile)
                after = current
                errors = validation.compare(before, current, profile, role_changed=pair and index == 1)
                if peer:
                    other = validation.collect(peer, profile)
                    validation.pair_health(current, other, settings, mixed=index == 0)
                    errors += ['peer: ' + f for f in validation.readiness(other, profile)]
                    require(other['configuration_sha256'] == peer_before['configuration_sha256'], 'peer configuration drifted')
                require(not errors, '; '.join(errors))
                return current

            after = wait_for(converged, profile.validation_timeout or options.validation_timeout,
                             options.poll_interval, emit, 'Waiting for stable post-upgrade validation')
            if monitor:
                continuity = monitor.summary()
                plan['traffic_continuity'] = continuity
                require(continuity['healthy'], 'synthetic traffic exceeded the allowed failover interruption')
            persist('validated', post=after)
            if pair:
                receipt.update(phase='first_validated' if index == 0 else 'complete', updated=time.time())
                if index == 0:
                    receipt['first_after'] = after
                private_json(receipt_path, receipt)
            emit('completed', 'PAN-OS target and stable health checks verified', {'post': after, 'upgrade_plan': plan}, report(before, after, []))
            return Result(host=host, result=plan, changed=changed)
    except Exception as exc:
        from ..debuglog import redact
        message = redact(str(exc))
        plan['blockers'] = list(dict.fromkeys([*plan['blockers'], message]))
        if journal_path is not None and journal:
            journal.update(phase='recovery_required' if changed else 'blocked', error=message, updated=time.time())
            private_json(journal_path, journal)
        emit('recovery_required' if changed else 'blocked', message, {'upgrade_plan': plan}, report(before, after, plan['blockers']))
        return Result(host=host, result=plan, changed=changed, failed=True)
