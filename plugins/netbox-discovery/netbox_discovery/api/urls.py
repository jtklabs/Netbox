from netbox.api.routers import NetBoxRouter

from netbox_discovery.api.views import (
    DiscoveryPollerViewSet,
    HardwareReplacementViewSet,
    OnboardingRequestViewSet,
)

router = NetBoxRouter()
router.register('pollers', DiscoveryPollerViewSet)
router.register('onboarding-requests', OnboardingRequestViewSet)
router.register('hardware-replacements', HardwareReplacementViewSet)
urlpatterns = router.urls
