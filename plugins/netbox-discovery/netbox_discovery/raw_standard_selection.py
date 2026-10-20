"""Named raw standard selection shared by profile and scheduling forms."""
from django import forms
from django.apps import apps
from django.db.models import F
from netbox.context import current_request


def raw_standard_field():
    return forms.TypedChoiceField(label='Raw command standard', required=False, coerce=int, empty_value=None,
        help_text='Run only this named raw standard. Blank uses the single applicable raw standard; '
                  'multiple matches block scheduling. Device scope and permissions still apply.')


def configure_raw_standard(form, user=None):
    field = form.fields['raw_standard']
    field.choices = [('', 'Automatic (only when one applies)')]
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
