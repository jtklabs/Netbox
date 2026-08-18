#!/usr/bin/env python3
"""Check Cisco IOS devices against the configuration standards held in NetBox.

The standards live in NetBox (the Compliance plugin: ConfigStandard), not in a
file here. That is the point of the exercise — "how many devices still have
`ip http server` on?" has to be answerable from a report rather than by running
this and reading the output. This tool connects, compares, writes the verdict
back as a per-device compliance record, and optionally fixes what it found.

  ./ios_standards.py --poller boston                     # audit; writes results to NetBox
  ./ios_standards.py --device lab-sw-01                  # one device
  ./ios_standards.py --site boston --update              # plan the additions
  ./ios_standards.py --site boston --update --commit     # send them
  ./ios_standards.py --device lab-sw-01 --enforce --commit
  ./ios_standards.py --poller boston --only "No HTTP server"
  ./ios_standards.py --poller boston --no-report         # do not write to NetBox

Three modes, and a plain run changes nothing on a device:

  audit    (default) read, compare, report. Writes the RESULT to NetBox — that
           is what makes the fleet view exist — and nothing to the device.
  update   add what the standard says should be there. Never removes anything,
           which means an `absent` standard (`no ip http server`) is reported
           and left alone: its fix is a removal.
  enforce  update, plus removing configuration the standard says must not be
           there. Only for standards with "Allow enforce" ticked in NetBox, and
           only with --commit.

--commit gates writes to DEVICES. Results always go to NetBox unless
--no-report, because a compliance record is the deliverable, not a side effect.

Guards that are not optional, whatever a standard says:

  * the account this session authenticates as is never removed
  * the last privilege-15 local account is never removed
  * if an addition could not be built (no secret supplied), no removals are sent
  * additions are sent before removals, and the accounts are re-read from the
    device in between — a removal only proceeds once the replacement is there
  * the governed configuration is captured, redacted, before anything is
    written, and stored on the compliance record as a rollback reference
  * every command is shown before it is sent, redacted

Exit codes: 0 compliant or committed, 1 a device failed, 2 drift found but not
committed — so a cron compliance check can tell "all good" from "needs work".
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone

import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from iosconfig import (  # noqa: E402
    MODE_AUDIT,
    MODE_ENFORCE,
    MODE_UPDATE,
    RESULT_COMPLIANT,
    evaluate,
    parse_config,
    plan_remediation,
)
from iosenv import load_settings  # noqa: E402
from netboxio import NetBox, NetBoxError, resolve_device_owners  # noqa: E402

RUNNING_CONFIG = 'show running-config'
_print_lock = None


def log(name, message):
    global _print_lock
    if _print_lock is None:
        import threading

        _print_lock = threading.Lock()
    with _print_lock:
        print('[%s] [%s] %s' % (time.strftime('%H:%M:%S'), name, message), flush=True)


# --------------------------------------------------------------------------- #
@dataclass
class Target:
    """One device to visit."""

    name: str
    address: str
    device_id: int = None
    site: str = ''


@dataclass
class DeviceOutcome:
    """What happened on one device, ready to print and to post."""

    target: Target
    error: str = ''
    # Every standard in scope, kept separately from the results: a device that
    # could not be reached has no results, and it still has to appear as Check
    # failed against each standard rather than vanishing from the report.
    standards: list = field(default_factory=list)
    results: list = field(default_factory=list)   # (standard, evaluation, plan, changed)

    @property
    def failed(self):
        return bool(self.error)

    @property
    def drifted(self):
        return any(e.result != RESULT_COMPLIANT for _s, e, _p, _c in self.results)


# --------------------------------------------------------------------------- #
# Device selection
# --------------------------------------------------------------------------- #
def select_targets(netbox, args):
    """Work out which devices to visit, from whichever selector was given.

    `--host` is the escape hatch for a device NetBox does not know about yet;
    everything else goes through NetBox so the tool and the report agree on what
    the fleet is.
    """
    if args.host:
        # --device-name is not just a label: naming the NetBox device lets the
        # standards be scoped to it properly, exactly as they would be for a
        # device selected the normal way. Without it there is no record to
        # scope against and every active standard is checked.
        device_id = None
        if args.device_name:
            match = netbox.devices(name=args.device_name)
            if not match:
                sys.exit('No device called %r in NetBox — drop --device-name to '
                         'check the host anyway, unscoped.' % args.device_name)
            device_id = match[0]['id']
        return [
            Target(name=args.device_name or host, address=host, device_id=device_id)
            for host in args.host
        ]

    filters = {}
    if args.device:
        filters['name'] = args.device
    if args.site:
        filters['site'] = args.site
    if args.platform:
        filters['platform'] = args.platform
    if args.role:
        filters['role'] = args.role

    if args.poller:
        owners, our_tag = resolve_device_owners(netbox, args.poller)
        wanted = {device_id for device_id, owner in owners.items() if owner == our_tag}
        if not wanted:
            sys.exit('No devices are tagged for poller %s, and no site or region '
                     'above them is either. Tag a site or a region with %s.'
                     % (args.poller, our_tag))
        filters['id'] = sorted(wanted)

    if not filters:
        sys.exit('Say which devices to check: --device, --site, --poller, --platform, '
                 '--role or --host. There is deliberately no "everything" default.')

    targets = []
    for device in netbox.devices(**filters):
        address = _management_address(device)
        if not address:
            log(device.get('name') or device['id'],
                'skipped: no primary IP in NetBox, so there is nothing to connect to')
            continue
        targets.append(Target(
            name=device.get('name') or str(device['id']),
            address=address,
            device_id=device['id'],
            site=(device.get('site') or {}).get('name', ''),
        ))
    return targets


def _management_address(device):
    for key in ('primary_ip4', 'primary_ip6', 'primary_ip'):
        entry = device.get(key)
        if entry and entry.get('address'):
            return entry['address'].split('/')[0]
    return ''


# --------------------------------------------------------------------------- #
# The device session
# --------------------------------------------------------------------------- #
class Session:
    """A netmiko connection, wrapped so the rest of this file never imports it.

    netmiko is the one heavy dependency here and it earns its place: classic IOS
    has no REST API, so this is SSH, and enable mode, terminal paging and config
    mode are exactly the fiddly parts that are not worth hand-rolling against a
    device that will happily half-apply a change. See the README.
    """

    def __init__(self, settings, target):
        from netmiko import ConnectHandler

        self._connection = ConnectHandler(
            device_type=settings.device_type,
            host=target.address,
            port=settings.port,
            username=settings.username,
            password=settings.password,
            secret=settings.enable_secret,
            conn_timeout=settings.timeout,
            fast_cli=False,
        )
        if settings.enable_secret:
            self._connection.enable()
        self.username = settings.username

    def running_config(self):
        return self._connection.send_command(RUNNING_CONFIG, read_timeout=120)

    def send(self, commands):
        return self._connection.send_config_set(commands, read_timeout=120)

    def save(self):
        return self._connection.save_config()

    def close(self):
        try:
            self._connection.disconnect()
        except Exception:  # noqa: BLE001 - a failed disconnect must not mask a result
            pass


# --------------------------------------------------------------------------- #
# Per-device work
# --------------------------------------------------------------------------- #
def check_device(settings, netbox, target, args):
    """Visit one device: read, evaluate, plan, optionally write."""
    outcome = DeviceOutcome(target=target)

    try:
        standards = _standards_for(netbox, target, args)
    except NetBoxError as exc:
        outcome.error = 'could not load standards: %s' % exc
        return outcome
    if not standards:
        log(target.name, 'no standards in force for this device — nothing to check')
        return outcome
    outcome.standards = standards

    try:
        session = Session(settings, target)
    except Exception as exc:  # noqa: BLE001 - netmiko raises a wide family
        outcome.error = '%s: %s' % (type(exc).__name__, str(exc).strip().split('\n')[0])
        log(target.name, 'connection failed — %s' % outcome.error)
        return outcome

    try:
        config = session.running_config()
        lines = parse_config(config)
        log(target.name, 'read running-config (%d lines)' % len(lines))

        for standard in standards:
            evaluation = evaluate(standard, lines)
            plan = plan_remediation(
                standard, evaluation, args.mode,
                secrets=settings.secrets, session_user=session.username,
            )
            changed = False
            _report_standard(target, standard, evaluation, plan)

            if plan.will_change and args.commit:
                changed = _apply(session, target, standard, evaluation, plan)
                if changed:
                    # Re-read so the recorded verdict is what the device says
                    # after the change, not what we hope it says.
                    lines = parse_config(session.running_config())
                    evaluation = evaluate(standard, lines)
                    log(target.name, '%s -> %s after remediation'
                        % (standard['name'], evaluation.result))
            outcome.results.append((standard, evaluation, plan, changed))

        if args.commit and any(changed for *_rest, changed in outcome.results):
            session.save()
            log(target.name, 'configuration saved')
    except Exception as exc:  # noqa: BLE001
        outcome.error = '%s: %s' % (type(exc).__name__, str(exc).strip().split('\n')[0])
        log(target.name, 'failed — %s' % outcome.error)
    finally:
        session.close()

    return outcome


def _standards_for(netbox, target, args):
    if target.device_id is None:
        # A --host target NetBox does not know: fall back to every active
        # standard, and say so, because scoping cannot be applied to a device
        # that has no record to scope against.
        standards = netbox.all('/api/plugins/compliance/config-standards/', active='true')
        log(target.name, 'no NetBox device record — checking against all %d active '
                         'standards, unscoped. Name it with --device-name to scope '
                         'them and to record the result.' % len(standards))
    else:
        standards = netbox.standards_for_device(target.device_id)
    if args.only:
        wanted = {name.lower() for name in args.only}
        standards = [s for s in standards if s['name'].lower() in wanted]
    return standards


def _report_standard(target, standard, evaluation, plan):
    """Print the verdict and the plan. Everything here is already redacted."""
    marker = 'OK  ' if evaluation.compliant else 'DRIFT'
    log(target.name, '%s %s' % (marker, standard['name']))

    for key in evaluation.missing:
        log(target.name, '        missing: %s' % key)
    for entry in evaluation.extra:
        where = ' (in %s)' % entry.line.context if entry.line.context else ''
        log(target.name, '        unexpected: %s%s' % (entry.redacted, where))
    for entry in evaluation.violations:
        where = ' (in %s)' % entry.line.context if entry.line.context else ''
        log(target.name, '        violation: %s%s [line %d]'
            % (entry.redacted, where, entry.line.number))
    for item in plan.blocked:
        log(target.name, '        NOT DOING %s — %s' % (item.what, item.why))
    for line in plan.display_lines():
        log(target.name, '        would send: %s' % line)


def _apply(session, target, standard, evaluation, plan):
    """Send the plan, additions first, and only then the removals.

    The re-read between the two halves is the guard that matters most. A plan
    that adds the account the standard wants and removes the one it does not is
    safe only if the first half actually landed; without checking, a failed add
    followed by a successful remove is a switch nobody can log in to.
    """
    # Captured before anything is written, and only the governed sections,
    # redacted. A full running-config would put every secret on the device into
    # NetBox, which is the one thing this design will not do.
    plan.pre_change_config = evaluation.governed_capture()

    sent = []
    if plan.add:
        commands = [c for command in plan.add for c in command.as_sent()]
        shown = [c for command in plan.add for c in command.as_shown()]
        log(target.name, 'sending %d addition(s)' % len(plan.add))
        session.send(commands)
        sent.extend(shown)

    if plan.remove:
        if plan.add and not _additions_landed(session, standard, plan):
            log(target.name, 'ABORTING removals — the additions are not on the device '
                             'after sending them, so removing anything now could '
                             'leave it short')
            plan.sent_display = '\n'.join(sent)
            return bool(sent)
        commands = [c for command in plan.remove for c in command.as_sent()]
        shown = [c for command in plan.remove for c in command.as_shown()]
        log(target.name, 'sending %d removal(s)' % len(plan.remove))
        session.send(commands)
        sent.extend(shown)

    plan.sent_display = '\n'.join(sent)
    return bool(sent)


def _additions_landed(session, standard, plan):
    """Re-read the device and confirm every addition is actually there."""
    lines = parse_config(session.running_config())
    after = evaluate(standard, lines)
    present = {entry.key for entry in after.observed}
    return all(command.entry in present for command in plan.add)


# --------------------------------------------------------------------------- #
# Reporting back to NetBox
# --------------------------------------------------------------------------- #
def build_report(outcome):
    """The payload for the compliance report endpoint. Redacted, always."""
    checked_at = datetime.now(timezone.utc).isoformat()
    items = []

    if outcome.failed:
        # A device that could not be reached is Check failed against every
        # standard in scope, not absent from the report. "We could not look"
        # and "there is nothing to look at" are different answers.
        for standard in outcome.standards:
            items.append({
                'device': outcome.target.name,
                'standard': standard['name'],
                'result': 'error',
                'error_message': outcome.error[:500],
                'checked_at': checked_at,
            })
        return items

    for standard, evaluation, plan, changed in outcome.results:
        item = {
            'device': outcome.target.name,
            'standard': standard['name'],
            'result': evaluation.result,
            'observed': evaluation.observed_text,
            'findings': evaluation.findings(),
            'checked_at': checked_at,
            'source': 'ssh',
        }
        if changed:
            item['remediated'] = True
            item['pre_change_config'] = plan.pre_change_config
            item['remediation_log'] = plan.sent_display
        items.append(item)
    return items


# --------------------------------------------------------------------------- #
def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description='Check Cisco IOS devices against the standards held in NetBox.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='A plain run changes nothing on a device. --commit is what sends '
               'configuration; --update and --enforce only decide what it would send.',
    )
    selection = parser.add_argument_group('which devices')
    selection.add_argument('--device', action='append',
                           help='NetBox device name (repeatable)')
    selection.add_argument('--site', action='append', help='site slug (repeatable)')
    selection.add_argument('--platform', action='append', help='platform slug (repeatable)')
    selection.add_argument('--role', action='append', help='device role slug (repeatable)')
    selection.add_argument('--poller', help='every device this poller owns, by the '
                                            'poller-<name> tag chain')
    selection.add_argument('--host', action='append',
                           help='connect to this address directly, bypassing NetBox '
                                'selection (repeatable)')
    selection.add_argument('--device-name',
                           help='NetBox device name a single --host corresponds to, '
                                'so its result can still be recorded')

    mode = parser.add_argument_group('what to do')
    mode.add_argument('--update', action='store_true',
                      help='add what is missing (never removes anything)')
    mode.add_argument('--enforce', action='store_true',
                      help='also remove configuration the standard forbids, for '
                           'standards that allow it')
    mode.add_argument('--commit', action='store_true',
                      help='actually send the configuration. Without it nothing is '
                           'written to any device.')
    mode.add_argument('--only', action='append',
                      help='only this standard, by name (repeatable)')
    mode.add_argument('--no-report', action='store_true',
                      help='do not write results back to NetBox')

    parser.add_argument('--env-file', help='path to the .env file')
    parser.add_argument('--workers', type=int,
                        help='how many devices to work on at once')

    args = parser.parse_args(argv)
    if args.enforce:
        args.mode = MODE_ENFORCE
    elif args.update:
        args.mode = MODE_UPDATE
    else:
        args.mode = MODE_AUDIT
    if args.device_name and (not args.host or len(args.host) != 1):
        parser.error('--device-name names the NetBox device for a single --host')
    return args


def main(argv=None):
    args = parse_args(argv)
    settings = load_settings(args.env_file)
    netbox = NetBox(
        settings.netbox_url, settings.netbox_token,
        verify_ssl=settings.netbox_verify_ssl, timeout=settings.netbox_timeout,
    )

    try:
        targets = select_targets(netbox, args)
    except NetBoxError as exc:
        sys.exit(str(exc))
    if not targets:
        sys.exit('No devices matched.')

    print('%d device(s), mode=%s, %s'
          % (len(targets), args.mode,
             'COMMITTING changes' if args.commit else 'nothing will be written to devices'))

    workers = args.workers or settings.workers
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        outcomes = list(pool.map(
            lambda target: check_device(settings, netbox, target, args), targets
        ))

    if not args.no_report:
        _post_results(netbox, outcomes)

    return _summarise(outcomes, args)


def _post_results(netbox, outcomes):
    # A --host target that NetBox does not know has nothing to record a result
    # against. Posting anyway would return an error row per standard and read
    # like the devices failed, rather than like we never said which device it
    # was — so say that once, here, instead.
    anonymous = [o for o in outcomes if not o.target.device_id and not _named(o)]
    if anonymous:
        print('not recorded in NetBox: %s — a --host target needs --device-name to '
              'say which device the result belongs to'
              % ', '.join(o.target.name for o in anonymous))

    items = [
        item for outcome in outcomes
        if outcome not in anonymous
        for item in build_report(outcome)
    ]
    if not items:
        return
    try:
        response = netbox.post_results(items)
    except NetBoxError as exc:
        print('warning: results were not recorded in NetBox: %s' % exc, file=sys.stderr)
        return
    summary = response.get('summary', {})
    print('recorded in NetBox: %s'
          % ', '.join('%s %s' % (count, name) for name, count in sorted(summary.items())))


def _named(outcome):
    """Did the caller tell us which NetBox device a --host target is?"""
    return outcome.target.name != outcome.target.address


def _summarise(outcomes, args):
    failed = [o for o in outcomes if o.failed]
    drifted = [o for o in outcomes if not o.failed and o.drifted]
    # A device no standard applies to is not compliant, it is unmeasured, and
    # rolling it into the green number is how a fleet report flatters itself.
    unscoped = [o for o in outcomes if not o.failed and not o.results]
    clean = len(outcomes) - len(failed) - len(drifted) - len(unscoped)

    print('')
    print('  %-24s %d' % ('compliant', clean))
    print('  %-24s %d' % ('drift found', len(drifted)))
    print('  %-24s %d' % ('failed', len(failed)))
    if unscoped:
        print('  %-24s %d' % ('no standards in scope', len(unscoped)))
    for outcome in failed:
        print('    %s: %s' % (outcome.target.name, outcome.error))

    if failed:
        return 1
    if drifted and not args.commit:
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
