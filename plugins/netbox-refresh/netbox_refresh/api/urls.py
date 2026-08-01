from netbox.api.routers import NetBoxRouter

from netbox_refresh.api.views import ModelLifecycleViewSet

router = NetBoxRouter()
router.register('lifecycles', ModelLifecycleViewSet)
urlpatterns = router.urls
