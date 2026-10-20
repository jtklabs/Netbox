"""Boot the recorded previous partition once; no install, save, drain or failover."""
import re
import time

from nornir.core.task import Result

from .. import cancellation, f5_waf
from . import f5
from .profile import Profile, f5_version
from .workflow import device_lock

VOLUMES = '/mgmt/tm/sys/software/volume'


def validate(job):
    profile = Profile.from_mapping(job['profile'])
    plan = job.get('rollback_plan')
    if profile.family != 'f5' or type(job.get('rollback_of')) is not int or job['rollback_of'] <= 0:
        raise ValueError('Rollback requires a recorded F5 source upgrade job')
    if not isinstance(plan, dict) or set(plan) - {'volume', 'from_volume', 'previous_version', 'previous_build', 'expected_version', 'note'}:
        raise ValueError('Invalid F5 rollback plan')
    for key in ('volume', 'from_volume'):
        value = plan.get(key)
        if (key == 'volume' or value) and (not isinstance(value, str) or not re.fullmatch(r'HD\d+\.\d+', value)):
            raise ValueError('Rollback requires a recorded boot partition such as HD1.1')
    if plan.get('from_volume') == plan['volume']:
        raise ValueError('Rollback partitions must differ')
    if f5_version(plan.get('expected_version', '')) != f5_version(profile.target_version):
        raise ValueError('Rollback expected release must match the source upgrade')
    if plan.get('previous_version'):
        f5_version(plan['previous_version'])
    if plan.get('previous_build') and (not isinstance(plan['previous_build'], str) or not re.fullmatch(r'\d+(?:\.\d+)*', plan['previous_build'])):
        raise ValueError('Invalid recorded previous build')
    return dict(plan)


def volumes(client):
    rows = client.get_json(VOLUMES).get('items')
    if not isinstance(rows, list) or not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Boot partitions could not be read')
    active = [row for row in rows if str(row.get('active', '')).lower() == 'true']
    if len(active) != 1:
        raise ValueError('Could not identify exactly one active boot partition')
    return rows, active[0]


def target_partition(rows, plan):
    matches = [row for row in rows if row.get('name') == plan['volume']]
    if len(matches) != 1 or str(matches[0].get('status', '')).lower() != 'complete':
        raise ValueError('The recorded previous partition is missing or not complete')
    target = matches[0]
    if not target.get('version'):
        raise ValueError('The previous partition has no readable software version')
    if plan.get('previous_version') and f5_version(target['version']) != f5_version(plan['previous_version']):
        raise ValueError('The previous partition software changed after the upgrade; rollback refused')
    if plan.get('previous_build') and target.get('build') != plan['previous_build']:
        raise ValueError('The previous partition build changed after the upgrade; rollback refused')
    return target


def running_release_matches(client, target):
    system = f5.first_entry(client.get_json('/mgmt/tm/sys/version'))
    version = f5.describe(system.get('Version'))
    build = f5.describe(system.get('Build'))
    return (bool(version) and f5_version(version) == f5_version(target['version'])
            and (not target.get('build') or build == target['build']))


def run(task, plan, options, reporter):
    host = task.host
    if not options.apply:
        raise ValueError('Rollback requires --apply')
    if host.platform not in (None, '', 'f5_tmsh') or not host.username or not host.password:
        raise ValueError('Rollback requires an F5 device and REST credentials')
    settings = dict(options.f5)
    def emit(stage, message):
        return reporter.emit(host, stage, message, {'progress_summary': {'rollback_plan': plan}},
                             changed=stage in ('rebooting', 'reconnecting', 'completed'))
    with device_lock(options.lock_dir, f"{host.hostname}:{settings.get('port', 443)}"):
        with f5_waf.Client(host, **settings) as client:
            rows, active = volumes(client)
            target = target_partition(rows, plan)
            if active.get('name') == plan['volume']:
                if not running_release_matches(client, target):
                    raise ValueError('Active partition and running software disagree; inspect the device')
                emit('already_current', f"Already booted from {plan['volume']}; no reboot sent")
                return Result(host=host, result='Already on the previous boot partition')
            if plan.get('from_volume') and active.get('name') != plan['from_volume']:
                raise ValueError('The active boot partition changed after the upgrade; rollback refused')
            if f5_version(active.get('version', '')) != f5_version(plan['expected_version']):
                raise ValueError('The running release no longer matches the source upgrade; rollback refused')
            if not emit('ready', f"Confirmed rollback to {plan['volume']}: immediate reboot, no traffic drain"):
                raise ValueError('NetBox did not authorize rollback; no reboot sent')
            emit('rebooting', f"Rebooting immediately to {plan['volume']} without draining traffic")
            cancellation.checkpoint()
            f5.reboot_to(client, plan['volume'])
        # A reset/timeout during POST is ambiguous: observe, never resend reboot.
        deadline = time.monotonic() + options.reload_timeout
        while time.monotonic() < deadline:
            cancellation.checkpoint()
            emit('reconnecting', f"Waiting for boot partition {plan['volume']}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                connect = dict(settings, timeout=min(settings.get('timeout', 30), remaining))
                with f5_waf.Client(host, **connect) as client:
                    rows, active = volumes(client)
                    target = target_partition(rows, plan)
                    if active.get('name') == plan['volume'] and running_release_matches(client, target):
                        emit('completed', f"Rollback boot confirmed on {plan['volume']}; review device health and traffic")
                        return Result(host=host, result='Previous boot partition confirmed', changed=True)
            except Exception:
                # Services and authentication can take time to return. Preserve
                # the single reboot attempt and keep cancellation interruptible.
                pass
            cancellation.sleep(min(max(options.poll_interval, 0.1), max(0, deadline-time.monotonic())))
        raise TimeoutError('Rollback boot was not confirmed before the reconnect deadline; inspect the device')
