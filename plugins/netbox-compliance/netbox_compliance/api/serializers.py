"""REST serializers.

The checker is the main consumer, and it needs two things NetBox does not give
it for free: a standard rendered in a form it can act on without re-deriving
anything (`entries`, `runtime_variables`, `scope_summary`), and a way to post a
result per device per standard without first working out whether a row exists.

`ConfigCheckReportSerializer` is the second of those. It is deliberately not a
ModelSerializer: it takes the device by name or id and the standard by name or
id, because the checker knows a hostname and a standard's name — making it look
up primary keys first would be two extra round trips per device for nothing.
"""

from dcim.api.serializers import (
    DeviceRoleSerializer,
    DeviceSerializer,
    PlatformSerializer,
    SiteSerializer,
)
from extras.api.serializers import TagSerializer
from netbox.api.serializers import NetBoxModelSerializer
from rest_framework import serializers

from netbox_compliance.choices import ConfigCheckResultChoices, ConfigCheckSourceChoices
from netbox_compliance.models import ConfigCompliance, ConfigStandard

__all__ = (
    'ConfigStandardSerializer',
    'ConfigComplianceSerializer',
    'ConfigCheckReportSerializer',
)


class ConfigStandardSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_compliance-api:configstandard-detail'
    )
    is_active = serializers.BooleanField(read_only=True)
    scope_summary = serializers.CharField(read_only=True)
    # The normalised form of expected_entries. The stored field accepts bare
    # strings as shorthand; this always comes back as a list of
    # {"key", "vars"} objects so a client never has to handle both shapes.
    entries = serializers.ListField(read_only=True)
    runtime_variables = serializers.ListField(
        read_only=True,
        help_text='Template variables the checker must supply itself — typically the secret',
    )
    result_count = serializers.IntegerField(read_only=True, required=False)

    # Nested rather than bare primary keys, following netbox_quotes: the portal
    # renders "which platforms is this standard for" straight off the list, and
    # a page of integers costs it a lookup per row. Writes still take ids.
    platforms = PlatformSerializer(nested=True, many=True, required=False)
    roles = DeviceRoleSerializer(nested=True, many=True, required=False)
    sites = SiteSerializer(nested=True, many=True, required=False)
    device_tags = TagSerializer(nested=True, many=True, required=False)

    class Meta:
        model = ConfigStandard
        fields = (
            'url', 'id', 'display', 'name', 'check_type',
            'match_pattern', 'expected_entries', 'entries',
            'add_template', 'remove_template', 'runtime_variables',
            'auto_remediable', 'allow_enforce', 'remediation_notes',
            'platforms', 'roles', 'sites', 'device_tags', 'scope_summary',
            'valid_from', 'valid_to', 'is_active', 'result_count',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'name', 'check_type', 'auto_remediable')


class ConfigComplianceSerializer(NetBoxModelSerializer):
    url = serializers.HyperlinkedIdentityField(
        view_name='plugins-api:netbox_compliance-api:configcompliance-detail'
    )
    # Derived: the stored result with any exemption folded over it. Read-only
    # because writing it would mean deciding which of the two underlying facts
    # the caller meant to change.
    status = serializers.CharField(read_only=True)
    finding_count = serializers.IntegerField(read_only=True)
    needs_manual_fix = serializers.BooleanField(read_only=True)
    is_stale = serializers.BooleanField(read_only=True)

    device = DeviceSerializer(nested=True)
    standard = ConfigStandardSerializer(nested=True)

    class Meta:
        model = ConfigCompliance
        fields = (
            'url', 'id', 'display', 'device', 'standard',
            'result', 'status', 'observed', 'findings', 'finding_count',
            'error_message', 'source', 'last_checked', 'is_stale',
            'pre_change_config', 'pre_change_at', 'last_remediated', 'remediation_log',
            'exempt', 'exempt_reason', 'exempt_approved_by', 'exempt_approved_on',
            'exempt_review_by', 'needs_manual_fix',
            'description', 'comments', 'tags', 'custom_fields', 'created', 'last_updated',
        )
        brief_fields = ('url', 'id', 'display', 'device', 'standard', 'result', 'status')


class ConfigCheckReportSerializer(serializers.Serializer):
    """One device's result for one standard, as posted by the checker.

    Device and standard may be given by id or by name. Names are what a checker
    actually has in hand — it connected to a hostname and read a standard called
    "No HTTP server" — and requiring ids would mean a lookup round trip per
    device before it could report anything.
    """

    device = serializers.CharField(
        required=False, help_text='Device name. Give this or device_id.'
    )
    device_id = serializers.IntegerField(required=False)
    standard = serializers.CharField(
        required=False, help_text='Standard name. Give this or standard_id.'
    )
    standard_id = serializers.IntegerField(required=False)

    result = serializers.ChoiceField(choices=ConfigCheckResultChoices)
    observed = serializers.CharField(
        required=False, allow_blank=True,
        help_text='Redacted governed lines. Do not send unredacted configuration.',
    )
    findings = serializers.JSONField(required=False)
    error_message = serializers.CharField(required=False, allow_blank=True, max_length=500)
    source = serializers.ChoiceField(
        choices=ConfigCheckSourceChoices, required=False,
        default=ConfigCheckSourceChoices.SOURCE_SSH,
    )
    checked_at = serializers.DateTimeField(required=False)

    # Remediation evidence, sent only by a run that actually wrote something.
    pre_change_config = serializers.CharField(required=False, allow_blank=True)
    remediation_log = serializers.CharField(required=False, allow_blank=True)
    remediated = serializers.BooleanField(required=False, default=False)

    def validate(self, data):
        if not data.get('device') and not data.get('device_id'):
            raise serializers.ValidationError('Give a device name or a device_id.')
        if not data.get('standard') and not data.get('standard_id'):
            raise serializers.ValidationError('Give a standard name or a standard_id.')
        if data.get('result') == ConfigCheckResultChoices.RESULT_ERROR and not (
            data.get('error_message') or ''
        ).strip():
            raise serializers.ValidationError(
                {'error_message': 'A failed check has to say what went wrong.'}
            )
        return data
