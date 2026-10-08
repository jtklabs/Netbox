from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem
from django.conf import settings

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
    label='Device Operations',
    groups=(
        ('Onboarding', (onboarding, pollers, rules, stripped_domains)),
        ('Changes', (replacements, issues)),
        ('Software and Standards', (
            *((PluginMenuItem(
                link='plugins:netbox_compliance:configstandard_list', link_text='Standards',
                permissions=['netbox_compliance.view_configstandard'],
            ),) if 'netbox_compliance' in settings.PLUGINS else ()),
            PluginMenuItem(
                link='plugins:netbox_discovery:jobprofile_list', link_text='Job Profiles',
                permissions=['netbox_discovery.view_jobprofile'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:jobprofile_add', title='Add a profile',
                    icon_class='mdi mdi-plus-thick', permissions=['netbox_discovery.add_jobprofile'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:platformprofile_list', link_text='Platform Profiles',
                permissions=['netbox_discovery.view_platformprofile'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:platformprofile_add', title='Assign a standards profile to a platform',
                    icon_class='mdi mdi-plus-thick', permissions=['netbox_discovery.add_platformprofile'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:devicetypeprofile_list', link_text='Model Profiles',
                permissions=['netbox_discovery.view_devicetypeprofile'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:devicetypeprofile_add', title='Assign profiles to a model',
                    icon_class='mdi mdi-plus-thick', permissions=['netbox_discovery.add_devicetypeprofile'],
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
            PluginMenuItem(
                link='plugins:netbox_discovery:prestagepolicy_list', link_text='Automatic Image Staging',
                permissions=['netbox_discovery.view_prestagepolicy'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:prestagepolicy_add', title='Add an image staging policy',
                    icon_class='mdi mdi-plus-thick',
                    permissions=['netbox_discovery.add_prestagepolicy', 'netbox_discovery.apply_upgradejob'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:auditschedule_list', link_text='Audit Schedules',
                permissions=['netbox_discovery.view_auditschedule'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:auditschedule_add', title='Schedule standards audit or remediation',
                    icon_class='mdi mdi-calendar-plus', permissions=['netbox_discovery.add_auditschedule'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:upgradejob_list', link_text='Upgrade Jobs',
                permissions=['netbox_discovery.view_upgradejob'],
                buttons=(PluginMenuButton(
                    link='plugins:netbox_discovery:upgradejob_add', title='Schedule upgrade',
                    icon_class='mdi mdi-calendar-plus', permissions=['netbox_discovery.add_upgradejob'],
                ),),
            ),
            PluginMenuItem(
                link='plugins:netbox_discovery:standardsjob_list', link_text='Standards Jobs',
                permissions=['netbox_discovery.view_upgradejob'],
            ),
        )),
    ),
    icon_class='mdi mdi-server-network',
)
