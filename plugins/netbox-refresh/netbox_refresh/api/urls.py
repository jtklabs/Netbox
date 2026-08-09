from netbox.api.routers import NetBoxRouter

from netbox_refresh.api.views import (
    DeviceSoftwareViewSet,
    ModelLifecycleViewSet,
    SoftwareStandardViewSet,
    SoftwareVersionViewSet,
)

router = NetBoxRouter()
router.register('lifecycles', ModelLifecycleViewSet)
router.register('software-versions', SoftwareVersionViewSet)
router.register('software-standards', SoftwareStandardViewSet)
router.register('device-software', DeviceSoftwareViewSet)
urlpatterns = router.urls
