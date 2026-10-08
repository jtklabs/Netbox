"""Reusable plans and the defaults selected for each hardware model."""
from django.core.exceptions import ValidationError
from django.db import models
from django.db import transaction
from django.urls import reverse
from netbox.models import PrimaryModel


class JobProfile(PrimaryModel):
    name = models.CharField(max_length=100, unique=True)
    kind = models.CharField(max_length=16, choices=(('upgrade', 'Upgrade'), ('remediate', 'Remediation')))
    plan = models.JSONField()

    class Meta:
        ordering = ('kind', 'name')

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:jobprofile', args=[self.pk])

    def resolved_plan(self):
        from copy import deepcopy
        from .profile_assignments import selected_models
        plan = deepcopy(self.plan)
        if self.kind == 'upgrade':
            plan['models'] = sorted(set(selected_models(self).values_list('model', flat=True)))
        return plan

    def clean(self):
        super().clean()
        from .upgrade_queue import validate_profile
        try:
            # A profile can be saved before any model defaults are assigned.
            plan = dict(self.plan) if isinstance(self.plan, dict) else self.plan
            if isinstance(plan, dict) and self.kind == 'upgrade':
                plan['models'] = ['assigned-models']
            validate_profile(plan, self.kind)
        except (ValueError, TypeError) as exc:
            raise ValidationError(str(exc)) from exc
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).values_list('kind', flat=True).first()
            if original != self.kind and (self.upgrade_models.exists() or self.remediation_models.exists()):
                raise ValidationError({'kind': 'Remove model assignments before changing the profile type.'})


class DeviceTypeProfile(PrimaryModel):
    device_type = models.OneToOneField('dcim.DeviceType', on_delete=models.CASCADE,
                                       related_name='job_profiles', verbose_name='Model')
    upgrade_profile = models.ForeignKey(JobProfile, on_delete=models.PROTECT, null=True, blank=True,
                                        related_name='upgrade_models', limit_choices_to={'kind': 'upgrade'})
    remediation_profile = models.ForeignKey(JobProfile, on_delete=models.PROTECT, null=True, blank=True,
                                            related_name='remediation_models', limit_choices_to={'kind': 'remediate'})

    class Meta:
        ordering = ('device_type__manufacturer__name', 'device_type__model')
        verbose_name = 'model profile assignment'

    def __str__(self):
        return str(self.device_type)

    def get_absolute_url(self):
        return reverse('plugins:netbox_discovery:devicetypeprofile', args=[self.pk])

    @transaction.atomic
    def save(self, *args, **kwargs):
        from dcim.models import DeviceType
        list(DeviceType.objects.select_for_update().filter(pk=self.device_type_id))
        return super().save(*args, **kwargs)

    @transaction.atomic
    def delete(self, *args, **kwargs):
        from dcim.models import DeviceType
        list(DeviceType.objects.select_for_update().filter(pk=self.device_type_id))
        return super().delete(*args, **kwargs)

    def clean(self):
        super().clean()
        for field, kind in (('upgrade_profile', 'upgrade'), ('remediation_profile', 'remediate')):
            profile = getattr(self, field)
            if profile and profile.kind != kind:
                raise ValidationError({field: 'Select a profile of the matching type.'})
        if not self.upgrade_profile_id and not self.remediation_profile_id:
            raise ValidationError('Select at least one profile.')
