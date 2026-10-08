from netbox.api.routers import NetBoxRouter

from netbox_discovery.api.views import (
    DiscoveryIssueViewSet,
    DiscoveryPollerViewSet,
    DiscoveryRuleViewSet,
    HardwareReplacementViewSet,
    OnboardingRequestViewSet,
    StrippedDomainViewSet,
)

router = NetBoxRouter()
router.register('pollers', DiscoveryPollerViewSet)
router.register('onboarding-requests', OnboardingRequestViewSet)
router.register('hardware-replacements', HardwareReplacementViewSet)
router.register('issues', DiscoveryIssueViewSet)
router.register('rules', DiscoveryRuleViewSet)
router.register('stripped-domains', StrippedDomainViewSet)
urlpatterns = router.urls

from django.urls import path
from .upgrades import (UpgradeJobViewSet, ScheduleView, UpgradeCheckInView, UpgradeReportView, UpgradeCancelView,
                       UpgradeGroupViewSet, UpgradeDependencyViewSet, UpgradeHoldView, UpgradeReleaseView,
                       UpgradeReleaseBatchView, UpgradeRefreshGroupsView, UpgradeRequeueView, PrestagePolicyViewSet,
                       PrestageRunView)

from .commands import CommandOutputViewSet
from .profiles import JobProfileViewSet, DeviceTypeProfileViewSet, PlatformProfileViewSet
from .audits import AuditScheduleViewSet, AuditRunViewSet

router.register('upgrade-jobs', UpgradeJobViewSet)
router.register('upgrade-groups', UpgradeGroupViewSet)
router.register('upgrade-dependencies', UpgradeDependencyViewSet)
router.register('command-outputs', CommandOutputViewSet)
router.register('prestage-policies', PrestagePolicyViewSet)
router.register('job-profiles', JobProfileViewSet)
router.register('model-profiles', DeviceTypeProfileViewSet)
router.register('platform-profiles', PlatformProfileViewSet)
router.register('audit-schedules', AuditScheduleViewSet)
router.register('audit-runs', AuditRunViewSet)
urlpatterns = [
    path('upgrade-jobs/schedule/', ScheduleView.as_view(), name='upgradejob-schedule'),
    path('upgrade-jobs/check-in/', UpgradeCheckInView.as_view(), name='upgradejob-check-in'),
    path('upgrade-jobs/release-batch/', UpgradeReleaseBatchView.as_view(), name='upgradejob-release-batch'),
    path('upgrade-jobs/<int:pk>/report/', UpgradeReportView.as_view(), name='upgradejob-report'),
    path('upgrade-jobs/<int:pk>/cancel/', UpgradeCancelView.as_view(), name='upgradejob-cancel'),
    path('upgrade-jobs/<int:pk>/hold/', UpgradeHoldView.as_view(), name='upgradejob-hold'),
    path('upgrade-jobs/<int:pk>/release/', UpgradeReleaseView.as_view(), name='upgradejob-release'),
    path('upgrade-jobs/<int:pk>/requeue/', UpgradeRequeueView.as_view(), name='upgradejob-requeue'),
    path('upgrade-groups/refresh/', UpgradeRefreshGroupsView.as_view(), name='upgradegroup-refresh'),
    path('prestage-policies/run/', PrestageRunView.as_view(), name='prestagepolicy-run'),
] + router.urls
