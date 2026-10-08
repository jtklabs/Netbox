"""Shared model defaults edited from either profile or model views."""
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from dcim.models import DeviceType

from .profile_models import DeviceTypeProfile


class AssignmentConflict(ValidationError):
    def __init__(self, message, confirmation):
        super().__init__(message)
        self.confirmation = confirmation


def assignment_field(kind):
    return 'remediation_profile' if kind == 'remediate' else 'upgrade_profile'


def selected_models(profile):
    if not profile.pk:
        return DeviceType.objects.none()
    return DeviceType.objects.filter(**{f'job_profiles__{assignment_field(profile.kind)}': profile})


def validate_model_replacement(instance, replace=False):
    if not instance.pk or replace:
        return
    original = DeviceTypeProfile.objects.filter(pk=instance.pk).first()
    if original is None:
        raise ValidationError('This assignment no longer exists. Reload before saving.')
    for field in ('upgrade_profile_id', 'remediation_profile_id'):
        previous, selected = getattr(original, field), getattr(instance, field)
        if previous and selected and previous != selected:
            raise ValidationError('This replaces an existing default. Select Replace existing assignments to confirm.')


def validate_selection(profile, models, user, replace=False, confirmation=''):
    field = assignment_field(profile.kind)
    selected = {model.pk for model in models}
    current = set(selected_models(profile).values_list('pk', flat=True))
    rows = {row.device_type_id: row for row in DeviceTypeProfile.objects.filter(
        device_type_id__in=selected | current).select_related('device_type', field)}
    changed = {pk for pk in selected | current
               if (pk in selected) != (pk in current)}
    if not changed:
        return rows
    if user is None or not user.is_authenticated:
        raise ValidationError('Model assignment changes require an authenticated user.')
    if DeviceType.objects.restrict(user, 'view').filter(pk__in=changed).count() != len(changed):
        raise ValidationError('You cannot change assignments for one or more of these models.')
    conflicts = []
    for pk in sorted(changed):
        row = rows.get(pk)
        if row is None:
            if not user.has_perm('netbox_discovery.add_devicetypeprofile'):
                raise ValidationError('You need permission to add model profile assignments.')
            continue
        other_field = 'upgrade_profile_id' if field == 'remediation_profile' else 'remediation_profile_id'
        action = 'delete' if pk not in selected and not getattr(row, other_field) else 'change'
        if not DeviceTypeProfile.objects.restrict(user, action).filter(pk=row.pk).exists():
            raise ValidationError(f'You cannot {action} the assignment for {row.device_type}.')
        previous = getattr(row, field)
        if pk in selected and previous is not None:
            if not type(profile).objects.restrict(user, 'view').filter(pk=previous.pk).exists():
                raise ValidationError('An existing profile assignment is not accessible to you.')
            conflicts.append((pk, previous.pk, str(row.device_type), previous.name))
    if conflicts:
        expected = {'kind': profile.kind,
                    'conflicts': [[pk, old] for pk, old, _, _ in conflicts]}
        try:
            confirmed = signing.loads(confirmation, salt='profile-assignments', max_age=900)
        except signing.BadSignature:
            confirmed = None
        if not replace or confirmed != expected:
            detail = '; '.join(f'{model}: {old}' for _, _, model, old in conflicts)
            raise AssignmentConflict(
                f'Already assigned: {detail}. Select Replace existing assignments and save again to confirm.',
                signing.dumps(expected, salt='profile-assignments'))
    return rows


@transaction.atomic
def assign_models(profile, models, user, replace=False, confirmation=''):
    models = list(models)
    current = set(selected_models(profile).values_list('pk', flat=True))
    selected = {model.pk for model in models}
    # Serialize competing profile edits for the same hardware models.
    list(DeviceType.objects.select_for_update().filter(pk__in=current | selected).order_by('pk'))
    rows = validate_selection(profile, models, user, replace, confirmation)
    field = assignment_field(profile.kind)
    current = {pk for pk, row in rows.items() if getattr(row, f'{field}_id') == profile.pk}
    for pk in sorted(current | selected):
        row = rows.get(pk)
        if pk in selected:
            if row and getattr(row, f'{field}_id') == profile.pk:
                continue
            if row:
                row.snapshot()
            else:
                row = DeviceTypeProfile(device_type_id=pk)
            setattr(row, field, profile)
            row.full_clean()
            row.save()
            action = 'change' if pk in rows else 'add'
            if not DeviceTypeProfile.objects.restrict(user, action).filter(pk=row.pk).exists():
                raise ValidationError('The resulting model assignment is outside your permissions.')
        else:
            row.snapshot()
            setattr(row, field, None)
            if row.upgrade_profile_id or row.remediation_profile_id:
                row.full_clean()
                row.save()
                if not DeviceTypeProfile.objects.restrict(user, 'change').filter(pk=row.pk).exists():
                    raise ValidationError('The resulting model assignment is outside your permissions.')
            else:
                row.delete()
