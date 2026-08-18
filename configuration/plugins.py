# Plugins are installed into the image via Dockerfile-Plugins:
# - PyPI plugins from plugin_requirements.txt
# - our own plugins from plugins/
# Keep this list in sync with both.

from os import environ

PLUGINS = [
    "netbox_quotes",
    "netbox_refresh",
    "netbox_discovery",
    "netbox_compliance",
]

PLUGINS_CONFIG = {
    "netbox_refresh": {
        # Cisco Support API credentials for the EoX sync. Kept in the
        # environment rather than in this file so nothing secret is committed.
        "cisco_client_id": environ.get("CISCO_CLIENT_ID", ""),
        "cisco_client_secret": environ.get("CISCO_CLIENT_SECRET", ""),
        # Base URL of the internal HTTP server holding software images. Set it
        # and a software version needs only a filename to get a download link;
        # the image server can then move without touching any record. Plain
        # http is expected — these are download links, never page resources.
        # e.g. IMAGE_BASE_URL=http://images.internal/network
        "image_base_url": environ.get("IMAGE_BASE_URL", ""),
        # Dates in list views. NetBox's table column renders ISO outright and
        # ignores the locale, so without this a list shows 2027-12-31 while
        # the detail page for the same record shows 12/31/2027. Enabling it
        # patches that column — see _localise_table_dates() for why that is
        # opt-in rather than simply done.
        "us_dates_in_tables": True,
    },
    "netbox_compliance": {
        # A configuration check older than this many days is shown as stale.
        # Shorter than the software side's 90: running config changes when
        # somebody types on a switch, which is a different tempo from a code
        # upgrade, so a month-old result is already worth a warning triangle.
        "stale_after_days": 30,
    },
}
