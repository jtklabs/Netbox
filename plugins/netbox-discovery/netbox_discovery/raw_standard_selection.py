"""Named raw standard selection shared by profile and scheduling forms."""
from django import forms
from django.apps import apps
from django.db.models import F
from netbox.context import current_request


def raw_standard_field():
    return forms.TypedMultipleChoiceField(label='Raw command standards', required=False, coerce=int,
        help_text='Select up to eight raw standards. Each runs separately, in standard ID order. Blank uses the single applicable raw standard; '
                  'multiple matches block scheduling. Device scope and permissions still apply.')


def configure_raw_standard(form, user=None):
    field = form.fields['raw_standard']
    field.choices = []
    if not apps.is_installed('netbox_compliance'):
        field.disabled = True
        field.widget = forms.HiddenInput()
        return
    from netbox_compliance.models import ConfigStandard
    from netbox_compliance.scoping import active_standards
    request = current_request.get()
    user = getattr(request, 'user', None) or user
    if user is None:
        return
    standards = active_standards(queryset=ConfigStandard.objects.restrict(user, 'view')).filter(
        check_type='netops', revisions__number=F('revision'),
        revisions__definition__settings__has_key='raw_commands').distinct().order_by('name')
    field.choices = [*field.choices, *((standard.pk, standard.name) for standard in standards)]


def selected_standard_ids(profile):
    """Validate explicit selection; keep legacy single-ID plans compatible."""
    if 'raw_standard_id' in profile and 'raw_standard_ids' in profile:
        raise ValueError('Use raw_standard_ids or raw_standard_id, not both.')
    if 'raw_standard_id' in profile:
        value = profile['raw_standard_id']
        if type(value) is not int or value < 1:
            raise ValueError('raw_standard_id must be a positive integer.')
        selected = [value]
    elif 'raw_standard_ids' in profile:
        selected = profile['raw_standard_ids']
        if (not isinstance(selected, list) or not 1 <= len(selected) <= 8
                or any(type(value) is not int or value < 1 for value in selected)
                or len(set(selected)) != len(selected)):
            raise ValueError('raw_standard_ids must contain 1–8 distinct positive integers.')
    else:
        return []
    if 'raw_commands' not in profile.get('features', []):
        raise ValueError('A raw standard selection requires the raw_commands feature.')
    return list(selected)


def store_selection(plan, values):
    if len(values) == 1:
        plan['raw_standard_id'] = values[0]
    elif values:
        plan['raw_standard_ids'] = sorted(values)
