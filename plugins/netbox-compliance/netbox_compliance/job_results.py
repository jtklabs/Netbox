"""Translate verified feature verdicts into revision-aware compliance records."""
from .definitions import FEATURE_SECTIONS
from .models import ConfigCompliance, ConfigStandardRevision


def record_job_results(job, summary, checked_at):
    verdicts = summary.get('compliance_results', {})
    if not isinstance(verdicts, dict):
        return
    grouped = {}
    for feature in job.profile['features']:
        result = verdicts.get(feature)
        if result not in ('compliant', 'non-compliant', 'error'):
            continue
        grouped.setdefault(FEATURE_SECTIONS[feature], []).append(result)
    for pinned in job.standards_snapshot['revisions']:
        results = grouped.get(pinned['section'])
        # A shared section (SNMP + packet size) needs every selected check.
        required = sum(FEATURE_SECTIONS[f] == pinned['section'] for f in job.profile['features'])
        if not results or len(results) != required:
            continue
        revision = ConfigStandardRevision.objects.get(standard_id=pinned['standard_id'], number=pinned['revision'])
        result = 'error' if 'error' in results else 'non-compliant' if 'non-compliant' in results else 'compliant'
        record, _ = ConfigCompliance.objects.get_or_create(device_id=job.device_id, standard_id=pinned['standard_id'])
        if record.last_checked and record.last_checked > checked_at:
            continue
        record.standard_revision = revision
        record.result = result
        record.last_checked = checked_at
        record.source = 'ssh'
        record.findings = {'job_id': job.pk, 'run_id': job.run_id}
        record.observed = ''
        record.error_message = 'Feature check failed; see the linked job.' if result == 'error' else ''
        record.save()
