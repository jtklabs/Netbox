"""Serial number hygiene — the first repo-managed scheduled script.

Serials arrive from many hands (CSV imports, quote-line matching, manual
entry) and the quote matcher and SNMP scanner both join on them, so stray
whitespace quietly breaks joins that look like they should match. This script
trims it, and reports — without changing — serials that collide once trimmed,
because which record is right is a human call.

It doubles as the template for future data-update scripts: copy the file,
keep the shape. The conventions that matter:

  * `commit` is handled by NetBox, not by the script. Every run from the UI
    has a "Commit changes" checkbox; unticked, NetBox rolls the whole run
    back afterwards, so the log always shows what WOULD happen. Write the
    script as if changes are real, log everything, never manage transactions
    yourself.
  * Log liberally (`self.log_info` / `log_success` / `log_warning`). The job
    log is the only artifact a 3am scheduled run leaves behind.
  * Keep runs idempotent: a script on a schedule reruns forever, so running
    it twice must be safe and the second run should log "nothing to do".

To run on a schedule: Customization -> Scripts -> (this script) -> Run, set
"Schedule at" for the first run and "Recurs every N minutes" for the cadence
(1440 = daily). The netbox-worker container executes it; see
docs/SCHEDULED-JOBS.md.
"""

from dcim.models import Device, Site
from extras.scripts import ObjectVar, Script


class SerialHygiene(Script):
    class Meta:
        name = 'Serial number hygiene'
        description = (
            'Trim stray whitespace from device serials and report duplicates. '
            'Safe to run on a schedule; changes nothing else.'
        )
        # Same inputs every run — lets the schedule rerun without prompting.
        job_timeout = 300

    site = ObjectVar(
        model=Site,
        required=False,
        description='Limit to one site (leave empty for the whole estate)',
    )

    def run(self, data, commit):
        devices = Device.objects.exclude(serial='')
        if data.get('site'):
            devices = devices.filter(site=data['site'])

        trimmed = 0
        for device in devices.filter(serial__regex=r'^\s|\s$'):
            before = device.serial
            device.serial = device.serial.strip()
            device.save()
            trimmed += 1
            self.log_success(
                f'{device.name}: serial {before!r} -> {device.serial!r}'
            )

        # Collisions are reported, never auto-resolved: two devices claiming
        # one serial means one of them is wrong, and only a human knows which.
        seen = {}
        for device in devices.values('pk', 'name', 'serial'):
            key = device['serial'].strip().lower()
            if key in seen:
                self.log_warning(
                    f"Duplicate serial {device['serial']!r}: "
                    f"{device['name']} and {seen[key]}"
                )
            else:
                seen[key] = device['name']

        if not trimmed:
            self.log_info('No serials needed trimming — nothing to do.')
        return f'{trimmed} serial(s) trimmed'
