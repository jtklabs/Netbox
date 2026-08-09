# NetBox resolves the plugin's GraphQL schema as the attribute `schema` on THIS
# package (netbox/plugins/__init__.py: DEFAULT_RESOURCE_PATHS['graphql_schema']
# = 'graphql.schema', resolved with import_string, which splits off the last
# component and does getattr on the module). Importing the name here rebinds it
# from the `schema` SUBMODULE to the list the loader expects; without this line
# the loader hands NetBox a module object and `.extend()` fails.
from netbox_quotes.graphql.schema import schema  # noqa: F401
