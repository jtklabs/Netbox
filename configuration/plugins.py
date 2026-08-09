# Plugins are installed into the image via Dockerfile-Plugins:
# - PyPI plugins from plugin_requirements.txt
# - our own plugins from plugins/
# Keep this list in sync with both.

from os import environ

PLUGINS = [
    "netbox_quotes",
    "netbox_refresh",
]

PLUGINS_CONFIG = {
    "netbox_refresh": {
        # Cisco Support API credentials for the EoX sync. Kept in the
        # environment rather than in this file so nothing secret is committed.
        "cisco_client_id": environ.get("CISCO_CLIENT_ID", ""),
        "cisco_client_secret": environ.get("CISCO_CLIENT_SECRET", ""),
    },
}
