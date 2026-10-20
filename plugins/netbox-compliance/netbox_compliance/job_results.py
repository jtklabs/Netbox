"""Translate verified feature verdicts into revision-aware compliance records."""
from .definitions import FEATURE_SECTIONS
from .models import ConfigCompliance, ConfigStandardRevision


def record_job_results(job, summary, checked_at):
    verdicts = summary.get('compliance_results', {})
    if not isinstance(verdicts, dict):
        return
    for pinned in job.standards_snapshot['revisions']:
        check_features = [f for f in job.profile['features'] if FEATURE_SECTIONS[f] == pinned['section']]
        if pinned['section'] == 'raw_commands' and 'raw_standard_ids' in job.profile:
            check_features = [f'raw_commands:{pinned["standard_id"]}']
        results = [verdicts[f] for f in check_features
                   if verdicts.get(f) in ('compliant', 'non-compliant', 'error')]
        # A shared section (SNMP + packet size) needs every selected check.
        required = len(check_features)
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
        details = summary.get('compliance_details', {})
        checks = {feature: details[feature] for feature in check_features
                  if isinstance(details, dict)
                  and isinstance(details.get(feature), dict)}
        checks = {feature: {
            **{key: [item for item in check.get(key, []) if isinstance(item, str)]
               for key in ('add', 'remove', 'current', 'desired', 'commands', 'notes', 'advisories')
               if isinstance(check.get(key), list)},
            'error': check.get('error') if isinstance(check.get('error'), str) else '',
            'truncated': check.get('truncated') is True,
        } for feature, check in checks.items()}
        record.findings['checks'] = checks
        record.findings['missing'] = [f'{feature}: {item}' for feature, check in checks.items()
                                      for item in check.get('add', []) if verdicts.get(feature) == 'non-compliant']
        record.findings['extra'] = [{'line': f'{feature}: {item}'} for feature, check in checks.items()
                                    for item in check.get('remove', []) if verdicts.get(feature) == 'non-compliant']
        record.observed = '\n\n'.join(f'{feature}:\n' + '\n'.join(map(str, check.get('current', [])))
                                      for feature, check in checks.items() if check.get('current'))
        errors = [str(check['error']) for check in checks.values() if check.get('error')]
        record.error_message = ('\n'.join(errors) or 'Feature check failed; see the linked job.') if result == 'error' else ''
        record.save()
