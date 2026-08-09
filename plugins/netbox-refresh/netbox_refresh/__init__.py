from netbox.plugins import PluginConfig


class RefreshConfig(PluginConfig):
    name = 'netbox_refresh'
    verbose_name = 'Hardware Lifecycle'
    description = 'End-of-life dates, replacement models and refresh cost reporting'
    version = '0.1.0'
    author = 'Nova Team'
    author_email = 'noreply@example.com'
    base_url = 'refresh'
    min_version = '4.6.0'
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
    }


config = RefreshConfig
