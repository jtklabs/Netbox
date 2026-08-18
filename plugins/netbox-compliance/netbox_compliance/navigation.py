from netbox.plugins import PluginMenu, PluginMenuButton, PluginMenuItem

# Neither model gets a CSV importer. A standard's expected entries and its four
# scope relations do not round-trip through a flat CSV in any way that would not
# mislead, and a bulk-imported *result* is a claim about what a device is doing
# that nobody observed — which is the one thing this plugin must not make easy.
standards = PluginMenuItem(
    link='plugins:netbox_compliance:configstandard_list',
    link_text='Config Standards',
    permissions=['netbox_compliance.view_configstandard'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_compliance:configstandard_add',
            title='Add',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_compliance.add_configstandard'],
        ),
    ),
)

results = PluginMenuItem(
    link='plugins:netbox_compliance:configcompliance_list',
    link_text='Device Results',
    permissions=['netbox_compliance.view_configcompliance'],
    buttons=(
        PluginMenuButton(
            link='plugins:netbox_compliance:configcompliance_add',
            title='Add',
            icon_class='mdi mdi-plus-thick',
            permissions=['netbox_compliance.add_configcompliance'],
        ),
    ),
)

report = PluginMenuItem(
    link='plugins:netbox_compliance:compliance_report',
    link_text='Compliance Report',
    permissions=['netbox_compliance.view_configcompliance'],
)

menu = PluginMenu(
    label='Config Compliance',
    groups=(
        ('Configuration', (standards, results, report)),
    ),
    icon_class='mdi mdi-clipboard-check-outline',
)
