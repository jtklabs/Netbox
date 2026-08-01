"""Populate ModelLifecycle records from the Cisco EoX API.

Manually-maintained records are never overwritten: a record whose source is
`manual` is skipped entirely unless the caller forces it. That keeps hand-entered
data for non-Cisco kit (and hand-corrected Cisco data) safe from the sync.
"""

import logging
from os import environ

from django.contrib.contenttypes.models import ContentType
from django.conf import settings
from django.utils import timezone

from netbox_refresh.choices import LifecycleSourceChoices
from netbox_refresh.cisco import BATCH_SIZE, CiscoEoxClient, CiscoEoxError
from netbox_refresh.models import ModelLifecycle

logger = logging.getLogger('netbox.plugins.netbox_refresh')

DATE_FIELDS = (
    'announcement_date',
    'end_of_sale',
    'end_of_sw_maintenance',
    'end_of_security_support',
    'end_of_routine_failure_analysis',
    'end_of_service_attach',
    'end_of_service_contract_renewal',
    'end_of_support',
)


def get_settings():
    return settings.PLUGINS_CONFIG.get('netbox_refresh', {})


def get_credentials():
    config = get_settings()
    return (
        config.get('cisco_client_id') or environ.get('CISCO_CLIENT_ID', ''),
        config.get('cisco_client_secret') or environ.get('CISCO_CLIENT_SECRET', ''),
    )


def candidate_types():
    """Device/module types with a Cisco part number, which is the PID we query."""
    from dcim.models import DeviceType, ModuleType

    manufacturers = get_settings().get('cisco_manufacturers') or ['Cisco']
    found = []
    for model in (DeviceType, ModuleType):
        found.extend(
            model.objects.filter(manufacturer__name__in=manufacturers)
            .exclude(part_number='')
            .select_related('manufacturer')
        )
    return found


def _resolve_replacement(obj, pid):
    """Find the DeviceType/ModuleType matching a Cisco migration PID, if we stock it."""
    if not pid:
        return None
    model = type(obj)
    return model.objects.filter(part_number__iexact=pid).first()


def sync(dry_run=False, force=False, limit=None, logger_fn=None):
    """Sync EoL data for every Cisco device/module type. Returns a summary dict."""
    emit = logger_fn or (lambda msg: logger.info('netbox_refresh: %s', msg))

    client_id, client_secret = get_credentials()
    client = CiscoEoxClient(client_id, client_secret)

    targets = candidate_types()
    if limit:
        targets = targets[:limit]
    emit('%d Cisco device/module types with a part number' % len(targets))

    # Several types can share a PID; look each PID up once.
    by_pid = {}
    for obj in targets:
        by_pid.setdefault(obj.part_number.strip().upper(), []).append(obj)
    pids = sorted(by_pid)

    summary = {'types': len(targets), 'pids': len(pids), 'updated': 0, 'created': 0,
               'no_data': 0, 'skipped_manual': 0, 'replacements_linked': 0, 'errors': 0}

    for start in range(0, len(pids), BATCH_SIZE):
        batch = pids[start:start + BATCH_SIZE]
        emit('looking up PIDs %d-%d of %d' % (start + 1, start + len(batch), len(pids)))
        try:
            results = client.fetch(batch)
        except CiscoEoxError as exc:
            summary['errors'] += len(batch)
            emit('batch failed (%s) — leaving these records untouched' % exc)
            continue

        for pid in batch:
            data = results.get(pid)
            if data is None:
                summary['no_data'] += 1
                continue
            for obj in by_pid[pid]:
                outcome = _apply(obj, data, dry_run=dry_run, force=force)
                summary[outcome] = summary.get(outcome, 0) + 1
                if outcome != 'skipped_manual' and data.get('replacement_pid'):
                    if _resolve_replacement(obj, data['replacement_pid']):
                        summary['replacements_linked'] += 1

    return summary


def _apply(obj, data, dry_run=False, force=False):
    content_type = ContentType.objects.get_for_model(obj)
    record = ModelLifecycle.objects.filter(
        assigned_object_type=content_type, assigned_object_id=obj.pk
    ).first()

    if record and record.source == LifecycleSourceChoices.SOURCE_MANUAL and not force:
        return 'skipped_manual'

    created = record is None
    if created:
        record = ModelLifecycle(assigned_object_type=content_type, assigned_object_id=obj.pk)

    for field in DATE_FIELDS:
        setattr(record, field, data.get(field))
    record.bulletin_number = data.get('bulletin_number') or ''
    record.bulletin_url = data.get('bulletin_url') or ''
    record.source = LifecycleSourceChoices.SOURCE_CISCO
    record.last_synced = timezone.now()

    replacement = _resolve_replacement(obj, data.get('replacement_pid'))
    if replacement is not None:
        if obj._meta.model_name == 'devicetype':
            record.replacement_device_type = replacement
        else:
            record.replacement_module_type = replacement
    # Keep the vendor's prose either way — it explains cases where no single
    # successor model exists, and names a PID we do not stock yet.
    notes = data.get('replacement_notes') or ''
    if data.get('replacement_pid') and replacement is None:
        notes = ('Cisco migration PID %s (not in NetBox). %s'
                 % (data['replacement_pid'], notes)).strip()
    record.replacement_notes = notes[:2000]

    if not dry_run:
        record.full_clean(exclude=['assigned_object_type', 'assigned_object_id'])
        record.save()
    return 'created' if created else 'updated'
