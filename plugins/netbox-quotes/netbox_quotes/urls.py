from django.urls import path
from netbox.views.generic import ObjectChangeLogView

from netbox_quotes import views
from netbox_quotes.models import Quote, QuoteLine, Vendor

urlpatterns = [
    # Vendors
    path('vendors/', views.VendorListView.as_view(), name='quotevendor_list'),
    path('vendors/add/', views.VendorEditView.as_view(), name='quotevendor_add'),
    path('vendors/import/', views.VendorBulkImportView.as_view(), name='quotevendor_bulk_import'),
    path('vendors/delete/', views.VendorBulkDeleteView.as_view(), name='quotevendor_bulk_delete'),
    path('vendors/<int:pk>/', views.VendorView.as_view(), name='quotevendor'),
    path('vendors/<int:pk>/edit/', views.VendorEditView.as_view(), name='quotevendor_edit'),
    path('vendors/<int:pk>/delete/', views.VendorDeleteView.as_view(), name='quotevendor_delete'),
    path(
        'vendors/<int:pk>/changelog/',
        ObjectChangeLogView.as_view(),
        name='quotevendor_changelog',
        kwargs={'model': Vendor},
    ),
    # Quotes
    path('quotes/', views.QuoteListView.as_view(), name='quote_list'),
    path('quotes/add/', views.QuoteEditView.as_view(), name='quote_add'),
    path('quotes/import/', views.QuoteBulkImportView.as_view(), name='quote_bulk_import'),
    path('quotes/delete/', views.QuoteBulkDeleteView.as_view(), name='quote_bulk_delete'),
    path('quotes/<int:pk>/', views.QuoteView.as_view(), name='quote'),
    path('quotes/<int:pk>/edit/', views.QuoteEditView.as_view(), name='quote_edit'),
    path('quotes/<int:pk>/delete/', views.QuoteDeleteView.as_view(), name='quote_delete'),
    path('quotes/<int:pk>/rematch/', views.QuoteRematchView.as_view(), name='quote_rematch'),
    path(
        'quotes/<int:pk>/changelog/',
        ObjectChangeLogView.as_view(),
        name='quote_changelog',
        kwargs={'model': Quote},
    ),
    # Quote lines
    path('lines/', views.QuoteLineListView.as_view(), name='quoteline_list'),
    path('lines/add/', views.QuoteLineEditView.as_view(), name='quoteline_add'),
    path('lines/import/', views.QuoteLineBulkImportView.as_view(), name='quoteline_bulk_import'),
    path('lines/delete/', views.QuoteLineBulkDeleteView.as_view(), name='quoteline_bulk_delete'),
    path('lines/<int:pk>/', views.QuoteLineView.as_view(), name='quoteline'),
    path('lines/<int:pk>/edit/', views.QuoteLineEditView.as_view(), name='quoteline_edit'),
    path('lines/<int:pk>/delete/', views.QuoteLineDeleteView.as_view(), name='quoteline_delete'),
    path(
        'lines/<int:pk>/changelog/',
        ObjectChangeLogView.as_view(),
        name='quoteline_changelog',
        kwargs={'model': QuoteLine},
    ),

    path('reports/coverage-expiry/', views.CoverageExpiryReportView.as_view(),
         name='coverage_expiry_report'),
    path('reports/eol-transition/', views.EolTransitionReportView.as_view(),
         name='eol_transition_report'),
]
