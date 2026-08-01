from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from netbox_refresh import views
from netbox_refresh.models import ModelLifecycle

urlpatterns = [
    path('lifecycle/', views.ModelLifecycleListView.as_view(), name='modellifecycle_list'),
    path('lifecycle/add/', views.ModelLifecycleEditView.as_view(), name='modellifecycle_add'),
    path('lifecycle/import/', views.ModelLifecycleBulkImportView.as_view(),
         name='modellifecycle_bulk_import'),
    path('lifecycle/edit/', views.ModelLifecycleBulkEditView.as_view(),
         name='modellifecycle_bulk_edit'),
    path('lifecycle/delete/', views.ModelLifecycleBulkDeleteView.as_view(),
         name='modellifecycle_bulk_delete'),
    path('lifecycle/<int:pk>/', views.ModelLifecycleView.as_view(), name='modellifecycle'),
    path('lifecycle/<int:pk>/edit/', views.ModelLifecycleEditView.as_view(),
         name='modellifecycle_edit'),
    path('lifecycle/<int:pk>/delete/', views.ModelLifecycleDeleteView.as_view(),
         name='modellifecycle_delete'),
    path('lifecycle/<int:pk>/changelog/', ObjectChangeLogView.as_view(),
         name='modellifecycle_changelog', kwargs={'model': ModelLifecycle}),

    path('report/', views.RefreshReportView.as_view(), name='refresh_report'),
    path('sync/cisco/', views.CiscoSyncView.as_view(), name='cisco_sync'),
]
