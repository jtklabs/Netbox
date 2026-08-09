from dcim.api.serializers import DeviceSerializer, DeviceRoleSerializer, SiteSerializer
from dcim.models import DeviceRole, Site
from ipam.api.serializers import PrefixSerializer, VRFSerializer
from tenancy.api.serializers import TenantSerializer
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from netbox_discovery.choices import OnboardingStatusChoices
from netbox_discovery.models import (
    DiscoveryPoller,
    HardwareReplacement,
    OnboardingRequest,
)

__all__ = (
    'DiscoveryPollerSerializer',
    'OnboardingRequestSerializer',
    'PollerCheckInSerializer',
    'ScanResultSerializer',
    'ApplyResultSerializer',
    'ApproveSerializer',
    'RejectSerializer',
    'HardwareReplacementSerializer',
)


class DiscoveryPollerSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_discovery-api:discoverypoller-detail'
    )
    tenant = TenantSerializer(nested=True, required=False, allow_null=True)
    is_stale = serializers.BooleanField(read_only=True)

    class Meta:
        model = DiscoveryPoller
        fields = (
            'url', 'id', 'display', 'name', 'tenant', 'last_seen_at', 'version',
            'last_scan_summary', 'is_stale',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'name', 'is_stale')


class OnboardingRequestSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_discovery-api:onboardingrequest-detail'
    )
    site = SiteSerializer(nested=True, read_only=True)
    override_site = SiteSerializer(nested=True, required=False, allow_null=True)
    prefix = PrefixSerializer(nested=True, read_only=True)
    poller = DiscoveryPollerSerializer(nested=True, read_only=True)
    role = DeviceRoleSerializer(nested=True, required=False, allow_null=True)
    tenant = TenantSerializer(nested=True, required=False, allow_null=True)
    vrf = VRFSerializer(nested=True, required=False, allow_null=True)
    device = DeviceSerializer(nested=True, read_only=True)

    class Meta:
        model = OnboardingRequest
        fields = (
            'url', 'id', 'display', 'address', 'status', 'site', 'override_site',
            'override_name', 'role', 'tenant', 'vrf', 'used_default_region',
            'prefix', 'poller', 'discovered', 'error',
            'device', 'requested_by', 'claimed_at', 'scanned_at', 'reviewed_at',
            'reviewed_by', 'applied_at',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'address', 'status')
        read_only_fields = (
            'status', 'site', 'prefix', 'poller', 'discovered', 'error', 'device',
            'claimed_at', 'scanned_at', 'reviewed_at', 'applied_at',
            'used_default_region',
        )


class PollerCheckInSerializer(serializers.Serializer):
    """What a poller sends when it wakes up.

    A poller identifies itself by name only — the same name as its
    `poller-<name>` tags. There is no registration step: an unknown name
    creates the record, so standing up a poller is installing the scanner and
    tagging some sites, with nothing to remember to do in the UI first.
    """

    name = serializers.CharField(
        help_text='Poller name, matching its poller-&lt;name&gt; tag'
    )
    version = serializers.CharField(required=False, allow_blank=True, default='')
    summary = serializers.CharField(
        required=False, allow_blank=True, default='',
        help_text='What the poller did on its last run, shown in the UI',
    )
    claim = serializers.BooleanField(
        default=True,
        help_text='Claim the returned work. False just looks, for a dry run.',
    )
    limit = serializers.IntegerField(
        default=25, min_value=1, max_value=500,
        help_text='Most requests to take in one check-in',
    )


class DiscoveredDeviceSerializer(serializers.Serializer):
    """One device a scan turned up — a standalone box, or one stack member."""

    name = serializers.CharField(allow_blank=True)
    model = serializers.CharField(required=False, allow_blank=True, default='')
    serial = serializers.CharField(required=False, allow_blank=True, default='')
    manufacturer = serializers.CharField(required=False, allow_blank=True, default='')
    platform = serializers.CharField(required=False, allow_blank=True, default='')
    software_version = serializers.CharField(required=False, allow_blank=True, default='')
    is_master = serializers.BooleanField(required=False, default=False)
    vc_position = serializers.IntegerField(required=False, allow_null=True, default=None)
    interfaces = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    modules = serializers.ListField(child=serializers.DictField(), required=False, default=list)


class ScanResultSerializer(serializers.Serializer):
    """A poller reporting what it found, without having written anything.

    Nothing here is applied. The request moves to `review` and waits for a
    person; that is the whole point of the two-step flow.
    """

    ok = serializers.BooleanField()
    error = serializers.CharField(required=False, allow_blank=True, default='')
    sys_name = serializers.CharField(required=False, allow_blank=True, default='')
    sys_descr = serializers.CharField(required=False, allow_blank=True, default='')
    credential = serializers.CharField(
        required=False, allow_blank=True, default='',
        help_text='Which credential set the device accepted',
    )
    devices = DiscoveredDeviceSerializer(many=True, required=False, default=list)
    access_points = serializers.ListField(
        child=serializers.DictField(), required=False, default=list
    )

    def validate(self, attrs):
        if attrs.get('ok') and not attrs.get('devices'):
            raise serializers.ValidationError(
                'A successful scan must report at least one device; send ok=false '
                'with an error instead.'
            )
        if not attrs.get('ok') and not attrs.get('error'):
            raise serializers.ValidationError(
                'A failed scan must say why, so the request page can show it.'
            )
        return attrs


class ApplyResultSerializer(serializers.Serializer):
    """A poller reporting the outcome of applying an approved request."""

    ok = serializers.BooleanField()
    device = serializers.IntegerField(
        required=False, allow_null=True, default=None,
        help_text='The device that was created or updated',
    )
    error = serializers.CharField(required=False, allow_blank=True, default='')

    def validate(self, attrs):
        if attrs.get('ok') and not attrs.get('device'):
            raise serializers.ValidationError(
                'A successful apply must name the device it produced.'
            )
        if not attrs.get('ok') and not attrs.get('error'):
            raise serializers.ValidationError('A failed apply must say why.')
        return attrs


class ApproveSerializer(serializers.Serializer):
    """Approving over the API, with the same overrides the review form offers.

    Every field is optional and omitting one leaves it as it is — so the common
    case, "yes, as scanned", is an empty body.
    """

    override_name = serializers.CharField(
        required=False, allow_blank=True,
        help_text="Name the device this instead of the hostname it reports",
    )
    override_site = serializers.PrimaryKeyRelatedField(
        queryset=Site.objects.all(), required=False, allow_null=True,
        help_text='Site to create the device at, instead of the derived one',
    )
    role = serializers.PrimaryKeyRelatedField(
        queryset=DeviceRole.objects.all(), required=False, allow_null=True,
        help_text="Device role; the poller's default when omitted",
    )


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False, allow_blank=True, default='',
        help_text='Shown on the request so the decision is not a mystery later',
    )


class JobSerializer(serializers.Serializer):
    """One unit of work handed to a poller at check-in."""

    id = serializers.IntegerField()
    address = serializers.CharField()
    action = serializers.ChoiceField(choices=('scan', 'apply'))
    site = serializers.IntegerField(allow_null=True)
    site_name = serializers.CharField(allow_blank=True)
    override_name = serializers.CharField(allow_blank=True)
    role = serializers.CharField(allow_blank=True)
    tenant = serializers.IntegerField(allow_null=True)
    tenant_name = serializers.CharField(allow_blank=True)


class HardwareReplacementSerializer(NetBoxModelSerializer):
    # Writable, not read-only: the poller creates these. Marked read-only they
    # are silently dropped from a POST and the create fails on a null device,
    # which is a confusing way to find out.
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_discovery-api:hardwarereplacement-detail'
    )
    device = DeviceSerializer(nested=True)
    replaced_device = DeviceSerializer(nested=True, required=False, allow_null=True)
    poller = DiscoveryPollerSerializer(nested=True, required=False, allow_null=True)
    detected_at = serializers.DateTimeField(required=False)

    class Meta:
        model = HardwareReplacement
        fields = (
            'url', 'id', 'display', 'kind', 'device', 'replaced_device',
            'module_bay', 'old_serial', 'new_serial', 'model_name',
            'detected_at', 'poller',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'old_serial', 'new_serial')
