from django.contrib.contenttypes.models import ContentType
from netbox.api.fields import ContentTypeField
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers
from utilities.api import get_serializer_for_model

from netbox_quotes.models import Quote, QuoteLine, Vendor

__all__ = ('VendorSerializer', 'QuoteSerializer', 'QuoteLineSerializer')


class VendorSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_quotes-api:quotevendor-detail'
    )

    class Meta:
        model = Vendor
        fields = (
            'url',
            'id',
            'display',
            'name',
            'portal_url',
            'description',
            'comments',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'name')


class QuoteSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_quotes-api:quote-detail'
    )
    vendor = VendorSerializer(nested=True)
    total = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Quote
        fields = (
            'url',
            'id',
            'display',
            'vendor',
            'number',
            'status',
            'quote_date',
            'valid_until',
            'currency',
            'document',
            'total',
            'description',
            'comments',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'vendor', 'number', 'status')

    def get_total(self, obj):
        total = obj.total
        return str(total) if total is not None else None


class QuoteLineSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_quotes-api:quoteline-detail'
    )
    quote = QuoteSerializer(nested=True)
    assigned_object_type = ContentTypeField(
        queryset=ContentType.objects.all(), required=False, allow_null=True
    )
    assigned_object = serializers.SerializerMethodField(read_only=True)
    device = serializers.SerializerMethodField(read_only=True)
    effective_total = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = QuoteLine
        fields = (
            'url',
            'id',
            'display',
            'quote',
            'line_number',
            'description',
            'part_number',
            'service_sku',
            'serial',
            'quantity',
            'unit_price',
            'line_total',
            'effective_total',
            'coverage_start',
            'coverage_end',
            'assigned_object_type',
            'assigned_object_id',
            'assigned_object',
            'device',
            'match_state',
            'comments',
            'tags',
            'custom_fields',
            'created',
            'last_updated',
        )
        brief_fields = (
            'url',
            'id',
            'display',
            'quote',
            'serial',
            'match_state',
            'effective_total',
        )

    def get_assigned_object(self, obj):
        if obj.assigned_object is None:
            return None
        serializer = get_serializer_for_model(obj.assigned_object)
        return serializer(obj.assigned_object, nested=True, context=self.context).data

    def get_device(self, obj):
        device = obj.device
        if device is None:
            return None
        serializer = get_serializer_for_model(device)
        return serializer(device, nested=True, context=self.context).data

    def get_effective_total(self, obj):
        total = obj.effective_total
        return str(total) if total is not None else None
