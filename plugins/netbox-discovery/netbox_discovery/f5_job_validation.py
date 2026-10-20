"""Audited operator overrides, delivered only to the worker holding this job."""
from django.db import transaction
from django.utils import timezone

from .models import UpgradeJob
from . import upgrade_queue as queue

PERMISSIONS = tuple('netbox_discovery.' + action + '_upgradejob' for action in ('view', 'change', 'apply'))


@transaction.atomic
def force_pass(user, pk, step, reason):
    if not user.has_perms(PERMISSIONS):
        raise queue.QueueError('Manual validation requires view, change and apply device-job permissions.')
    job = (UpgradeJob.objects.restrict(user, 'view').restrict(user, 'change').restrict(user, 'apply')
           .select_for_update().get(pk=pk))
    if type(step) is not int or step not in (8, 9) or job.manual_validation_step != step:
        raise queue.QueueError('Only the current running F5 validation step can be manually passed. Refresh the job.')
    if not reason.strip():
        raise queue.QueueError('Describe why this validation step can be passed manually.')
    record = {'step': step, 'user_id': user.pk, 'username': user.get_username(),
              'timestamp': timezone.now().isoformat(), 'reason': reason.strip()[:1000]}
    job.validation_overrides = {**job.validation_overrides, str(step): record}
    job.events = (job.events + [{
        'stage': 'manual_validation_requested', 'timestamp': record['timestamp'],
        'message': f"Step {step} force pass requested by {record['username']}: {record['reason']}",
    }])[-500:]
    job.save()
    return job
