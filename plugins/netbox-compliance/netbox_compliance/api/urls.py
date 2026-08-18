from netbox.api.routers import NetBoxRouter

from netbox_compliance.api.views import ConfigComplianceViewSet, ConfigStandardViewSet

router = NetBoxRouter()
router.register('config-standards', ConfigStandardViewSet)
router.register('config-compliance', ConfigComplianceViewSet)
urlpatterns = router.urls
