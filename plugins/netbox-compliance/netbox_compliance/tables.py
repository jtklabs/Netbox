"""Tables for the standards list, the results list and the two reports.

The staleness marker is rendered beside the status rather than replacing it,
for the same reason netbox_refresh does it: a device that passed three months
ago has a green verdict and an unreliable one, and a table that shows only the
green badge is quietly lying about the fleet.
"""

import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_compliance.models import ConfigCompliance, ConfigStandard

__all__ = (
    'ConfigStandardTable',
    'ConfigComplianceTable',
    'ComplianceReportTable',
    'StandardRollupTable',
)

STALE_MARKER = (
    '{% if record.is_stale %}<span class="text-warning ms-1" '
    'title="Not checked recently"><i class="mdi mdi-clock-alert-outline"></i></span>{% endif %}'
)


class ConfigStandardTable(NetBoxTable):
    name = tables.Column(linkify=True)
    check_type = columns.ChoiceFieldColumn()
    # Audit-only is the fact an operator most needs off this list: it is the
    # difference between "the next update run clears this" and "somebody has to
    # go and do it".
    remediation = columns.TemplateColumn(
        template_code=(
            '{% load helpers %}'
            '{% if not record.auto_remediable %}{% badge "Audit only" bg_color="gray" %}'
            '{% elif record.allow_enforce %}{% badge "Enforce allowed" bg_color="orange" %}'
            '{% else %}{% badge "Update only" bg_color="blue" %}{% endif %}'
        ),
        orderable=False,
        verbose_name='Remediation',
    )
    scope_summary = tables.Column(orderable=False, verbose_name='Scope')
    match_pattern = columns.TemplateColumn(
        template_code='<code>{{ record.match_pattern }}</code>', verbose_name='Pattern',
    )
    is_active = columns.BooleanColumn(orderable=False, verbose_name='In force')
    result_count = tables.Column(orderable=False, verbose_name='Results')

    class Meta(NetBoxTable.Meta):
        model = ConfigStandard
        fields = (
            'pk', 'id', 'name', 'check_type', 'remediation', 'match_pattern',
            'scope_summary', 'auto_remediable', 'allow_enforce',
            'valid_from', 'valid_to', 'is_active', 'result_count',
            'description', 'created', 'last_updated',
        )
        default_columns = (
            'name', 'check_type', 'remediation', 'scope_summary',
            'valid_from', 'is_active', 'result_count',
        )


class ConfigComplianceTable(NetBoxTable):
    device = tables.Column(linkify=True)
    standard = tables.Column(linkify=True)
    status = columns.TemplateColumn(
        template_code=(
            '{% load helpers %}'
            '{% badge record.get_status_display bg_color=record.get_status_color %}'
            + STALE_MARKER
        ),
        orderable=False,
        verbose_name='Status',
    )
    result = columns.ChoiceFieldColumn()
    source = columns.ChoiceFieldColumn()
    finding_count = tables.Column(orderable=False, verbose_name='Findings')
    needs_manual_fix = columns.BooleanColumn(orderable=False, verbose_name='Manual fix')
    site = tables.Column(accessor='device__site', linkify=True)

    class Meta(NetBoxTable.Meta):
        model = ConfigCompliance
        fields = (
            'pk', 'id', 'device', 'site', 'standard', 'status', 'result',
            'finding_count', 'needs_manual_fix', 'source', 'last_checked',
            'last_remediated', 'exempt', 'exempt_review_by', 'error_message',
            'description', 'created', 'last_updated',
        )
        default_columns = (
            'device', 'site', 'standard', 'status', 'finding_count',
            'source', 'last_checked',
        )


class ComplianceReportTable(tables.Table):
    """Report rows are plain dicts, because most of them have no database row.

    A device in scope for a standard nobody has run against it is the row the
    report exists to surface, and it is exactly the row that cannot be a model
    instance. Rendering the report off ConfigCompliance would make those
    devices invisible.
    """

    device = tables.Column(linkify=lambda record: record['device'].get_absolute_url())
    site = tables.Column(accessor='device.site', linkify=True)
    standard = tables.Column(linkify=lambda record: record['standard'].get_absolute_url())
    status = columns.TemplateColumn(
        template_code=(
            '{% load helpers %}'
            '{% badge record.status_label bg_color=record.status_color %}'
            '{% if record.is_stale %}<span class="text-warning ms-1" '
            'title="Not checked recently"><i class="mdi mdi-clock-alert-outline"></i>'
            '</span>{% endif %}'
        ),
        orderable=False,
    )
    findings = tables.Column(verbose_name='Findings')
    needs_manual_fix = columns.BooleanColumn(verbose_name='Manual fix')
    last_checked = columns.DateTimeColumn(verbose_name='Last checked')

    class Meta:
        attrs = {'class': 'table table-hover object-list'}
        empty_text = 'No devices are in scope for any standard in force.'
        orderable = False


class StandardRollupTable(tables.Table):
    """Per-standard totals — "how many devices are missing X", one row per X.

    Coverage sits next to compliance deliberately. A standard checked on four
    devices out of a thousand and passing on all four is 100% compliant and
    0.4% covered, and only one of those numbers is worth anything on its own.
    """

    standard = tables.Column(linkify=lambda record: record['standard'].get_absolute_url())
    in_scope = tables.Column(verbose_name='In scope')
    checked = tables.Column()
    coverage = columns.TemplateColumn(
        template_code='{% if record.coverage is not None %}{{ record.coverage }}%'
                      '{% else %}<span class="text-muted">&mdash;</span>{% endif %}',
        verbose_name='Coverage',
    )
    compliant = tables.Column()
    non_compliant = tables.Column(verbose_name='Non-compliant')
    unknown = tables.Column(verbose_name='Not checked')
    error = tables.Column(verbose_name='Failed')
    exempt = tables.Column()
    compliance = columns.TemplateColumn(
        template_code='{% if record.compliance is not None %}{{ record.compliance }}%'
                      '{% else %}<span class="text-muted">&mdash;</span>{% endif %}',
        verbose_name='Compliant %',
    )

    class Meta:
        attrs = {'class': 'table table-hover object-list'}
        empty_text = 'No standards are in force for the selected devices.'
        orderable = False
