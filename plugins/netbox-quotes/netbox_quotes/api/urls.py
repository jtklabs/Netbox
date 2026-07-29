from netbox.api.routers import NetBoxRouter

from netbox_quotes.api.views import QuoteLineViewSet, QuoteViewSet, VendorViewSet

router = NetBoxRouter()
router.register('vendors', VendorViewSet)
router.register('quotes', QuoteViewSet)
router.register('quote-lines', QuoteLineViewSet)
urlpatterns = router.urls
