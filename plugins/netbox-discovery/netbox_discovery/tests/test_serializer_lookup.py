"""NetBox resolves serializers by model name during change logging/deletion."""
from django.apps import apps
from django.test import SimpleTestCase
from netbox.models import PrimaryModel
from utilities.api import get_serializer_for_model


class SerializerLookupTest(SimpleTestCase):
    def test_all_primary_models_expose_their_serializer_to_netbox(self):
        for model in apps.get_app_config('netbox_discovery').get_models():
            if not issubclass(model, PrimaryModel):
                continue
            with self.subTest(model=model.__name__):
                self.assertIs(get_serializer_for_model(model).Meta.model, model)
