"""Lifecycle plugin — hardware end-of-life and software image compliance.

Naming wart, deliberate: the Python package is `netbox_refresh` and the URL
prefix is `refresh`, but the plugin presents itself in the UI as "Lifecycle".
It began as hardware-refresh reporting and grew a software-compliance half.
Renaming the package would mean fresh migrations, moved template directories
and changed REST API paths (`/api/plugins/refresh/...`) for zero functional
gain, so the mismatch stays. If you came here looking for "netbox_lifecycle",
that was a third-party plugin we removed — see D10 in PROJECT_PLAN.md.

Both halves live in one plugin because they answer one business question — "is
this device current and supported?" — share the per-device exemption pattern,
and belong in one report. Model class names must also be unique across every
installed plugin (NetBox's Owner reverse accessors clash otherwise, which is
why netbox_quotes has QuoteVendor), so a third plugin would triple that
surface for no benefit.
"""

from netbox.plugins import PluginConfig


class RefreshConfig(PluginConfig):
    name = 'netbox_refresh'
    verbose_name = 'Lifecycle'
    description = 'Hardware end-of-life reporting and software image compliance'
    version = '0.2.0'
    author = 'Nova Team'
    author_email = 'noreply@example.com'
    base_url = 'refresh'
    min_version = '4.6.0'
    # Pointed at explicitly rather than left to the default ('graphql.schema'),
    # which resolves the attribute `schema` on the package netbox_refresh.graphql
    # — where the submodule of the same name shadows it depending on import
    # order. This form is unambiguous: module netbox_refresh.graphql.schema,
    # attribute `schema`.
    graphql_schema = 'graphql.schema.schema'
    default_settings = {
        # Manufacturer names whose part numbers are Cisco PIDs, used by the
        # EoX sync to decide which device/module types to look up.
        'cisco_manufacturers': ['Cisco'],
        # Cisco Support API credentials. Left empty so they can come from the
        # environment instead of being written into configuration.
        'cisco_client_id': '',
        'cisco_client_secret': '',
        # Card placement on device/device-type pages.
        'lifecycle_card_position': 'right_page',
        # Base URL of the internal HTTP file server that holds code images.
        # When set, a version with an image filename but no explicit URL gets a
        # download link derived from this (e.g. 'http://images.internal/net/').
        # Plain http is expected and fine — see SoftwareVersion.download_url.
        'image_base_url': '',
        # A running-version reading older than this many days is shown as stale.
        # Freshness is deliberately separate from the compliance state: a device
        # can be compliant against a six-month-old reading, and the report should
        # say so rather than render it green and confident.
        'stale_after_days': 90,
    }


config = RefreshConfig
