from netbox.api.routers import NetBoxRouter

from netbox_discovery.api.views import (
    DiscoveryIssueViewSet,
    DiscoveryPollerViewSet,
    HardwareReplacementViewSet,
    OnboardingRequestViewSet,
)

router = NetBoxRouter()
router.register('pollers', DiscoveryPollerViewSet)
router.register('onboarding-requests', OnboardingRequestViewSet)
router.register('hardware-replacements', HardwareReplacementViewSet)
router.register('issues', DiscoveryIssueViewSet)
urlpatterns = router.urls
