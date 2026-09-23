"""Queued configuration remediation: configure.py features, run for one NetBox device.

A remediation job names features (ntp, syslog, ...) and a mode; the desired
state is this poller's own standards.yaml, never anything sent by NetBox.
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

import re
import subprocess
import sys

FEATURES = ('ntp', 'syslog', 'banner', 'acl', 'users', 'snmp', 'snmp_packetsize')
MODES = ('add', 'replace')
TIMEOUT = 1800

# configure.py exit codes.
EXIT_OK, EXIT_FAILED, EXIT_DIFF = 0, 1, 2


def validate(profile):
    """The features and mode a remediation job asks for; nothing else is accepted."""
    if not isinstance(profile, dict) or set(profile) != {'features', 'mode'}:
        raise ValueError('Remediation job must name only features and mode')
    features, mode = profile['features'], profile['mode']
    if (not isinstance(features, list) or not features or len(set(features)) != len(features)
            or any(feature not in FEATURES for feature in features)):
        raise ValueError(f'Remediation features must be a nonempty list from: {", ".join(FEATURES)}')
    if mode not in MODES:
        raise ValueError('Remediation mode must be add or replace')
    return features, mode


def command(feature, device_id, mode, apply, args):
    argv = [sys.executable, str(project_root() / 'configure.py'), feature,
            '--netbox', '--netbox-filter', f'id={int(device_id)}', '--no-netbox-autofilter', f'--{mode}']
    # The dry run exits 2 when changes are pending, so "compliant" and "would change" differ.
    argv += ['--apply', '--yes'] if apply else ['--fail-on-diff']
    if getattr(args, 'env_file', None):
        argv += ['--env-file', args.env_file]
    if getattr(args, 'standards', None) is not None and getattr(args.standards, 'path', None):
        argv += ['--standards', str(args.standards.path)]
    return argv


def project_root():
    from ..cli import PROJECT_ROOT
    return PROJECT_ROOT


def invoke(feature, device_id, mode, apply, args):
    """Run one feature; returns (exit code, output). A timeout is a failure."""
    try:
        done = subprocess.run(command(feature, device_id, mode, apply, args), capture_output=True, text=True,
                              timeout=TIMEOUT, cwd=project_root(), stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return EXIT_FAILED, f'{feature} did not finish within {TIMEOUT // 60} minutes'
    return done.returncode, (done.stdout or '') + (done.stderr or '')


def one_device(output):
    """The NetBox filter must have selected exactly this device, or nothing was checked."""
    match = re.search(r'inventory: NetBox \((\d+) device\(s\)\)', output)
    return bool(match) and match[1] == '1'


def last_line(output, pattern=r'.'):
    from ..debuglog import redact
    lines = [line.strip() for line in output.splitlines() if re.search(pattern, line)]
    return redact(lines[-1])[:400] if lines else ''


def run_job(task, job, args, emit):
    """Dry run, gate, apply. Returns (failed, changed, result) for the Nornir Result."""
    features, mode = validate(job['profile'])
    device_id = job['device_id']
    emit('connecting', f'Checking {", ".join(features)} ({mode}) against this poller\'s standards')

    results, pending = {}, []
    for feature in features:
        code, output = invoke(feature, device_id, mode, False, args)
        if code in (EXIT_OK, EXIT_DIFF) and not one_device(output):
            emit('failed', f'{feature}: NetBox inventory did not select this device; '
                           'check it is active with a primary IP')
            return True, False, results
        if code == EXIT_OK:
            results[feature] = 'compliant'
        elif code == EXIT_DIFF:
            results[feature] = 'pending'
            pending.append(feature)
        else:
            emit('failed', f'{feature}: dry run failed: {last_line(output) or f"exit {code}"}')
            return True, False, results
    summary = {'progress_summary': {'mode': mode, 'features': dict(results)}}
    emit('precheck_complete', ', '.join(f'{f} {s}' for f, s in results.items()), summary)
    if not pending:
        emit('already_current', f'Already compliant: {", ".join(features)}', summary)
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
            results[feature], changed = 'changed', True
        elif code == EXIT_DIFF:
            # Drift the feature declined to fix: out of compliance, not ours to change.
            results[feature] = 'attention'
        else:
            results[feature] = 'failed'
            results[f'{feature}_error'] = last_line(output) or f'exit {code}'
    summary = {'progress_summary': {'mode': mode, 'features': dict(results), 'rollback': journals}}
    failed = [f for f in pending if results[f] == 'failed']
    if failed:
        undo = f'; undo with configure.py rollback {journals[-1]}' if journals else ''
        emit('failed', f'{", ".join(failed)} failed after changes were authorized: '
                       f'{results[failed[0] + "_error"]}{undo}', summary)
        return True, changed, results
    attention = [f for f in pending if results[f] == 'attention']
    if attention:
        emit('completed_with_warnings', f'Applied; still needs a person: {", ".join(attention)}', summary)
    else:
        emit('completed', f'Applied {", ".join(pending)} ({mode})', summary)
    return False, changed, results
