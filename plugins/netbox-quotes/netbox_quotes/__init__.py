from netbox.plugins import PluginConfig


class QuotesConfig(PluginConfig):
    name = 'netbox_quotes'
    verbose_name = 'Support Quotes'
    description = 'Vendor support-renewal quotes with serial-matched device assignment'
    version = '0.1.0'
    author = 'JTK Labs'
    author_email = 'jason@jtklabs.dev'
    base_url = 'quotes'
    min_version = '4.6.0'

    def ready(self):
        super().ready()
        from . import signals  # noqa: F401


config = QuotesConfig
