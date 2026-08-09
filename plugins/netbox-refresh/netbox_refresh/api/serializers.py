from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from netbox_refresh.choices import SoftwareSourceChoices
from netbox_refresh.models import (
    DeviceSoftware,
    ModelLifecycle,
    SoftwareStandard,
    SoftwareVersion,
)

__all__ = (
    'ModelLifecycleSerializer',
    'SoftwareVersionSerializer',
    'SoftwareStandardSerializer',
    'DeviceSoftwareSerializer',
    'SoftwareReportSerializer',
)


class ModelLifecycleSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_refresh-api:modellifecycle-detail'
    )
    assigned_object_type = ContentTypeField(queryset=ContentType.objects.all())
    assigned_object = serializers.SerializerMethodField(read_only=True)
    replacement = serializers.SerializerMethodField(read_only=True)
    installed_count = serializers.IntegerField(read_only=True)
    extended_cost = serializers.SerializerMethodField(read_only=True)
    status = serializers.CharField(read_only=True)

    class Meta:
        model = ModelLifecycle
        fields = (
            'url', 'id', 'display',
            'assigned_object_type', 'assigned_object_id', 'assigned_object',
            'announcement_date', 'end_of_sale', 'end_of_sw_maintenance',
            'end_of_security_support', 'end_of_routine_failure_analysis',
            'end_of_service_attach', 'end_of_service_contract_renewal', 'end_of_support',
            'status', 'bulletin_number', 'bulletin_url',
            'replacement_device_type', 'replacement_module_type', 'replacement',
            'replacement_notes', 'replacement_cost', 'currency', 'cost_updated',
            'installed_count', 'extended_cost',
            'source', 'last_synced',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'assigned_object', 'end_of_support', 'status')

    def _nested(self, obj):
        if obj is None:
            return None
        serializer = get_serializer_for_model(obj)
        return serializer(obj, nested=True, context=self.context).data

    def get_assigned_object(self, obj):
        return self._nested(obj.assigned_object)

    def get_replacement(self, obj):
        return self._nested(obj.replacement)

    def get_extended_cost(self, obj):
        value = obj.extended_cost
        return str(value) if value is not None else None


class SoftwareVersionSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_refresh-api:softwareversion-detail'
    )
    download_url = serializers.CharField(read_only=True)
    installed_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = SoftwareVersion
        fields = (
            'url', 'id', 'display', 'platform', 'version', 'release_date',
            'image_filename', 'image_url', 'image_file', 'image_size',
            'checksum_type', 'checksum', 'download_url', 'installed_count',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'version', 'platform')


class SoftwareStandardSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_refresh-api:softwarestandard-detail'
    )
    assigned_object_type = ContentTypeField(queryset=ContentType.objects.all())
    assigned_object = serializers.SerializerMethodField(read_only=True)
    is_active = serializers.BooleanField(read_only=True)

    class Meta:
        model = SoftwareStandard
        fields = (
            'url', 'id', 'display',
            'assigned_object_type', 'assigned_object_id', 'assigned_object',
            'approved_versions', 'preferred_version', 'valid_from', 'valid_to',
            'is_active', 'description', 'comments', 'tags', 'custom_fields',
            'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'assigned_object', 'valid_from', 'valid_to')

    def get_assigned_object(self, obj):
        if obj.assigned_object is None:
            return None
        serializer = get_serializer_for_model(obj.assigned_object)
        return serializer(obj.assigned_object, nested=True, context=self.context).data


class DeviceSoftwareSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_refresh-api:devicesoftware-detail'
    )
    # Derived, read-only: compliance is computed from the standard in force, so
    # writing it would be meaningless. Exposed here so a caller can read the
    # verdict without re-implementing the rules.
    compliance_status = serializers.CharField(read_only=True)
    is_stale = serializers.BooleanField(read_only=True)
    as_of = serializers.DateTimeField(read_only=True)

    class Meta:
        model = DeviceSoftware
        fields = (
            'url', 'id', 'display', 'device', 'software_version', 'raw_version',
            'raw_report', 'source', 'collected_at', 'last_checked', 'as_of',
            'compliance_status', 'is_stale',
            'exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
            'exempt_review_by',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'device', 'software_version', 'compliance_status')


class SoftwareReportSerializer(serializers.Serializer):
    """One running-version reading pushed in by a collector.

    Deliberately not a ModelSerializer: collectors identify devices and
    platforms by name, send the version as a raw string, and should not have to
    know about SoftwareVersion primary keys or create catalogue rows themselves.
    """

    device = serializers.IntegerField(
        required=False, help_text='NetBox device ID'
    )
    device_name = serializers.CharField(
        required=False, allow_blank=True, help_text='Used when no device ID is given'
    )
    version = serializers.CharField(
        required=False, allow_blank=True, trim_whitespace=True,
        help_text='Raw version string. Omit or send "" when nothing was collected — '
                  'never a placeholder, which would be stored as a real version.',
    )
    platform = serializers.CharField(
        required=False, allow_blank=True,
        help_text='Platform name or slug; defaults to the device platform',
    )
    source = serializers.ChoiceField(
        choices=SoftwareSourceChoices, default=SoftwareSourceChoices.SOURCE_API
    )
    collected_at = serializers.DateTimeField(
        required=False, help_text='When the reading was taken at the device'
    )
    raw = serializers.CharField(
        required=False, allow_blank=True,
        help_text='Verbatim collector output the version came from, e.g. sysDescr',
    )

    def validate(self, data):
        if not data.get('device') and not data.get('device_name'):
            raise serializers.ValidationError(
                'Provide either device (ID) or device_name.'
            )
        return data
