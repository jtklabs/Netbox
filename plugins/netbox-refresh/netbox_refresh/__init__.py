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
        # Render dates in tables the way the rest of NetBox renders them, i.e.
        # honouring the locale. Off by default because it patches a NetBox
        # internal; see ready().
        'us_dates_in_tables': False,
    }

    def ready(self):
        super().ready()
        if self.default_settings and _setting_enabled('us_dates_in_tables'):
            _localise_table_dates()


def _setting_enabled(name):
    from django.conf import settings

    configured = settings.PLUGINS_CONFIG.get('netbox_refresh', {})
    return bool(configured.get(name, RefreshConfig.default_settings[name]))


def _localise_table_dates():
    """Make table columns respect the locale like every other date does.

    NetBox's own DateColumn returns value.isoformat() outright, so a list view
    shows 2027-12-31 whatever the locale says, while the detail page for the
    same record shows 12/31/2027. Setting US dates without this gets you half
    an answer, and the list is where anyone actually reads end-of-life dates.

    This patches a NetBox internal, which is worth being uncomfortable about:
    it is a five-line render method that has been stable for a long time, but
    an upgrade could change it. So it is opt-in, it checks the shape it
    expects before touching anything, and it complains rather than failing
    silently or crashing the app on start.
    """
    import logging

    logger = logging.getLogger('netbox_refresh')
    try:
        from django.utils.formats import date_format
        from netbox.tables import columns
    except ImportError as exc:
        logger.warning('us_dates_in_tables: %s — leaving table dates alone', exc)
        return

    patched = []

    date_column = getattr(columns, 'DateColumn', None)
    if date_column is not None and hasattr(date_column, 'render'):
        def render_date(self, value):
            # `value` alone is left as ISO: that is what CSV export and
            # copy-to-clipboard use, where an unambiguous date matters more
            # than a familiar one.
            return date_format(value) if value else None

        date_column.render = render_date
        patched.append('DateColumn')

    # "Last updated" and friends are a different class, and a list showing
    # 12/31/2027 beside 2026-08-11 09:14:22 is not what anybody meant by US
    # dates. Its timezone conversion is kept: it is the reason the column
    # exists, and dropping it would show UTC to everyone.
    datetime_column = getattr(columns, 'DateTimeColumn', None)
    if datetime_column is not None and hasattr(datetime_column, 'render'):
        import zoneinfo

        from django.conf import settings as django_settings

        def render_datetime(self, value):
            if not value:
                return None
            local = value.astimezone(zoneinfo.ZoneInfo(django_settings.TIME_ZONE))
            return date_format(local, 'DATETIME_FORMAT')

        datetime_column.render = render_datetime
        patched.append('DateTimeColumn')

    if not patched:
        logger.warning(
            'us_dates_in_tables: netbox.tables.columns is not the shape this '
            'expects, so table dates are unchanged'
        )
        return
    logger.info('us_dates_in_tables: %s now follow the locale', ', '.join(patched))


config = RefreshConfig
