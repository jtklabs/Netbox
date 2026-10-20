"""Operator acknowledgments for the two post-upgrade F5 validation gates."""

from .. import cancellation


def forced(plan, step, emit, *, timed_out=False):
    control = cancellation.validation_control()
    if control is None:
        return False
    stage = 'validating' if step == 8 else 'waf_requests'
    while True:
        cancellation.checkpoint()
        if str(step) in plan.get('validation_overrides', {}):
            return True
        record = control.validation_overrides.get(str(step))
        if record:
            plan.setdefault('validation_overrides', {})[str(step)] = dict(record)
            emit(stage, f"Step {step} manually passed by {record['username']}: {record['reason']}",
                 {'upgrade_plan': plan})
            return True
        if not timed_out:
            return False
        emit(stage, f"Step {step} validation timed out; awaiting Force pass or cancellation",
             {'progress_summary': {'awaiting_manual_validation': True}})
        cancellation.sleep(5)


def findings_after_override(findings, plan):
    if '8' not in plan.get('validation_overrides', {}):
        return findings
    # Retain the original failing evidence in reports without representing it
    # as an automatic clean pass.
    return [dict(finding, severity='warning', original_severity='error', manually_overridden=True)
            if finding['severity'] == 'error' else finding for finding in findings]
