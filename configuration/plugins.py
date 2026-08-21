# Plugins are installed into the image via Dockerfile-Plugins:
# - PyPI plugins from plugin_requirements.txt
# - our own plugins from plugins/
# Keep this list in sync with both.

from os import environ


def _secret(name: str, env_var: str) -> str:
    """A credential from the environment, or from a Docker secret file.

    /run/secrets/<name> is the Compose-secrets location; it keeps the value
    out of `docker inspect` and the process environment, which is the better
    home for an API secret. The environment variable still works so the
    existing prod.env flow keeps working unchanged. Whichever is set wins.
    """
    try:
        with open('/run/secrets/' + name, encoding='utf-8') as handle:
            value = handle.readline().strip()
            if value:
                return value
    except OSError:
        pass
    return environ.get(env_var, '')


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


PLUGINS = [
    "netbox_quotes",
    "netbox_refresh",
    "netbox_discovery",
    "netbox_compliance",
]

PLUGINS_CONFIG = {
    "netbox_refresh": {
        # Cisco Support API credentials for the EoX sync. Kept out of this
        # file so nothing secret is committed: from CISCO_CLIENT_ID /
        # CISCO_CLIENT_SECRET in the container environment (prod.env on the
        # data disk), or from Docker secret files /run/secrets/cisco_client_id
        # and /run/secrets/cisco_client_secret. When both are set, the sync is
        # scheduled automatically on the worker (see the interval below).
        "cisco_client_id": _secret("cisco_client_id", "CISCO_CLIENT_ID"),
        "cisco_client_secret": _secret("cisco_client_secret", "CISCO_CLIENT_SECRET"),
        # Minutes between automatic EoX syncs. 10080 is weekly; 0 disables the
        # schedule (the Sync button still works). Ignored when no credentials
        # are configured.
        "cisco_sync_interval_minutes": _int(environ.get("CISCO_SYNC_INTERVAL_MINUTES"), 10080),
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
