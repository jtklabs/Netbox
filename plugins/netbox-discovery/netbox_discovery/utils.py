from django.conf import settings


def plugin_setting(name):
    """Read one of this plugin's settings, falling back to its default.

    PLUGINS_CONFIG only carries keys an operator overrode, so reading it
    directly returns None for everything left at its default.
    """
    from netbox_discovery import DiscoveryConfig

    configured = settings.PLUGINS_CONFIG.get('netbox_discovery', {})
    if name in configured:
        return configured[name]
    return DiscoveryConfig.default_settings[name]
