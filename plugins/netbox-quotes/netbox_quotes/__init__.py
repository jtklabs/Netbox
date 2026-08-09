from netbox.plugins import PluginConfig


class QuotesConfig(PluginConfig):
    name = 'netbox_quotes'
    verbose_name = 'Support Management'
    description = 'Vendor support-renewal quotes with serial-matched device assignment'
    version = '0.1.0'
    author = 'Nova Team'
    author_email = 'noreply@example.com'
    base_url = 'quotes'
    # Pointed at explicitly rather than left to the default ('graphql.schema'),
    # which resolves the attribute `schema` on the PACKAGE netbox_quotes.graphql
    # — where the submodule of the same name shadows the list depending on import
    # order. This form is unambiguous: import_string splits on the last dot, so
    # module netbox_quotes.graphql.schema, attribute `schema`. Matches
    # netbox_refresh.
    graphql_schema = 'graphql.schema.schema'
    min_version = '4.6.0'

    def ready(self):
        super().ready()
        from . import signals  # noqa: F401


config = QuotesConfig
