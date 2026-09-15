from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from netbox_discovery import views
from netbox_discovery.models import (
    DiscoveryIssue,
    DiscoveryPoller,
    DiscoveryRule,
    HardwareReplacement,
    OnboardingRequest,
)

# Route names must match the lowercased model class name — NetBox's generic
# views and get_absolute_url() both derive the name that way, and a mismatch
# fails at reverse() time rather than at import time.
urlpatterns = [
    path('onboarding/', views.OnboardingRequestListView.as_view(),
         name='onboardingrequest_list'),
    path('onboarding/add/', views.OnboardingRequestEditView.as_view(),
         name='onboardingrequest_add'),
    path('onboarding/import/', views.OnboardingRequestBulkImportView.as_view(),
         name='onboardingrequest_bulk_import'),
    path('onboarding/delete/', views.OnboardingRequestBulkDeleteView.as_view(),
         name='onboardingrequest_bulk_delete'),
    path('onboarding/<int:pk>/', views.OnboardingRequestView.as_view(),
         name='onboardingrequest'),
    path('onboarding/<int:pk>/edit/', views.OnboardingRequestEditView.as_view(),
         name='onboardingrequest_edit'),
    path('onboarding/<int:pk>/delete/', views.OnboardingRequestDeleteView.as_view(),
         name='onboardingrequest_delete'),
    path('onboarding/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='onboardingrequest_changelog', kwargs={'model': OnboardingRequest}),
    path('onboarding/<int:pk>/approve/', views.OnboardingApproveView.as_view(),
         name='onboardingrequest_approve'),
    path('onboarding/<int:pk>/reject/', views.OnboardingRejectView.as_view(),
         name='onboardingrequest_reject'),
    path('onboarding/<int:pk>/retry/', views.OnboardingRetryView.as_view(),
         name='onboardingrequest_retry'),
    path('onboarding/<int:pk>/recheck/', views.OnboardingRecheckView.as_view(),
         name='onboardingrequest_recheck'),
    path('onboarding/bulk-recheck/', views.OnboardingBulkRecheckView.as_view(),
         name='onboardingrequest_bulk_recheck'),
    path('onboarding/bulk-retry/', views.OnboardingBulkRetryView.as_view(),
         name='onboardingrequest_bulk_retry'),
    path('onboarding/<int:pk>/manual/', views.OnboardingManualEntryView.as_view(),
         name='onboardingrequest_manual'),

    path('pollers/', views.DiscoveryPollerListView.as_view(), name='discoverypoller_list'),
    path('pollers/add/', views.DiscoveryPollerEditView.as_view(), name='discoverypoller_add'),
    path('pollers/delete/', views.DiscoveryPollerBulkDeleteView.as_view(),
         name='discoverypoller_bulk_delete'),
    path('pollers/<int:pk>/', views.DiscoveryPollerView.as_view(), name='discoverypoller'),
    path('pollers/<int:pk>/edit/', views.DiscoveryPollerEditView.as_view(),
         name='discoverypoller_edit'),
    path('pollers/<int:pk>/delete/', views.DiscoveryPollerDeleteView.as_view(),
         name='discoverypoller_delete'),
    path('pollers/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='discoverypoller_changelog', kwargs={'model': DiscoveryPoller}),

    path('replacements/', views.HardwareReplacementListView.as_view(),
         name='hardwarereplacement_list'),
    path('replacements/delete/', views.HardwareReplacementBulkDeleteView.as_view(),
         name='hardwarereplacement_bulk_delete'),
    path('replacements/<int:pk>/', views.HardwareReplacementView.as_view(),
         name='hardwarereplacement'),
    path('replacements/<int:pk>/delete/', views.HardwareReplacementDeleteView.as_view(),
         name='hardwarereplacement_delete'),
    path('replacements/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='hardwarereplacement_changelog', kwargs={'model': HardwareReplacement}),

    path('issues/', views.DiscoveryIssueListView.as_view(), name='discoveryissue_list'),
    path('issues/delete/', views.DiscoveryIssueBulkDeleteView.as_view(),
         name='discoveryissue_bulk_delete'),
    path('issues/<int:pk>/', views.DiscoveryIssueView.as_view(), name='discoveryissue'),
    path('issues/<int:pk>/edit/', views.DiscoveryIssueEditView.as_view(),
         name='discoveryissue_edit'),
    path('issues/<int:pk>/delete/', views.DiscoveryIssueDeleteView.as_view(),
         name='discoveryissue_delete'),
    path('issues/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='discoveryissue_changelog', kwargs={'model': DiscoveryIssue}),

    path('rules/', views.DiscoveryRuleListView.as_view(), name='discoveryrule_list'),
    path('rules/add/', views.DiscoveryRuleEditView.as_view(), name='discoveryrule_add'),
    path('rules/delete/', views.DiscoveryRuleBulkDeleteView.as_view(),
         name='discoveryrule_bulk_delete'),
    path('rules/<int:pk>/', views.DiscoveryRuleView.as_view(), name='discoveryrule'),
    path('rules/<int:pk>/edit/', views.DiscoveryRuleEditView.as_view(),
         name='discoveryrule_edit'),
    path('rules/<int:pk>/delete/', views.DiscoveryRuleDeleteView.as_view(),
         name='discoveryrule_delete'),
    path('rules/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='discoveryrule_changelog', kwargs={'model': DiscoveryRule}),
]

from . import command_views, upgrade_views
from .models import UpgradeDependency, UpgradeGroup, UpgradeJob

urlpatterns += [
    path('command-outputs/<int:pk>/', command_views.CommandOutputRawView.as_view(), name='commandoutput_raw'),
    path('command-outputs/<int:pk>/download/', command_views.CommandOutputDownloadView.as_view(), name='commandoutput_download'),
    path('devices/<int:pk>/command-outputs/', command_views.DeviceCommandOutputsView.as_view(), name='device_command_outputs'),
]

urlpatterns += [
    path('upgrades/', upgrade_views.UpgradeJobListView.as_view(), name='upgradejob_list'),
    path('upgrades/add/', upgrade_views.UpgradeScheduleView.as_view(), name='upgradejob_add'),
    path('upgrades/<int:pk>/', upgrade_views.UpgradeJobView.as_view(), name='upgradejob'),
    path('upgrades/<int:pk>/edit/', upgrade_views.UpgradeJobEditView.as_view(), name='upgradejob_edit'),
    path('upgrades/<int:pk>/cancel/', upgrade_views.UpgradeCancelView.as_view(), name='upgradejob_cancel'),
    path('upgrades/<int:pk>/changelog/', ObjectChangeLogView.as_view(), name='upgradejob_changelog',
         kwargs={'model': UpgradeJob}),
    path('upgrades/<int:pk>/hold/', upgrade_views.UpgradeHoldView.as_view(), name='upgradejob_hold'),
    path('upgrades/<int:pk>/release/', upgrade_views.UpgradeReleaseView.as_view(), name='upgradejob_release'),
    path('upgrade-groups/', upgrade_views.UpgradeGroupListView.as_view(), name='upgradegroup_list'),
    path('upgrade-groups/add/', upgrade_views.UpgradeGroupEditView.as_view(), name='upgradegroup_add'),
    path('upgrade-groups/refresh/', upgrade_views.UpgradeGroupRefreshView.as_view(), name='upgradegroup_refresh'),
    path('upgrade-groups/<int:pk>/', upgrade_views.UpgradeGroupView.as_view(), name='upgradegroup'),
    path('upgrade-groups/<int:pk>/edit/', upgrade_views.UpgradeGroupEditView.as_view(), name='upgradegroup_edit'),
    path('upgrade-groups/<int:pk>/delete/', upgrade_views.UpgradeGroupDeleteView.as_view(), name='upgradegroup_delete'),
    path('upgrade-groups/<int:pk>/changelog/', ObjectChangeLogView.as_view(), name='upgradegroup_changelog',
         kwargs={'model': UpgradeGroup}),
    path('upgrade-dependencies/', upgrade_views.UpgradeDependencyListView.as_view(), name='upgradedependency_list'),
    path('upgrade-dependencies/add/', upgrade_views.UpgradeDependencyEditView.as_view(), name='upgradedependency_add'),
    path('upgrade-dependencies/<int:pk>/', upgrade_views.UpgradeDependencyView.as_view(), name='upgradedependency'),
    path('upgrade-dependencies/<int:pk>/edit/', upgrade_views.UpgradeDependencyEditView.as_view(), name='upgradedependency_edit'),
    path('upgrade-dependencies/<int:pk>/delete/', upgrade_views.UpgradeDependencyDeleteView.as_view(), name='upgradedependency_delete'),
    path('upgrade-dependencies/<int:pk>/changelog/', ObjectChangeLogView.as_view(), name='upgradedependency_changelog',
         kwargs={'model': UpgradeDependency}),
]
