from rest_framework import serializers
from django.core.exceptions import ValidationError
from django.db import transaction
from dcim.models import DeviceType, Platform
from dcim.api.serializers import DeviceTypeSerializer, PlatformSerializer
from netbox.api.serializers import NetBoxModelSerializer
from netbox.api.viewsets import NetBoxModelViewSet
from ..models import DeviceTypeProfile, JobProfile, PlatformProfile
from ..profile_filtersets import DeviceTypeProfileFilterSet, JobProfileFilterSet, PlatformProfileFilterSet
from ..profile_assignments import (AssignmentConflict, assign_models, selected_models, validate_selection,
                                   validate_model_replacement)


class JobProfileSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:jobprofile-detail')
    device_types = serializers.PrimaryKeyRelatedField(queryset=DeviceType.objects.all(), many=True, required=False)
    replace_existing = serializers.BooleanField(write_only=True, required=False, default=False)
    assignment_confirmation = serializers.CharField(write_only=True, required=False, default='', allow_blank=True)

    class Meta:
        model = JobProfile
        fields = ('id', 'url', 'display', 'name', 'kind', 'plan', 'device_types',
                  'replace_existing', 'assignment_confirmation', 'description', 'comments',
                  'tags', 'custom_fields', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'name', 'kind')

    def validate(self, attrs):
        if self.nested:
            return super().validate(attrs)
        kind = attrs.get('kind', getattr(self.instance, 'kind', None))
        if kind == 'upgrade' and isinstance(attrs.get('plan'), dict):
            attrs['plan'] = {key: value for key, value in attrs['plan'].items() if key != 'models'}
        selection = {key: attrs.pop(key) for key in ('device_types', 'replace_existing', 'assignment_confirmation')
                     if key in attrs}
        attrs = super().validate(attrs)
        attrs.update(selection)
        if 'device_types' in attrs:
            candidate = JobProfile(pk=getattr(self.instance, 'pk', None), kind=kind)
            try:
                validate_selection(candidate, attrs['device_types'], self.context['request'].user,
                                   attrs.get('replace_existing', False), attrs.get('assignment_confirmation', ''))
            except AssignmentConflict as exc:
                raise serializers.ValidationError({'device_types': exc.messages,
                                                   'assignment_confirmation': exc.confirmation}) from exc
            except ValidationError as exc:
                raise serializers.ValidationError({'device_types': exc.messages}) from exc
        return attrs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if 'plan' in data:
            data['plan'] = instance.resolved_plan()
            models = selected_models(instance)
            request = self.context.get('request')
            if request is not None:
                models = models.restrict(request.user, 'view')
            data['device_types'] = list(models.values_list('pk', flat=True))
        return data

    @transaction.atomic
    def create(self, validated_data):
        return self._save_profile(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        return self._save_profile(validated_data, instance)

    def _save_profile(self, data, instance=None):
        models = data.pop('device_types', None)
        replace = data.pop('replace_existing', False)
        confirmation = data.pop('assignment_confirmation', '')
        profile = super().update(instance, data) if instance else super().create(data)
        if models is not None:
            try:
                assign_models(profile, models, self.context['request'].user, replace, confirmation)
            except ValidationError as exc:
                raise serializers.ValidationError({'device_types': exc.messages}) from exc
        return profile


class JobProfileViewSet(NetBoxModelViewSet):
    queryset = JobProfile.objects.all()
    serializer_class = JobProfileSerializer
    filterset_class = JobProfileFilterSet


class DeviceTypeProfileSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:devicetypeprofile-detail')
    device_type = DeviceTypeSerializer(nested=True)
    upgrade_profile = JobProfileSerializer(nested=True, required=False, allow_null=True)
    remediation_profile = JobProfileSerializer(nested=True, required=False, allow_null=True)
    replace_existing = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = DeviceTypeProfile
        fields = ('id', 'url', 'display', 'device_type', 'upgrade_profile', 'remediation_profile',
                  'replace_existing', 'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'device_type', 'upgrade_profile', 'remediation_profile')

    def validate(self, attrs):
        if self.nested:
            return super().validate(attrs)
        replace = attrs.pop('replace_existing', False)
        attrs = super().validate(attrs)
        candidate = DeviceTypeProfile(pk=getattr(self.instance, 'pk', None))
        for field in ('upgrade_profile', 'remediation_profile'):
            setattr(candidate, field, attrs.get(field, getattr(self.instance, field, None)))
        try:
            validate_model_replacement(candidate, replace)
        except ValidationError as exc:
            raise serializers.ValidationError({'replace_existing': exc.messages}) from exc
        attrs['replace_existing'] = replace
        return attrs

    def create(self, validated_data):
        validated_data.pop('replace_existing', None)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        replace = validated_data.pop('replace_existing', False)
        dtype = validated_data.get('device_type', instance.device_type)
        list(DeviceType.objects.select_for_update().filter(pk=dtype.pk))
        candidate = DeviceTypeProfile(pk=instance.pk)
        for field in ('upgrade_profile', 'remediation_profile'):
            setattr(candidate, field, validated_data.get(field, getattr(instance, field)))
        try:
            validate_model_replacement(candidate, replace)
        except ValidationError as exc:
            raise serializers.ValidationError({'replace_existing': exc.messages}) from exc
        return super().update(instance, validated_data)


class DeviceTypeProfileViewSet(NetBoxModelViewSet):
    queryset = DeviceTypeProfile.objects.select_related('device_type', 'upgrade_profile', 'remediation_profile')
    serializer_class = DeviceTypeProfileSerializer
    filterset_class = DeviceTypeProfileFilterSet


class PlatformProfileSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(view_name='plugins-api:netbox_discovery-api:platformprofile-detail')
    platform = PlatformSerializer(nested=True)
    remediation_profile = JobProfileSerializer(nested=True)
    replace_existing = serializers.BooleanField(write_only=True, required=False, default=False)

    class Meta:
        model = PlatformProfile
        fields = ('id', 'url', 'display', 'platform', 'remediation_profile', 'replace_existing',
                  'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated')
        brief_fields = ('id', 'url', 'display', 'platform', 'remediation_profile')

    def check_platform(self, platform):
        if PlatformProfile.objects.filter(platform=platform).exclude(pk=getattr(self.instance, 'pk', None)).exists():
            raise serializers.ValidationError({'platform': 'This platform already has a standards profile assignment.'})

    def validate(self, attrs):
        if self.nested:
            return super().validate(attrs)
        replace = attrs.pop('replace_existing', False)
        attrs = super().validate(attrs)
        self.check_platform(attrs.get('platform', getattr(self.instance, 'platform', None)))
        candidate = PlatformProfile(pk=getattr(self.instance, 'pk', None), remediation_profile=attrs.get(
            'remediation_profile', getattr(self.instance, 'remediation_profile', None)))
        try:
            validate_model_replacement(candidate, replace, fields=('remediation_profile_id',))
        except ValidationError as exc:
            raise serializers.ValidationError({'replace_existing': exc.messages}) from exc
        attrs['replace_existing'] = replace
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data.pop('replace_existing', None)
        platform = validated_data['platform']
        list(Platform.objects.select_for_update().filter(pk=platform.pk))
        self.check_platform(platform)
        return super().create(validated_data)

    @transaction.atomic
    def update(self, instance, validated_data):
        replace = validated_data.pop('replace_existing', False)
        platform = validated_data.get('platform', instance.platform)
        list(Platform.objects.select_for_update().filter(pk=platform.pk))
        self.check_platform(platform)
        candidate = PlatformProfile(pk=instance.pk, remediation_profile=validated_data.get(
            'remediation_profile', instance.remediation_profile))
        try:
            validate_model_replacement(candidate, replace, fields=('remediation_profile_id',))
        except ValidationError as exc:
            raise serializers.ValidationError({'replace_existing': exc.messages}) from exc
        return super().update(instance, validated_data)


class PlatformProfileViewSet(NetBoxModelViewSet):
    queryset = PlatformProfile.objects.select_related('platform', 'remediation_profile')
    serializer_class = PlatformProfileSerializer
    filterset_class = PlatformProfileFilterSet
