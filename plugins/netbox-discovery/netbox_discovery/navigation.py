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

# Serial changes are their own thing, not an onboarding concern: they come out
# of the routine rescan, and the people who care are the ones reconciling
# support contracts.
replacements = PluginMenuItem(
    link='plugins:netbox_discovery:hardwarereplacement_list',
    link_text='Hardware Replacements',
    permissions=['netbox_discovery.view_hardwarereplacement'],
)

issues = PluginMenuItem(
    link='plugins:netbox_discovery:discoveryissue_list',
    link_text='Issues',
    permissions=['netbox_discovery.view_discoveryissue'],
)

menu = PluginMenu(
    label='Discovery',
    groups=(
        ('Onboarding', (onboarding, pollers)),
        ('Changes', (replacements, issues)),
    ),
    icon_class='mdi mdi-radar',
)
