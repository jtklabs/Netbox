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

from django.urls import path
from .upgrades import (UpgradeJobViewSet, ScheduleView, UpgradeCheckInView,
                       UpgradeReportView, UpgradeCancelView)

router.register('upgrade-jobs', UpgradeJobViewSet)
urlpatterns = [
    path('upgrade-jobs/schedule/', ScheduleView.as_view(), name='upgradejob-schedule'),
    path('upgrade-jobs/check-in/', UpgradeCheckInView.as_view(), name='upgradejob-check-in'),
    path('upgrade-jobs/<int:pk>/report/', UpgradeReportView.as_view(), name='upgradejob-report'),
    path('upgrade-jobs/<int:pk>/cancel/', UpgradeCancelView.as_view(), name='upgradejob-cancel'),
] + router.urls
