import django_tables2 as tables
from netbox.tables import NetBoxTable, columns

from netbox_refresh.models import ModelLifecycle

__all__ = ('ModelLifecycleTable', 'RefreshReportTable')


class ModelLifecycleTable(NetBoxTable):
    assigned_object = tables.Column(
        linkify=True, orderable=False, verbose_name='Hardware model',
    )
    manufacturer = tables.Column(linkify=True, orderable=False)
    part_number = tables.Column(orderable=False)
    status = columns.TemplateColumn(
        template_code='{% load helpers %}{% badge record.get_status_display '
                      'bg_color=record.get_status_color %}',
        orderable=False,
    )
    replacement = tables.Column(linkify=True, orderable=False)
    installed_count = tables.Column(orderable=False, verbose_name='Installed')
    extended_cost = tables.Column(orderable=False, verbose_name='Total cost')
    source = columns.ChoiceFieldColumn()

    class Meta(NetBoxTable.Meta):
        model = ModelLifecycle
        fields = (
            'pk', 'assigned_object', 'manufacturer', 'part_number', 'status',
            'announcement_date', 'end_of_sale', 'end_of_sw_maintenance',
            'end_of_security_support', 'end_of_routine_failure_analysis',
            'end_of_service_attach', 'end_of_service_contract_renewal', 'end_of_support',
            'replacement', 'replacement_cost', 'currency', 'installed_count',
            'extended_cost', 'cost_updated', 'bulletin_number', 'source', 'last_synced',
            'description',
        )
        default_columns = (
            'assigned_object', 'part_number', 'status', 'end_of_sale', 'end_of_support',
            'replacement', 'replacement_cost', 'installed_count', 'extended_cost',
        )


class RefreshReportTable(tables.Table):
    """Report rows are plain dicts assembled by the view, not model instances."""

    model = tables.Column(linkify=lambda record: record['url'], verbose_name='Hardware model')
    manufacturer = tables.Column()
    part_number = tables.Column()
    milestone_date = tables.DateColumn(verbose_name='Milestone')
    installed = tables.Column()
    replacement = tables.Column(linkify=lambda record: record['replacement_url'])
    unit_cost = tables.Column()
    extended_cost = tables.Column()

    class Meta:
        attrs = {'class': 'table table-hover object-list'}
        empty_text = 'No hardware models reach this milestone in the selected window.'
        orderable = False
