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

# What a device does not report, said once instead of typed at every review.
rules = PluginMenuItem(
    link='plugins:netbox_discovery:discoveryrule_list',
    link_text='Rules',
    permissions=['netbox_discovery.view_discoveryrule'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_discovery:discoveryrule_add',
            title='Add a rule',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_discovery.add_discoveryrule'],
        ),
    ),
)

# Which part of a reported hostname is the domain, said out loud.
stripped_domains = PluginMenuItem(
    link='plugins:netbox_discovery:strippeddomain_list',
    link_text='Stripped Domains',
    permissions=['netbox_discovery.view_strippeddomain'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_discovery:strippeddomain_add',
            title='Add a domain',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_discovery.add_strippeddomain'],
        ),
        PluginMenuButton(
            link='plugins:netbox_discovery:strippeddomain_bulk_import',
            title='Import',
            icon_class='mdi mdi-upload',
            permissions=['netbox_discovery.add_strippeddomain'],
        ),
    ),
)

menu = PluginMenu(
    label='Discovery',
    groups=(
        ('Onboarding', (onboarding, pollers, rules, stripped_domains)),
        ('Changes', (replacements, issues)),
        ('Software', (
            PluginMenuItem(
                link='plugins:netbox_discovery:upgradejob_list', link_text='Upgrade Jobs',
                permissions=['netbox_discovery.view_upgradejob'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:upgradejob_add', title='Schedule upgrades',
                    icon_class='mdi mdi-calendar-plus',
                    permissions=['netbox_discovery.add_upgradejob'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:upgradegroup_list', link_text='Redundancy Groups',
                permissions=['netbox_discovery.view_upgradegroup'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:upgradegroup_add', title='Add a group',
                    icon_class='mdi mdi-plus-thick',
                    permissions=['netbox_discovery.add_upgradegroup'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:upgradedependency_list', link_text='Upgrade Dependencies',
                permissions=['netbox_discovery.view_upgradedependency'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:upgradedependency_add', title='Add a dependency',
                    icon_class='mdi mdi-plus-thick',
                    permissions=['netbox_discovery.add_upgradedependency'],
                ),),
            ),
        )),
    ),
    icon_class='mdi mdi-radar',
)
