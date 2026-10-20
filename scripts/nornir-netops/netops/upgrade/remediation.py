"""Queued configuration remediation: configure.py features, run for one NetBox device.

A remediation job names features (ntp, syslog, ...) and a mode; the desired
state is a pinned snapshot of versioned NetBox standards.
Each feature runs as the ordinary command line would run it against NetBox
inventory filtered to the job's device, so source-interface tags, syslog
policy writeback, the rollback journal and the run archive all behave exactly
as they do by hand:

    configure.py <feature> --netbox --netbox-filter id=<device> --add|--replace

A dry run of every feature comes first. Nothing is applied unless one of them
has changes pending and NetBox then authorizes the start (the `ready` gate), so
a missed window or a cancelled job never touches the device. Each run is its
own process: the command line keeps process-wide state (loaded environment,
secret redaction, the active archive) that concurrent jobs must not share.
"""

import copy
import json
import os
import re
from .. import cancellation

import subprocess
import sys
import tempfile
import uuid
from pathlib import Path
from types import SimpleNamespace

FEATURES = ('ntp', 'syslog', 'banner', 'acl', 'users', 'snmp', 'snmp_packetsize', 'raw_commands')
MODES = ('add', 'replace')
CONFIG_OPERATIONS = ('remediate', 'audit_config')
READ_ONLY_OPERATIONS = ('audit', 'audit_config')
TIMEOUT = 1800

# configure.py exit codes.
EXIT_OK, EXIT_FAILED, EXIT_DIFF = 0, 1, 2


def validate(profile):
    """The features and mode a remediation job asks for; nothing else is accepted."""
    if (not isinstance(profile, dict) or not {'features', 'mode'}.issubset(profile) or
            set(profile) - {'features', 'mode', 'allow_clearpass_cluster_changes', 'raw_standard_id', 'raw_standard_ids'}):
        raise ValueError('Remediation job must name features, mode and optional ClearPass cluster-change approval')
    if type(profile.get('allow_clearpass_cluster_changes', False)) is not bool:
        raise ValueError('ClearPass cluster-change approval must be true or false')
    features, mode = profile['features'], profile['mode']
    if (not isinstance(features, list) or not features or len(set(features)) != len(features)
            or any(feature not in FEATURES for feature in features)):
        raise ValueError(f'Remediation features must be a nonempty list from: {", ".join(FEATURES)}')
    if mode not in MODES:
        raise ValueError('Remediation mode must be add or replace')
    from ..raw_commands_policy import selected_standard_ids
    selected_standard_ids(profile)
    if profile.get('allow_clearpass_cluster_changes') and 'ntp' not in features:
        raise ValueError('ClearPass cluster-change approval requires NTP')
    return features, mode


def command(feature, device_id, mode, apply, args):
    cli_feature = feature.split(':', 1)[0]
    argv = [sys.executable, str(project_root() / 'configure.py'), cli_feature.replace('_', '-'),
            '--netbox', '--netbox-filter', f'id={int(device_id)}', '--no-netbox-autofilter', f'--{mode}']
    # The dry run exits 2 when changes are pending, so "compliant" and "would change" differ.
    argv += ['--apply', '--yes'] if apply else ['--fail-on-diff']
    if feature == 'ntp':
        if getattr(args, 'mobility_conductor_snapshot', None):
            argv += ['--mobility-conductor-snapshot', str(args.mobility_conductor_snapshot)]
        if getattr(args, 'clearpass_cluster_snapshot', None):
            argv += ['--clearpass-cluster-snapshot', str(args.clearpass_cluster_snapshot)]
        if apply and getattr(args, 'allow_clearpass_cluster_changes', False):
            argv += ['--allow-clearpass-cluster-changes']
    if getattr(args, 'device_credentials', None) is not None or getattr(args, 'no_env_file', False):
        # The worker already loaded its environment. Re-reading from the child
        # working directory can select a different .env or undo resolved values.
        argv += ['--no-env-file']
    elif getattr(args, 'env_file', None):
        argv += ['--env-file', args.env_file]
    if getattr(args, 'standards', None) is not None and getattr(args.standards, 'path', None):
        argv += ['--standards', str(args.standards.path)]
    return argv


def child_environment(args):
    """Pass resolved connection settings without secrets in argv or on disk."""
    env = os.environ.copy()
    credentials = getattr(args, 'device_credentials', None)
    if credentials is not None:
        for field, variable in (('username', 'NET_USER'), ('password', 'NET_PASS'), ('secret', 'NET_ENABLE')):
            env[variable] = getattr(credentials, field) or ''
        # AWS outranks environment credentials in the CLI. Resolution already
        # happened in the worker, so do not fetch a different login in children.
        env['NET_AWS_SECRET'] = ''
    for field, variable in (('key_file', 'NET_KEY_FILE'), ('port', 'NET_PORT'),
                            ('conn_timeout', 'NET_CONN_TIMEOUT'), ('f5_port', 'NETOPS_F5_PORT'),
                            ('f5_timeout', 'NETOPS_F5_TIMEOUT'), ('f5_login_provider', 'NETOPS_F5_LOGIN_PROVIDER')):
        if hasattr(args, field):
            value = getattr(args, field)
            if field == 'key_file' and value:
                value = Path(value).expanduser().resolve()
            env[variable] = str(value) if value is not None else ''
    env['NETOPS_F5_VERIFY_TLS'] = 'false'
    settings = getattr(args, 'remediation_netbox', None)
    if settings is not None:
        env.update(NETBOX_URL=settings['url'], NETBOX_TOKEN=settings['token'],
                   NETBOX_VERIFY_TLS=str(settings['verify_tls']).lower(), NETBOX_SECRET='')
    return env


def project_root():
    from ..cli import PROJECT_ROOT
    return PROJECT_ROOT


def invoke(feature, device_id, mode, apply, args):
    """Run one feature; returns (exit code, output). A timeout is a failure."""
    if feature in getattr(args, 'raw_standard_paths', {}):
        args = copy.copy(args)
        args.standards = SimpleNamespace(path=args.raw_standard_paths[feature])
    directory = Path(getattr(args, 'report_dir', None) or os.environ.get('NETOPS_REPORT_DIR')
                     or project_root() / 'reports').expanduser().resolve()
    report = directory / f'queued-{device_id}-{feature}-{uuid.uuid4().hex}.json'
    args.compliance_details[feature] = {}
    try:
        done = cancellation.run(command(feature, device_id, mode, apply, args) + ['--report', str(report)], capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=project_root(), stdin=subprocess.DEVNULL,
                              env=child_environment(args))
    except subprocess.TimeoutExpired:
        message = f'{feature} did not finish within {TIMEOUT // 60} minutes'
        args.compliance_details[feature] = {'error': message}
        return EXIT_FAILED, message
    try:
        details = report_details(json.loads(report.read_text(encoding='utf-8')))
        if apply:
            # An attempted command is not proof of the remaining drift. A
            # successful write is followed by a fresh read-only verification.
            details.pop('commands', None)
            details.setdefault('notes', []).append('Change attempt: run a fresh audit to establish remaining drift if verification fails.')
        args.compliance_details[feature] = details
    except (OSError, ValueError, TypeError):
        args.compliance_details[feature] = {'notes': ['Detailed findings unavailable; see the worker run archive.']}
    return done.returncode, (done.stdout or '') + (done.stderr or '')


def report_details(document):
    """Only publish bounded, already-redacted fields from the structured archive."""
    from ..archive import clean
    if not isinstance(document, dict):
        return {}
    run_error = document.get('error')
    fallback = ({'error': str(clean(run_error))[:500]} if run_error and not str(run_error).startswith(
        'Run did not reach device processing;') else {})
    devices = document.get('devices', {})
    if not isinstance(devices, dict) or len(devices) != 1:
        return fallback
    row = next(iter(devices.values()))
    if not isinstance(row, dict):
        return fallback
    details = fallback
    if row.get('read_only') is True:
        details['read_only'] = True
    for key in ('add', 'remove', 'commands', 'advisories', 'notes', 'current', 'desired'):
        values = row.get(key, [])
        if isinstance(values, list):
            details[key] = [str(clean(value))[:500] for value in values[:40]]
            if len(values) > 40 or any(len(str(value)) > 500 for value in values):
                details['truncated'] = True
    if row.get('error'):
        details['error'] = str(clean(row['error']))[:500]
    # Seven features must fit inside the API's 64 KiB progress-summary limit.
    while len(json.dumps(details).encode()) > 3500:
        largest = max((key for key in details if isinstance(details[key], list) and details[key]),
                      key=lambda key: len(json.dumps(details[key])))
        details[largest].pop()
        details['truncated'] = True
    return details


def one_device(output):
    """The NetBox filter must have selected exactly this device, or nothing was checked."""
    match = re.search(r'inventory: NetBox \((\d+) device\(s\)\)', output)
    return bool(match) and match[1] == '1'


def last_line(output, pattern=r'.'):
    from ..debuglog import redact
    lines = [line.strip() for line in output.splitlines() if re.search(pattern, line)]
    return redact(lines[-1])[:400] if lines else ''


def failure_reason(feature, args, output, code):
    """Keep the diagnostic in both the job message and feature findings.

    CLI output ends with log/report paths, so its last line is usually not
    the failure. Prefer the redacted device error, then explicit CLI errors.
    """
    from ..debuglog import redact
    details = args.compliance_details.setdefault(feature, {})
    message = (details.get('error') or
               last_line(output, r'(?i)(\berror:|\bFAILED\s*--)') or
               f'Feature process exited with code {code}; see the worker run archive.')
    details['error'] = redact(message)[:500]
    return details['error']


def run_job(task, job, args, emit):
    snapshot = job.get('standards_snapshot')
    if (not isinstance(snapshot, dict) or not isinstance(snapshot.get('document'), dict)
            or not (snapshot['document'] or snapshot.get('raw_standards')) or not snapshot.get('revisions')):
        raise ValueError('Job has no versioned NetBox standards; re-create it before execution')
    validate(job['profile'])
    from ..raw_commands_policy import selected_standard_ids, validate_raw_commands
    selected = selected_standard_ids(job['profile'])
    pinned = [item for item in snapshot['revisions'] if item.get('section') == 'raw_commands']
    if selected and sorted(item['standard_id'] for item in pinned) != sorted(selected):
        raise ValueError('Selected raw standards do not match the pinned job snapshot; re-create the job')
    raw = snapshot.get('raw_standards')
    if 'raw_standard_ids' in job['profile']:
        if (not isinstance(raw, dict) or set(raw) != {str(value) for value in selected}
                or 'raw_commands' in snapshot['document']):
            raise ValueError('Selected raw standards do not match the pinned job snapshot; re-create the job')
        for settings in raw.values():
            validate_raw_commands(settings)
    elif raw is not None or (selected and 'raw_commands' not in snapshot['document']):
        raise ValueError('Selected raw standard does not match the pinned job snapshot; re-create the job')
    options = copy.copy(args) if args is not None else SimpleNamespace()
    options.compliance_details = {}
    check_names = [check for feature in job['profile']['features'] for check in
                   ([f'raw_commands:{pk}' for pk in sorted(selected)]
                    if feature == 'raw_commands' and raw is not None else [feature])]
    read_only = snapshot.get('read_only_features', [])
    if (not isinstance(read_only, list) or any(not isinstance(item, str) for item in read_only)
            or len(set(read_only)) != len(read_only) or not set(read_only).issubset(check_names)):
        raise ValueError('Invalid read-only standards in the pinned job snapshot; re-create the job')
    options.read_only_features = list(read_only)
    # Both the dry run and apply read the same private snapshot, never a local fallback.
    with tempfile.TemporaryDirectory(prefix='netops-standards-') as directory:
        path = Path(directory) / 'standards.json'
        path.write_text(json.dumps(snapshot['document']), encoding='utf-8')
        options.standards = SimpleNamespace(path=path)
        options.raw_standard_paths = {}
        if raw is not None:
            for standard_id in sorted(selected):
                raw_path = Path(directory) / f'raw-{standard_id}.json'
                raw_path.write_text(json.dumps({**snapshot['document'],
                                               'raw_commands': raw[str(standard_id)]}), encoding='utf-8')
                options.raw_standard_paths[f'raw_commands:{standard_id}'] = raw_path
        options.allow_clearpass_cluster_changes = (job.get('operation') == 'remediate' and
                                                    'ntp' not in options.read_only_features and
                                                    job['profile'].get('allow_clearpass_cluster_changes') is True)
        options.clearpass_cluster_snapshot = None
        cluster = snapshot.get('clearpass_cluster')
        options.allow_clearpass_cluster_changes = options.allow_clearpass_cluster_changes and cluster is not None
        if cluster is not None:
            from ..clearpass_cluster import validate as validate_cluster
            cluster = validate_cluster(cluster)
            cluster_path = Path(directory) / 'clearpass-cluster.json'
            cluster_path.write_text(json.dumps(cluster), encoding='utf-8')
            options.clearpass_cluster_snapshot = cluster_path
        revisions = [{'standard_id': item['standard_id'], 'revision': item['revision']}
                     for item in snapshot['revisions']]
        conductor = snapshot.get('mobility_conductor')
        options.mobility_conductor_snapshot = None
        if conductor is not None:
            from ..mobility_conductor import validate as validate_conductor
            conductor = validate_conductor(conductor)
            conductor_path = Path(directory) / 'mobility-conductor.json'
            conductor_path.write_text(json.dumps(conductor), encoding='utf-8')
            options.mobility_conductor_snapshot = conductor_path

        def report(stage, message, payload=None):
            payload = dict(payload or {})
            summary = {**payload.get('progress_summary', {}), 'standards_revisions': revisions}
            if 'read_only_features' in snapshot:
                summary['read_only_features'] = options.read_only_features
            if cluster is not None:
                summary['clearpass_cluster'] = cluster
            if conductor is not None:
                summary['mobility_conductor'] = conductor
            if 'compliance_results' in payload:
                summary['compliance_results'] = payload['compliance_results']
                summary['compliance_details'] = copy.deepcopy(options.compliance_details)
                for feature in options.read_only_features:
                    if feature in summary['compliance_details']:
                        detail = summary['compliance_details'][feature]
                        detail['read_only'] = True
                        detail.setdefault('notes', []).append(
                            'Audit only: the pinned standard does not permit changes in this run.')
            # Reporter sends progress_summary to NetBox and keeps the full
            # payload in the local archive.
            return emit(stage, message, {**payload, 'progress_summary': summary,
                                         'standards_revisions': revisions})

        return _run_job(task, job, options, report)


def _run_job(task, job, args, emit):
    """Dry run, gate, apply. Returns (failed, changed, result) for the Nornir Result."""
    features, mode = validate(job['profile'])
    if getattr(args, 'raw_standard_paths', None):
        features = [check for feature in features for check in
                    (list(args.raw_standard_paths) if feature == 'raw_commands' else [feature])]
    if job.get('operation') not in CONFIG_OPERATIONS:
        raise ValueError('Not a configuration audit or remediation job')
    device_id = job['device_id']
    emit('connecting', f'Checking {", ".join(features)} ({mode}) against pinned NetBox standards')

    results, pending, verdicts = {}, [], {feature: 'error' for feature in features}
    for feature in features:
        code, output = invoke(feature, device_id, mode, False, args)
        if code in (EXIT_OK, EXIT_DIFF) and not one_device(output):
            emit('failed', f'{feature}: NetBox inventory did not select this device; '
                           'check it is active with a primary IP', {'compliance_results': dict(verdicts)})
            return True, False, results
        if code == EXIT_OK:
            results[feature] = 'compliant'
            verdicts[feature] = 'compliant'
        elif code == EXIT_DIFF:
            results[feature] = 'pending'
            pending.append(feature)
            verdicts[feature] = 'non-compliant'
        else:
            reason = failure_reason(feature, args, output, code)
            emit('failed', f'{feature}: dry run failed: {reason}',
                 {'compliance_results': dict(verdicts)})
            return True, False, results
    summary = {'progress_summary': {'mode': mode, 'features': dict(results)}, 'compliance_results': dict(verdicts)}
    emit('precheck_complete', ', '.join(f'{f} {s}' for f, s in results.items()), summary)
    if job['operation'] == 'audit_config':
        emit('completed_with_warnings' if pending else 'completed',
             ('Non-compliant: ' + ', '.join(pending) if pending else 'All selected standards compliant')
             + '; read-only audit, no configuration changed', summary)
        return False, False, results
    if not pending:
        emit('already_current', f'Already compliant: {", ".join(features)}', summary)
        return False, False, results

    # Standard permissions and assertion-only raw bundles have no apply phase.
    # Preserve their failed verdict without claiming a write.
    read_only = [feature for feature in pending
                 if feature in args.read_only_features or
                 (feature.split(':', 1)[0] == 'raw_commands'
                  and args.compliance_details.get(feature, {}).get('read_only') is True)]
    for feature in read_only:
        results[feature] = 'attention'
    pending = [feature for feature in pending if feature not in read_only]
    summary['progress_summary']['features'] = dict(results)
    if not pending:
        emit('completed_with_warnings', 'Read-only checks failed: ' + ', '.join(read_only)
             + '; no configuration changed', summary)
        return False, False, results

    if not emit('ready', f'Applying {", ".join(pending)} ({mode})', summary):
        raise ValueError('NetBox did not authorize the start; no configuration was changed')

    changed, journals = False, []
    for feature in pending:
        code, output = invoke(feature, device_id, mode, True, args)
        journal = last_line(output, r'rollback recorded in ')
        if journal:
            journals.append(journal.split('rollback recorded in ', 1)[1])
        if code == EXIT_OK:
            changed = True
            verify_code, verify_output = invoke(feature, device_id, mode, False, args)
            if verify_code == EXIT_OK and one_device(verify_output):
                results[feature], verdicts[feature] = 'changed', 'compliant'
            elif verify_code == EXIT_DIFF and one_device(verify_output):
                results[feature], verdicts[feature] = 'attention', 'non-compliant'
            else:
                results[feature], verdicts[feature] = 'failed', 'error'
                reason = failure_reason(feature, args, verify_output, verify_code)
                results[f'{feature}_error'] = f'Post-change verification failed: {reason}'
        elif code == EXIT_DIFF:
            # Drift the feature declined to fix: out of compliance, not ours to change.
            results[feature] = 'attention'
            verdicts[feature] = 'non-compliant'
        else:
            results[feature] = 'failed'
            verdicts[feature] = 'error'
            results[f'{feature}_error'] = failure_reason(feature, args, output, code)
    # A later raw bundle can undo an earlier bundle (even one initially compliant).
    # Re-audit all selected raw standards after the whole apply pass.
    if getattr(args, 'raw_standard_paths', None):
        for feature in args.raw_standard_paths:
            if results[feature] == 'failed':
                continue
            code, output = invoke(feature, device_id, mode, False, args)
            if code == EXIT_OK and one_device(output):
                results[feature] = 'changed' if feature in pending and mode == 'replace' else 'compliant'
                verdicts[feature] = 'compliant'
            elif code == EXIT_DIFF and one_device(output):
                results[feature], verdicts[feature] = 'attention', 'non-compliant'
            else:
                results[feature], verdicts[feature] = 'failed', 'error'
                results[f'{feature}_error'] = failure_reason(feature, args, output, code)
    summary = {'progress_summary': {'mode': mode, 'features': dict(results), 'rollback': journals},
               'compliance_results': verdicts}
    failed = [f for f in features if results[f] == 'failed']
    if failed:
        undo = f'; undo with configure.py rollback {journals[-1]}' if journals else ''
        emit('failed', f'{", ".join(failed)} failed after changes were authorized: '
                       f'{results[failed[0] + "_error"]}{undo}', summary)
        return True, changed, results
    attention = [f for f in features if results[f] == 'attention']
    if attention:
        emit('completed_with_warnings', f'Applied; still needs a person: {", ".join(attention)}', summary)
    else:
        emit('completed', f'Applied {", ".join(pending)} ({mode})', summary)
    return False, changed, results
