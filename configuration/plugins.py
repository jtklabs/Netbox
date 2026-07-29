# Plugins are installed into the image via Dockerfile-Plugins:
# - PyPI plugins from plugin_requirements.txt
# - our own plugins from plugins/
# Keep this list in sync with both.

PLUGINS = [
    "netbox_lifecycle",
    "netbox_quotes",
]

PLUGINS_CONFIG = {}
