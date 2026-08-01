# Plugins are installed into the image via Dockerfile-Plugins:
# - PyPI plugins from plugin_requirements.txt
# - our own plugins from plugins/
# Keep this list in sync with both.

from os import environ

PLUGINS = [
    "netbox_quotes",
    "netbox_refresh",
    "netbox_diode_plugin",
]

PLUGINS_CONFIG = {
    "netbox_refresh": {
        # Cisco Support API credentials for the EoX sync. Kept in the
        # environment rather than in this file so nothing secret is committed.
        "cisco_client_id": environ.get("CISCO_CLIENT_ID", ""),
        "cisco_client_secret": environ.get("CISCO_CLIENT_SECRET", ""),
    },
    "netbox_diode_plugin": {
        # gRPC target the plugin (and its derived auth URL) uses to reach Diode.
        "diode_target_override": environ.get(
            "DIODE_GRPC_TARGET", "grpc://diode-nginx:80/diode"
        ),
        # OAuth2 client the plugin uses against diode-auth; generated into .env
        # and discovery/oauth2/client/client-credentials.json by init-dev-env.sh.
        "netbox_to_diode_client_secret": environ.get(
            "NETBOX_TO_DIODE_CLIENT_SECRET", ""
        ),
    },
}
