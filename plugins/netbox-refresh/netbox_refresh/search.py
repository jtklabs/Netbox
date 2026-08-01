from netbox.search import SearchIndex, register_search

from netbox_refresh.models import ModelLifecycle


@register_search
class ModelLifecycleIndex(SearchIndex):
    model = ModelLifecycle
    fields = (
        ('bulletin_number', 100),
        ('replacement_notes', 2000),
        ('description', 4000),
        ('comments', 5000),
    )
    display_attrs = ('end_of_support', 'source')
