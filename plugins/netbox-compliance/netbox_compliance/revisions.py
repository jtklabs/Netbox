"""Durable definition snapshots, independent of NetBox changelog retention."""
from django.db import transaction
from contextlib import contextmanager
from contextvars import ContextVar
from django.db.models.signals import m2m_changed
from netbox.context import current_request

from .definitions import parse_definition

SCOPES = ('platforms', 'roles', 'sites', 'device_tags')
FIELDS = ('name', 'check_type', 'match_pattern', 'expected_entries', 'add_template',
          'remove_template', 'auto_remediable', 'allow_enforce', 'remediation_notes',
          'description', 'comments')
pending_revisions = ContextVar('pending_standard_revisions', default=None)


@contextmanager
def revision_batch():
    """A form/API edit includes its many-to-many scope changes in one revision."""
    if pending_revisions.get() is not None:
        yield
        return
    pending = set()
    token = pending_revisions.set(pending)
    try:
        with transaction.atomic():
            yield
            for pk in sorted(pending):
                record_revision(pk)
    finally:
        pending_revisions.reset(token)


def capture_revision(instance):
    pending = pending_revisions.get()
    if pending is not None:
        pending.add(instance.pk)
    else:
        instance.revision = record_revision(instance.pk).number


@transaction.atomic
def record_revision(pk):
    from .models import ConfigStandard, ConfigStandardRevision

    standard = ConfigStandard.objects.select_for_update().get(pk=pk)
    definition = {name: getattr(standard, name) for name in FIELDS}
    definition.update({name: sorted(getattr(standard, name).values_list('pk', flat=True)) for name in SCOPES})
    definition.update(valid_from=standard.valid_from.isoformat(),
                      valid_to=standard.valid_to.isoformat() if standard.valid_to else None,
                      settings=parse_definition(standard.definition_yaml) if standard.check_type == 'netops' else {})
    previous = standard.revisions.first()
    if previous and previous.definition == definition and previous.definition_yaml == standard.definition_yaml:
        return previous
    request = current_request.get()
    user = getattr(request, 'user', None)
    revision = ConfigStandardRevision.objects.create(
        standard=standard, number=(previous.number + 1) if previous else 1,
        definition=definition, definition_yaml=standard.definition_yaml,
        author=user.get_username() if user and user.is_authenticated else '')
    ConfigStandard.objects.filter(pk=pk).update(revision=revision.number)
    return revision


def scope_changed(sender, instance, action, reverse, pk_set, **kwargs):
    from .models import ConfigStandard

    if reverse:
        field = next(name for name in SCOPES if getattr(ConfigStandard, name).through == sender)
        if action == 'pre_clear':
            instance._standard_revision_ids = list(ConfigStandard.objects.filter(**{field: instance}).values_list('pk', flat=True))
        if action.startswith('post_'):
            for pk in pk_set or getattr(instance, '_standard_revision_ids', []):
                record_revision(pk)
    elif action.startswith('post_'):
        capture_revision(instance)


def register_signals():
    from .models import ConfigStandard
    for name in SCOPES:
        m2m_changed.connect(scope_changed, sender=getattr(ConfigStandard, name).through,
                            dispatch_uid=f'compliance-revision-{name}')
