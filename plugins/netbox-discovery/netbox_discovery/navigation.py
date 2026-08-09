from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

onboarding = PluginMenuItem(
    link='plugins:netbox_discovery:onboardingrequest_list',
    link_text='Onboarding Requests',
    permissions=['netbox_discovery.view_onboardingrequest'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_discovery:onboardingrequest_add',
            title='Onboard a device',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_discovery.add_onboardingrequest'],
        ),
        PluginMenuButton(
            link='plugins:netbox_discovery:onboardingrequest_bulk_import',
            title='Import',
            icon_class='mdi mdi-upload',
            permissions=['netbox_discovery.add_onboardingrequest'],
        ),
    ),
)

# Pollers register themselves on first check-in, so there is no Add button:
# creating one by hand suggests it does something, and it does not.
pollers = PluginMenuItem(
    link='plugins:netbox_discovery:discoverypoller_list',
    link_text='Pollers',
    permissions=['netbox_discovery.view_discoverypoller'],
)

menu = PluginMenu(
    label='Discovery',
    groups=(('Onboarding', (onboarding, pollers)),),
    icon_class='mdi mdi-radar',
)
