from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from netbox_compliance import views
from netbox_compliance.models import ConfigCompliance, ConfigStandard

# Route names must match the lowercased model class name — NetBox's generic
# views and get_absolute_url() both derive the name that way, and a mismatch
# fails at reverse() time rather than at import time. Same rule applies to the
# detail template filenames: configstandard.html, configcompliance.html.
urlpatterns = [
    path('standards/', views.ConfigStandardListView.as_view(), name='configstandard_list'),
    path('standards/add/', views.ConfigStandardEditView.as_view(), name='configstandard_add'),
    path('standards/edit/', views.ConfigStandardBulkEditView.as_view(),
         name='configstandard_bulk_edit'),
    path('standards/delete/', views.ConfigStandardBulkDeleteView.as_view(),
         name='configstandard_bulk_delete'),
    path('standards/<int:pk>/', views.ConfigStandardView.as_view(), name='configstandard'),
    path('standards/<int:pk>/edit/', views.ConfigStandardEditView.as_view(),
         name='configstandard_edit'),
    path('standards/<int:pk>/delete/', views.ConfigStandardDeleteView.as_view(),
         name='configstandard_delete'),
    path('standards/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='configstandard_changelog', kwargs={'model': ConfigStandard}),

    path('results/', views.ConfigComplianceListView.as_view(), name='configcompliance_list'),
    path('results/add/', views.ConfigComplianceEditView.as_view(), name='configcompliance_add'),
    path('results/edit/', views.ConfigComplianceBulkEditView.as_view(),
         name='configcompliance_bulk_edit'),
    path('results/delete/', views.ConfigComplianceBulkDeleteView.as_view(),
         name='configcompliance_bulk_delete'),
    path('results/<int:pk>/', views.ConfigComplianceView.as_view(), name='configcompliance'),
    path('results/<int:pk>/edit/', views.ConfigComplianceEditView.as_view(),
         name='configcompliance_edit'),
    path('results/<int:pk>/delete/', views.ConfigComplianceDeleteView.as_view(),
         name='configcompliance_delete'),
    path('results/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='configcompliance_changelog', kwargs={'model': ConfigCompliance}),

    path('report/', views.ComplianceReportView.as_view(), name='compliance_report'),
]
