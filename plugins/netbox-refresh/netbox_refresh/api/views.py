from netbox.api.viewsets import NetBoxModelViewSet

from netbox_refresh import filtersets
from netbox_refresh.api.serializers import ModelLifecycleSerializer
from netbox_refresh.models import ModelLifecycle

__all__ = ('ModelLifecycleViewSet',)


class ModelLifecycleViewSet(NetBoxModelViewSet):
    queryset = ModelLifecycle.objects.prefetch_related(
        'assigned_object_type', 'replacement_device_type', 'replacement_module_type', 'tags'
    )
    serializer_class = ModelLifecycleSerializer
    filterset_class = filtersets.ModelLifecycleFilterSet
