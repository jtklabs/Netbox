from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from netbox_refresh.models import ModelLifecycle

__all__ = ('ModelLifecycleSerializer',)


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
