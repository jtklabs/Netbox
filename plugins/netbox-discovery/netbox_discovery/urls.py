from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from netbox_discovery import views
from netbox_discovery.models import (
    DiscoveryIssue,
    DiscoveryPoller,
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
]
